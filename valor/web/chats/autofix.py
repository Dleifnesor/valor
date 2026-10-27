"""Failed builds: the assistant fixes them.

A build that fails (or whose verification fails) goes to a range chat in Code mode of the person who started it. In
the mode 'auto' the assistant's actions run without approval cards and VALOR rebuilds the range when it is done - up
to FIX_ROUNDS times, never when the plan would replace or remove a VM. A provider that cannot answer hands over to
the next one, and a fix turn that ends early (out of steps, VALOR restarted, no provider answered) is started again,
so a fix never waits for someone to type "continue".
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime

from ... import drafts, jobs, ops, state
from ...errors import ValorError
from ...plan import make_plan
from ...pve import PVE
from ...spec import normalize, parse_spec
from ...validate import validate_cluster
from .. import db
from ..builder import ai_settings
from ..ranges import _load
from . import turn

log = logging.getLogger("valor.chats")

FIX_WINDOW = 1800                 # seconds after a build failed in which VALOR starts the assistant on it by itself
NOT_FIXABLE = {"interrupted", "runner_died"}      # the worker stopped (reboot, crash): run it again, nothing to fix
JOB_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
_fix_seen: set[str] = set()



def job_range(job: dict) -> str:
    t = job.get("target") or {}
    return t.get("range") or str(t.get("spec", "")).removesuffix(".yaml")



def _verify_failures(cfg, job: dict) -> list[str]:
    """The tests and baseline controls that failed in the build's verification."""
    v = state.load(cfg, job_range(job), "verify") or {}
    if v.get("job") != job["id"]:
        return []
    out = [f"- test '{t.get('name')}': {t.get('from')} -> {t.get('to')} {t.get('proto')}"
           f"{'/' + str(t['port']) if t.get('port') else ''}, expected {t.get('expect')}, observed {t.get('observed')}"
           f" ({t.get('detail', '')})" for t in v.get("tests") or [] if not t.get("pass")]
    out += [f"- baseline on {host}: {r.get('control')} {r.get('title', '')} is {r.get('after')}"
            for host, rows in (v.get("baseline") or {}).items() for r in rows if r.get("after") != "pass"]
    return out



FINISH = {
    "prepare": "Look inside the affected VMs first, then fix the cause where it is: in the VMs (commands that change "
               "something wait for my approval), in the spec with update_spec when the spec is wrong, or both. The "
               "next apply runs the failed step and the verification again, so make sure they will pass then. Finish "
               "with what you found, what you changed, and whether I should re-apply.",
    "fix": "Look inside the affected VMs first, then fix the cause where it is: in the VMs (your actions run without "
           "approval cards), in the spec with update_spec when the spec is wrong, or both. The next apply runs the "
           "failed step and the verification again, so make sure they will pass then. Finish with what you found, "
           "what you changed, and whether I should re-apply.",
    "auto": "Your actions run without approval cards, and VALOR rebuilds and verifies the range as soon as you finish "
            "(you will hear here if that fails). Look inside the affected VMs first, then fix the cause where it is: "
            "in the VMs, in the spec with update_spec when the spec is wrong, or both - and record every lasting "
            "change in the spec, because a live change alone is lost when a VM is rebuilt. If the failure was "
            "transient (the guest agent restarted, a download timed out) and the VMs are fine now, say so and "
            "finish: the rebuild runs the step again. Make the range work as its spec describes; never make it pass "
            "by dropping tests, policy rules, roles or hardening unless the spec itself asks for something that "
            "cannot work. Finish with what you found and what you changed.",
}


def fix_prompt(job: dict, failures: list[str] | None = None, mode: str = "prepare") -> str:
    """What the assistant is told about a failed build (sent as the chat's first message)."""
    r = job.get("result") or {}
    lines = [f"[VALOR] The build of range {job_range(job)} failed (job {job['id']}). Find the cause and fix it so "
             "that the next apply succeeds."]
    if r.get("error") or not failures:
        lines.append(f"Error: {r.get('error', 'error')}: {r.get('message', '')}")
    if failures:
        lines.append("The build finished, but its verification failed:\n" + "\n".join(failures[:40]))
    if r.get("step"):
        lines.append(f"Failed step: {r['step']}" + (f" on {r['host']}" if r.get("host") else ""))
    if r.get("details"):
        lines.append("Output from the host:\n" + str(r["details"])[-3000:])
    if r.get("hint"):
        lines.append(f"VALOR's hint: {r['hint']}")
    if r.get("completed_steps"):
        lines.append("Steps that completed before it: " + ", ".join(map(str, r["completed_steps"][-15:])))
    lines.append(FINISH.get(mode, FINISH["prepare"]))
    return "\n".join(lines)[:7900]



FIX_ROUNDS = 5                     # automatic fix-and-rebuild rounds for one failed build
REVIVES = 2                        # restarts of a fix turn that ended early, per round; then VALOR rebuilds anyway
REVIVE_WAIT = 15                   # s before restarting it - PROVIDER_WAIT when no provider could answer
PROVIDER_WAIT = 300
PROVIDER_FAILED = {"ai_context_full", "ai_unreachable", "ai_auth", "ai_credits", "ai_rate_limited", "ai_error",
                   "ai_no_tools", "ai_truncated", "ai_not_configured"}      # another provider may well manage
RISKY = ("replace", "remove")      # plan actions an automatic rebuild leaves to the person (VMs lost or rebuilt)



def _origin(origin: str) -> dict:
    """Who started a build: web:<user>, assistant:<user>:<chat> (the rebuild after an automatic fix), rerun:<origin>
    (VALOR ran a build again after the worker stopped), cli / mcp."""
    if origin.startswith("rerun:"):
        return {**_origin(origin[len("rerun:"):]), "rerun": True}
    kind, _, rest = origin.partition(":")
    if kind == "web":
        return {"user": rest}
    if kind == "assistant":
        user, _, chat = rest.rpartition(":")
        return {"user": user, "chat": int(chat) if chat.isdigit() else None}
    return {}



def _owner(conn, origin: str) -> str | None:
    """Whose chat fixes a build: the operator who started it, else the first administrator (builds from the CLI/MCP)."""
    user = _origin(origin).get("user")
    if user:
        u = conn.execute("SELECT username, role, disabled FROM users WHERE username = ?", (user,)).fetchone()
        if u is not None and not u["disabled"] and u["role"] in ("operator", "admin"):
            return u["username"]
    a = conn.execute("SELECT username FROM users WHERE role = 'admin' AND disabled = 0 ORDER BY id LIMIT 1").fetchone()
    return a["username"] if a else None



def _signature(cfg, job: dict) -> str:
    """How a build failed - the same signature after a fix means the fix did not help."""
    r = job.get("result") or {}
    return json.dumps([r.get("error"), r.get("step"), r.get("host")] if r.get("error") else
                      sorted(_verify_failures(cfg, job)))



def _over_budget(conn, store: dict, username: str) -> bool:
    """The person whose chat fixes a build used their daily AI budget (the fix waits until there is some again)."""
    limit = int(store["daily_tokens_per_user"] or 0)
    return bool(limit) and conn.execute("SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM ai_usage WHERE "
                                        "username = ? AND ts > ?", (username, time.time() - 86400)).fetchone()[0] >= limit



def _ask(app, conn, chat_id: int, username: str, text: str) -> int:
    """VALOR's message in a fix chat, and the assistant's turn on it (Code mode)."""
    return turn.start(app, conn, chat_id, username, "code", text)[1]



def _say(conn, chat_id: int, text: str) -> None:
    """A note from VALOR at the end of a fix chat (no model turn)."""
    now = time.time()
    conn.execute("INSERT INTO chat_messages(chat_id, ts, role, mode, status, content, meta) VALUES (?, ?, 'assistant', "
                 "'code', 'done', ?, ?)", (chat_id, now, text, json.dumps({"steps": [], "valor": True})))
    conn.execute("UPDATE chats SET updated = ? WHERE id = ?", (now, chat_id))



def _set_state(conn, chat_id: int, **fields) -> dict:
    row = conn.execute("SELECT state FROM chats WHERE id = ?", (chat_id,)).fetchone()
    st = {**json.loads(row["state"] or "{}"), **fields}
    conn.execute("UPDATE chats SET state = ? WHERE id = ?", (json.dumps(st), chat_id))
    return st



def start_fix(app, conn, job: dict, username: str, auto: bool, provider: str | None = None,
              mode: str = "prepare") -> int:
    """The range chat (Code mode) in which the assistant works on a failed build: one per build and person. In the
    modes 'auto' and 'fix' its actions run without approval cards; 'auto' also rebuilds when it is done."""
    name = job_range(job)
    for r in conn.execute("SELECT id, state FROM chats WHERE owner = ? AND kind = 'range' AND range = ?",
                          (username, name)).fetchall():
        if json.loads(r["state"] or "{}").get("job") == job["id"]:
            return r["id"]
    now = time.time()
    res = job.get("result") or {}
    failures = [] if res.get("error") else _verify_failures(app.state.cfg, job)
    title = f"Fix: {res.get('message') or res.get('error') or ''}" if not failures else \
        f"Fix: verification failed ({len(failures)} check{'' if len(failures) == 1 else 's'})"
    title = (title if title != "Fix: " else "Fix: failed build")[:120]
    state_ = {"job": job["id"], "fix": mode, "round": 1, "sig": _signature(app.state.cfg, job), "jobs": [job["id"]],
              **({"provider": provider} if provider else {}), **({"allow_all": True} if mode in ("auto", "fix") else {})}
    cid = conn.execute("INSERT INTO chats(owner, kind, range, title, created, updated, state) VALUES "
                       "(?, 'range', ?, ?, ?, ?, ?)", (username, name, title, now, now, json.dumps(state_))).lastrowid
    db.audit(conn, "chat.fix", "ok", username=username, target=name,
             detail={"chat": cid, "job": job["id"], "auto": auto, "mode": mode})
    _ask(app, conn, cid, username, fix_prompt(job, failures, mode))
    return cid



def continue_fix(app, conn, chat, job: dict) -> int | None:
    """The automatic rebuild failed: the same chat goes on (round 2, 3, ...). A rebuild that fails the same way as the
    one before gets one more round to try another way; the same failure a third time ends it."""
    cfg, cs = app.state.cfg, json.loads(chat["state"] or "{}")
    rnd, sig = int(cs.get("round", 1)) + 1, _signature(cfg, job)
    again = sig == cs.get("sig")
    if rnd > FIX_ROUNDS or (again and cs.get("again")):
        why = "the rebuild failed the same way three times" if again else f"{FIX_ROUNDS} rounds did not fix it"
        _say(conn, chat["id"], f"VALOR stopped fixing this on its own: {why} (job {job['id']}). Read what the assistant "
                               "found above; ask it to try something else, or fix the spec and apply it yourself.")
        _set_state(conn, chat["id"], done="stopped")
        return None
    _set_state(conn, chat["id"], round=rnd, sig=sig, again=again, revived=0, jobs=[*cs.get("jobs", []), job["id"]])
    res = job.get("result") or {}
    how = ("the rebuild failed the same way again, so your last fix did not help. Look again and fix it another way."
           if again else "the rebuild failed too.")
    _ask(app, conn, chat["id"], chat["owner"], f"[VALOR] Round {rnd} of {FIX_ROUNDS}: {how}\n" +
         fix_prompt(job, [] if res.get("error") else _verify_failures(cfg, job), cs.get("fix", "prepare")))
    return chat["id"]



def revive_stalled(app, conn, store: dict) -> list[int]:
    """Mode 'auto': a fix turn that ended early - out of steps, VALOR restarted under it, no provider answered - is
    started again (REVIVES times per round). After that VALOR rebuilds anyway: a transient failure passes then, and
    one that fails again comes back as the next round."""
    out = []
    for chat in conn.execute("SELECT * FROM chats WHERE kind = 'range' AND updated > ?",
                             (time.time() - 6 * 3600,)).fetchall():
        cs = json.loads(chat["state"] or "{}")
        if cs.get("fix") != "auto" or "job" not in cs or cs.get("done"):
            continue
        rows = conn.execute("SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY id DESC LIMIT 2",
                            (chat["id"],)).fetchall()
        if len(rows) < 2 or rows[0]["role"] != "assistant" or not str(rows[1]["content"]).startswith("[VALOR]"):
            continue                             # only VALOR's own turns: a person who wrote in the chat is there
        turn.mark_interrupted(conn, rows[0])     # a turn still marked running whose thread is gone
        last = conn.execute("SELECT status, meta FROM chat_messages WHERE id = ?", (rows[0]["id"],)).fetchone()
        if last["status"] != "failed":
            continue
        err = json.loads(last["meta"] or "{}").get("error") or {}
        wait = PROVIDER_WAIT if err.get("code") in PROVIDER_FAILED else REVIVE_WAIT
        if time.time() - chat["updated"] < wait or _over_budget(conn, store, chat["owner"]):
            continue                             # over the daily AI budget: it waits until there is some again
        n = int(cs.get("revived", 0))
        if n < REVIVES:
            _set_state(conn, chat["id"], revived=n + 1, tried=[])         # every provider gets another chance
            _ask(app, conn, chat["id"], chat["owner"],
                 f"[VALOR] Your last answer ended early ({err.get('message', 'error')}); what it did is above. Go on "
                 "from there instead of starting over: make the fix now, record it in the spec, and finish - VALOR "
                 "rebuilds the range as soon as you do.")
        else:
            res = rebuild(app, conn, chat, chat["owner"])
            _say(conn, chat["id"], f"The assistant did not finish after {REVIVES + 1} tries. {res['note']}")
            if not res.get("job"):
                _set_state(conn, chat["id"], done="stopped")
        out.append(chat["id"])
    return out



def rebuild(app, conn, chat, username: str) -> dict:
    """Mode 'auto': build the range again once the assistant is done - its draft when it recorded one. A plan that
    would replace or remove a VM waits for the person."""
    cfg, name = app.state.cfg, chat["range"]
    d = drafts.load(cfg, name)
    try:
        text = d[0] if d else _load(cfg, name)[1]
        spec = normalize(parse_spec(text), cfg.default_os)
        pve = PVE(cfg)
        v = validate_cluster(pve, spec)
        if not v["ok"]:
            return {"note": "VALOR did not rebuild: the spec has problems - " +
                            "; ".join(f"{e['location']}: {e['message']}" for e in v["errors"])[:600]}
        p = make_plan(pve, spec)
    except ValorError as e:
        return {"note": f"VALOR did not rebuild: {e.message}"}
    risky = [f"{a['action']} {a['host']}" for a in p["actions"] if a["action"] in RISKY]
    if risky:
        return {"note": f"VALOR did not rebuild on its own: the plan would {', '.join(risky)}. Review the plan on the "
                        "range page and apply it there."}
    if d:                                    # the assistant's draft becomes the range's spec, as an approved apply does
        path = cfg.ranges_dir / f"{name}.yaml"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
        drafts.discard(cfg, name)
        db.audit(conn, "range.spec.save", "ok", username=username, target=name,
                 detail={"from": "draft", "by": "assistant", "chat": chat["id"]})
    job = ops.start(cfg, "apply", {"spec": f"{name}.yaml", "verify": True}, f"assistant:{username}:{chat['id']}",
                    background=True)
    _set_state(conn, chat["id"], rebuild=job["job"])
    db.audit(conn, "range.apply", "ok", username=username, target=name,
             detail={"job": job["job"], "by": "assistant", "chat": chat["id"], "changes": p["summary"]})
    changes = ", ".join(f"{n} {k}" for k, n in p["summary"].items() if n and k != "keep") or "no changes"
    return {"job": job["job"], "note": f"VALOR rebuilds the range now ({changes})."}



def fix_failed_builds(app) -> list[int]:
    """Builds that ended in the last FIX_WINDOW s, while 'fix failed builds' is on and a provider is connected:
    a failed one goes to the assistant (a chat of the person who started it, or the first admin for CLI/MCP builds;
    a failed automatic rebuild continues its chat); one the worker did not finish runs once more; a successful
    automatic rebuild closes its chat. Fix turns that ended early are started again (revive_stalled)."""
    cfg, out = app.state.cfg, []
    conn = db.connect(app.state.wcfg.db)
    try:
        store = ai_settings(conn, app.state.box)
        if not store["providers"] or not store["fix_failed_builds"]:
            return out
        for j in jobs.list_jobs(cfg, 20):
            if j.get("kind") != "apply" or j.get("state") not in ("failed", "succeeded") or j["id"] in _fix_seen:
                continue
            _fix_seen.add(j["id"])
            try:
                finished = datetime.strptime(str(j.get("finished")), "%Y-%m-%dT%H:%M:%S%z").timestamp()
            except ValueError:
                continue
            if time.time() - finished > FIX_WINDOW:
                continue
            job = jobs.status(cfg, j["id"], tail=1)
            origin = str(job.get("origin", ""))
            o = _origin(origin)
            chat = conn.execute("SELECT * FROM chats WHERE id = ?", (o["chat"],)).fetchone() if o.get("chat") else None
            if j["state"] == "succeeded":
                if chat is not None:
                    v = ((job.get("result") or {}).get("verify") or {}).get("summary") or {}
                    tests = f" Verification: tests {v.get('tests_passed')}/{v.get('tests_total')}, baseline " \
                            f"{v.get('baseline_passed')}/{v.get('baseline_total')}." if v else ""
                    _say(conn, chat["id"], f"VALOR rebuilt the range: the build succeeded (job {job['id']}).{tests}")
                    _set_state(conn, chat["id"], done="fixed")
                continue
            name = job_range(job)
            if not (cfg.ranges_dir / f"{name}.yaml").exists():
                continue
            if (job.get("result") or {}).get("error") in NOT_FIXABLE:
                if not o.get("rerun"):           # the worker stopped (reboot, crash): run the same build once more
                    r = ops.start(cfg, "apply", job["target"], f"rerun:{origin}", background=True)
                    db.audit(conn, "range.apply", "ok", username=o.get("user", ""), target=name,
                             detail={"job": r["job"], "rerun_of": job["id"], "by": "valor"})
                continue
            owner = chat["owner"] if chat is not None else _owner(conn, origin)
            if owner is None or _over_budget(conn, store, owner):
                continue
            cid = continue_fix(app, conn, chat, job) if chat is not None else \
                start_fix(app, conn, job, owner, True, store["fix_provider"] or None, store["fix_mode"])
            if cid:
                out.append(cid)
        out += [c for c in revive_stalled(app, conn, store) if c not in out]
    finally:
        conn.close()
    return out



def fallback(app, conn, chat, st: dict, e: ValorError) -> dict | None:
    """A fix chat whose provider could not answer (context too small, unreachable, credits, ...) moves on to the
    next one: the failed-builds provider, the default, then the others."""
    cs = json.loads(chat["state"] or "{}")
    if "job" not in cs or e.code not in PROVIDER_FAILED:
        return None
    store = ai_settings(conn, app.state.box)
    first = store["fix_provider"] or store["default"]
    order = sorted(store["providers"], key=lambda p: (p["id"] != first, p["id"] != store["default"]))
    tried = {*(cs.get("tried") or []), st.get("id")}
    nxt = next((p for p in order if p["id"] not in tried), None)
    if nxt is not None:
        _set_state(conn, chat["id"], provider=nxt["id"], tried=sorted(t for t in tried if t))
    return nxt



def watch_failed_builds(app, stop: threading.Event, every: float = 10.0) -> None:
    """The web service's thread that hands failed builds to the assistant (builds run in the worker service)."""
    while not stop.wait(every):
        try:
            fix_failed_builds(app)
        except Exception:                            # pragma: no cover - logged, the next round tries again
            log.exception("handing failed builds to the assistant")
