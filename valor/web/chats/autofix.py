# *grabs a tiny wrench and rushes over* OwO valor/web/chats/autofix.py: when a build FAILS, the AI assistant fixes
# it! ^w^
# A failed build (or failed verification) opens a Code-mode range chat for the person who started it, with the
# error explained to the assistant. In mode "auto" the assistant's actions run without approval cards, and VALOR
# rebuilds the range when it's done, up to FIX_ROUNDS times, but NEVER when the plan would replace or remove a VM.
# If a provider can't answer, the next one takes over; a fix turn that ends early is restarted, so nobody ever has
# to type "continue" *wags tail determinedly*
"""Failed builds: the assistant fixes them.

A build that fails (or whose verification fails) goes to a range chat in Code mode of the person who started it. In
the mode 'auto' the assistant's actions run without approval cards and VALOR rebuilds the range when it is done - up
to FIX_ROUNDS times, never when the plan would replace or remove a VM. A provider that cannot answer hands over to
the next one, and a fix turn that ends early (out of steps, VALOR restarted, no provider answered) is started again,
so a fix never waits for someone to type "continue".
"""

# Lazy type hints UwU
from __future__ import annotations

# json (chat state), logging, os (atomic file replace), re (job ids), threading (the watcher), time, datetime
# (parse job end times) >w<
import json
import logging
import os
import re
import threading
import time
from datetime import datetime

# drafts/jobs/ops/state, ValorError, make_plan, PVE, spec parsing, validate_cluster, db, ai_settings, the range
# loader, and turn (starts the assistant's turns) OwO
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

# The logger ^w^
log = logging.getLogger("valor.chats")

# FIX_WINDOW: only builds that ended in the last 30 min are picked up. NOT_FIXABLE: errors meaning "the worker
# stopped" (nothing to fix; just run it again). JOB_ID: the job id format. _fix_seen: jobs already handled UwU
FIX_WINDOW = 1800                 # seconds after a build failed in which VALOR starts the assistant on it by itself
NOT_FIXABLE = {"interrupted", "runner_died"}      # the worker stopped (reboot, crash): run it again, nothing to fix
JOB_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
_fix_seen: set[str] = set()


# job_range: the range a job is about (from "range", or its spec file name) >w<
def job_range(job: dict) -> str:
    target = job.get("target") or {}
    return target.get("range") or str(target.get("spec", "")).removesuffix(".yaml")


# _verify_failures: one line per failed test and failed baseline control in THIS build's verification OwO
def _verify_failures(cfg, job: dict) -> list[str]:
    """The tests and baseline controls that failed in the build's verification."""
    verification = state.load(cfg, job_range(job), "verify") or {}
    if verification.get("job") != job["id"]:
        return []                       # the latest verification is from another build
    out = [f"- test '{test.get('name')}': {test.get('from')} -> {test.get('to')} {test.get('proto')}"
           f"{'/' + str(test['port']) if test.get('port') else ''}, expected {test.get('expect')}, "
           f"observed {test.get('observed')} ({test.get('detail', '')})"
           for test in verification.get("tests") or [] if not test.get("pass")]
    out += [f"- baseline on {host}: {row.get('control')} {row.get('title', '')} is {row.get('after')}"
            for host, rows in (verification.get("baseline") or {}).items() for row in rows if row.get("after") != "pass"]
    return out


# FINISH: the closing instruction for each fix mode ^w^:
#   prepare  look, fix (changes wait for approval), report and say whether to re-apply
#   fix      the same, but actions run without approval cards
#   auto     no cards, and VALOR rebuilds afterwards: record lasting changes in the spec; never "fix" by dropping
#            tests, rules, roles or hardening
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


# fix_prompt: the first message the assistant gets about a failed build: what failed (error or failed checks),
# which step on which host, the host's output (last 3000 chars), VALOR's hint, the steps that completed, then the
# mode's instruction. Capped below the message size limit UwU
def fix_prompt(job: dict, failures: list[str] | None = None, mode: str = "prepare") -> str:
    """What the assistant is told about a failed build (sent as the chat's first message)."""
    result = job.get("result") or {}
    lines = [f"[VALOR] The build of range {job_range(job)} failed (job {job['id']}). Find the cause and fix it so "
             "that the next apply succeeds."]
    if result.get("error") or not failures:
        lines.append(f"Error: {result.get('error', 'error')}: {result.get('message', '')}")
    if failures:
        lines.append("The build finished, but its verification failed:\n" + "\n".join(failures[:40]))
    if result.get("step"):
        lines.append(f"Failed step: {result['step']}" + (f" on {result['host']}" if result.get("host") else ""))
    if result.get("details"):
        lines.append("Output from the host:\n" + str(result["details"])[-3000:])
    if result.get("hint"):
        lines.append(f"VALOR's hint: {result['hint']}")
    if result.get("completed_steps"):
        lines.append("Steps that completed before it: " + ", ".join(map(str, result["completed_steps"][-15:])))
    lines.append(FINISH.get(mode, FINISH["prepare"]))
    return "\n".join(lines)[:7900]


# Fix-loop limits >w<: FIX_ROUNDS (rounds of fix + rebuild), REVIVES (restarts of a turn that ended early, per round),
# REVIVE_WAIT / PROVIDER_WAIT (how long to wait before a restart; longer when the provider itself failed),
# PROVIDER_FAILED (errors where another provider may do better) and RISKY (plan actions only a person may approve)
FIX_ROUNDS = 5                     # automatic fix-and-rebuild rounds for one failed build
REVIVES = 2                        # restarts of a fix turn that ended early, per round; then VALOR rebuilds anyway
REVIVE_WAIT = 15                   # s before restarting it - PROVIDER_WAIT when no provider could answer
PROVIDER_WAIT = 300
PROVIDER_FAILED = {"ai_context_full", "ai_unreachable", "ai_auth", "ai_credits", "ai_rate_limited", "ai_error",
                   "ai_no_tools", "ai_truncated", "ai_not_configured"}      # another provider may well manage
RISKY = ("replace", "remove")      # plan actions an automatic rebuild leaves to the person (VMs lost or rebuilt)


# _origin: decode who started a build: "web:<user>", "assistant:<user>:<chat>" (an automatic rebuild),
# "rerun:<origin>" (VALOR re-ran it after the worker stopped), or cli/mcp OwO
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


# _owner: whose chat fixes a build: the operator who started it (if still active), else the first admin ^w^
def _owner(conn, origin: str) -> str | None:
    """Whose chat fixes a build: the operator who started it, else the first administrator (builds from the CLI/MCP)."""
    user = _origin(origin).get("user")
    if user:
        row = conn.execute("SELECT username, role, disabled FROM users WHERE username = ?", (user,)).fetchone()
        if row is not None and not row["disabled"] and row["role"] in ("operator", "admin"):
            return row["username"]
    admin = conn.execute("SELECT username FROM users WHERE role = 'admin' AND disabled = 0 ORDER BY id LIMIT 1").fetchone()
    return admin["username"] if admin else None


# _signature: HOW a build failed (error + step + host, or the sorted failed checks). The same signature after a fix
# means the fix didn't help UwU
def _signature(cfg, job: dict) -> str:
    """How a build failed - the same signature after a fix means the fix did not help."""
    result = job.get("result") or {}
    return json.dumps([result.get("error"), result.get("step"), result.get("host")] if result.get("error") else
                      sorted(_verify_failures(cfg, job)))


# _over_budget: has this person used their daily AI budget? (then the fix waits) >w<
def _over_budget(conn, store: dict, username: str) -> bool:
    """The person whose chat fixes a build used their daily AI budget (the fix waits until there is some again)."""
    limit = int(store["daily_tokens_per_user"] or 0)
    return bool(limit) and conn.execute("SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM ai_usage WHERE "
                                        "username = ? AND ts > ?", (username, time.time() - 86400)).fetchone()[0] >= limit


# _ask: post VALOR's message in a fix chat and start the assistant's turn (Code mode); returns the answer's id OwO
def _ask(app, conn, chat_id: int, username: str, text: str) -> int:
    """VALOR's message in a fix chat, and the assistant's turn on it (Code mode)."""
    return turn.start(app, conn, chat_id, username, "code", text)[1]


# _say: post a note from VALOR at the end of a fix chat, without a model turn ^w^
def _say(conn, chat_id: int, text: str) -> None:
    """A note from VALOR at the end of a fix chat (no model turn)."""
    now = time.time()
    conn.execute("INSERT INTO chat_messages(chat_id, ts, role, mode, status, content, meta) VALUES (?, ?, 'assistant', "
                 "'code', 'done', ?, ?)", (chat_id, now, text, json.dumps({"steps": [], "valor": True})))
    conn.execute("UPDATE chats SET updated = ? WHERE id = ?", (now, chat_id))


# _set_state: merge fields into the chat's JSON state (job, round, signature, provider...) UwU
def _set_state(conn, chat_id: int, **fields) -> dict:
    """Merge fields into the chat's state (JSON: the fix's job, round, signature, provider, ...)."""
    row = conn.execute("SELECT state FROM chats WHERE id = ?", (chat_id,)).fetchone()
    chat_state = {**json.loads(row["state"] or "{}"), **fields}
    conn.execute("UPDATE chats SET state = ? WHERE id = ?", (json.dumps(chat_state), chat_id))
    return chat_state


# start_fix: open the fix chat for a failed build (one per build and person; an existing one is reused) >w<
def start_fix(app, conn, job: dict, username: str, auto: bool, provider: str | None = None,
              mode: str = "prepare") -> int:
    """The range chat (Code mode) in which the assistant works on a failed build: one per build and person. In the
    modes 'auto' and 'fix' its actions run without approval cards; 'auto' also rebuilds when it is done."""
    name = job_range(job)
    for row in conn.execute("SELECT id, state FROM chats WHERE owner = ? AND kind = 'range' AND range = ?",
                            (username, name)).fetchall():
        if json.loads(row["state"] or "{}").get("job") == job["id"]:
            return row["id"]
    # The chat's title: "Fix: verification failed (N checks)" or "Fix: <error message>" OwO
    now = time.time()
    result = job.get("result") or {}
    failures = [] if result.get("error") else _verify_failures(app.state.cfg, job)
    if failures:
        title = f"Fix: verification failed ({len(failures)} check{'' if len(failures) == 1 else 's'})"
    else:
        title = f"Fix: {result.get('message') or result.get('error') or ''}"
    title = (title if title != "Fix: " else "Fix: failed build")[:120]
    # Its state: which job, which mode, round 1, the failure signature, the jobs so far, the provider, and allow_all
    # for the modes without approval cards ^w^
    chat_state = {"job": job["id"], "fix": mode, "round": 1, "sig": _signature(app.state.cfg, job), "jobs": [job["id"]],
                  **({"provider": provider} if provider else {}),
                  **({"allow_all": True} if mode in ("auto", "fix") else {})}
    # Create the chat, audit it, and send the first fix message UwU
    chat_id = conn.execute("INSERT INTO chats(owner, kind, range, title, created, updated, state) VALUES "
                           "(?, 'range', ?, ?, ?, ?, ?)", (username, name, title, now, now, json.dumps(chat_state))).lastrowid
    db.audit(conn, "chat.fix", "ok", username=username, target=name,
             detail={"chat": chat_id, "job": job["id"], "auto": auto, "mode": mode})
    _ask(app, conn, chat_id, username, fix_prompt(job, failures, mode))
    return chat_id


# continue_fix: the automatic rebuild failed too: go on in the same chat with the next round >w<
def continue_fix(app, conn, chat, job: dict) -> int | None:
    """The automatic rebuild failed: the same chat goes on (round 2, 3, ...). A rebuild that fails the same way as the
    one before gets one more round to try another way; the same failure a third time ends it."""
    cfg, chat_state = app.state.cfg, json.loads(chat["state"] or "{}")
    next_round, signature = int(chat_state.get("round", 1)) + 1, _signature(cfg, job)
    again = signature == chat_state.get("sig")
    # Stop after FIX_ROUNDS, or when the same failure comes back a third time; say why OwO
    if next_round > FIX_ROUNDS or (again and chat_state.get("again")):
        why = "the rebuild failed the same way three times" if again else f"{FIX_ROUNDS} rounds did not fix it"
        _say(conn, chat["id"], f"VALOR stopped fixing this on its own: {why} (job {job['id']}). Read what the assistant "
                               "found above; ask it to try something else, or fix the spec and apply it yourself.")
        _set_state(conn, chat["id"], done="stopped")
        return None
    # Otherwise record the round and tell the assistant (mentioning when its last fix didn't help) ^w^
    _set_state(conn, chat["id"], round=next_round, sig=signature, again=again, revived=0,
               jobs=[*chat_state.get("jobs", []), job["id"]])
    result = job.get("result") or {}
    how = ("the rebuild failed the same way again, so your last fix did not help. Look again and fix it another way."
           if again else "the rebuild failed too.")
    failures = [] if result.get("error") else _verify_failures(cfg, job)
    _ask(app, conn, chat["id"], chat["owner"], f"[VALOR] Round {next_round} of {FIX_ROUNDS}: {how}\n" +
         fix_prompt(job, failures, chat_state.get("fix", "prepare")))
    return chat["id"]


# revive_stalled: mode "auto" only: restart fix turns that ended early UwU
def revive_stalled(app, conn, store: dict) -> list[int]:
    """Mode 'auto': a fix turn that ended early - out of steps, VALOR restarted under it, no provider answered - is
    started again (REVIVES times per round). After that VALOR rebuilds anyway: a transient failure passes then, and
    one that fails again comes back as the next round."""
    out = []
    # Fix chats updated in the last 6 hours >w<
    for chat in conn.execute("SELECT * FROM chats WHERE kind = 'range' AND updated > ?",
                             (time.time() - 6 * 3600,)).fetchall():
        chat_state = json.loads(chat["state"] or "{}")
        if chat_state.get("fix") != "auto" or "job" not in chat_state or chat_state.get("done"):
            continue
        # Only VALOR's own turns (a person who wrote in the chat is handling it) OwO
        rows = conn.execute("SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY id DESC LIMIT 2",
                            (chat["id"],)).fetchall()
        if len(rows) < 2 or rows[0]["role"] != "assistant" or not str(rows[1]["content"]).startswith("[VALOR]"):
            continue                             # only VALOR's own turns: a person who wrote in the chat is there
        # Only failed turns; wait the right time, and not while the owner is over budget ^w^
        turn.mark_interrupted(conn, rows[0])     # a turn still marked running whose thread is gone
        last = conn.execute("SELECT status, meta FROM chat_messages WHERE id = ?", (rows[0]["id"],)).fetchone()
        if last["status"] != "failed":
            continue
        error = json.loads(last["meta"] or "{}").get("error") or {}
        wait = PROVIDER_WAIT if error.get("code") in PROVIDER_FAILED else REVIVE_WAIT
        if time.time() - chat["updated"] < wait or _over_budget(conn, store, chat["owner"]):
            continue                             # over the daily AI budget: it waits until there is some again
        # A few restarts (every provider gets another chance), then rebuild anyway (a transient failure passes then; a
        # real one comes back as the next round) UwU
        revived = int(chat_state.get("revived", 0))
        if revived < REVIVES:
            _set_state(conn, chat["id"], revived=revived + 1, tried=[])         # every provider gets another chance
            _ask(app, conn, chat["id"], chat["owner"],
                 f"[VALOR] Your last answer ended early ({error.get('message', 'error')}); what it did is above. Go on "
                 "from there instead of starting over: make the fix now, record it in the spec, and finish - VALOR "
                 "rebuilds the range as soon as you do.")
        else:
            outcome = rebuild(app, conn, chat, chat["owner"])
            _say(conn, chat["id"], f"The assistant did not finish after {REVIVES + 1} tries. {outcome['note']}")
            if not outcome.get("job"):
                _set_state(conn, chat["id"], done="stopped")
        out.append(chat["id"])
    return out


# rebuild: build the range again after an automatic fix >w<
def rebuild(app, conn, chat, username: str) -> dict:
    """Mode 'auto': build the range again once the assistant is done - its draft when it recorded one. A plan that
    would replace or remove a VM waits for the person."""
    cfg, name = app.state.cfg, chat["range"]
    draft = drafts.load(cfg, name)
    # Use the assistant's draft if it recorded one; validate and plan it OwO
    try:
        text = draft[0] if draft else _load(cfg, name)[1]
        spec = normalize(parse_spec(text), cfg.default_os)
        pve = PVE(cfg)
        checks = validate_cluster(pve, spec)
        if not checks["ok"]:
            return {"note": "VALOR did not rebuild: the spec has problems - " +
                            "; ".join(f"{e['location']}: {e['message']}" for e in checks["errors"])[:600]}
        plan = make_plan(pve, spec)
    except ValorError as e:
        return {"note": f"VALOR did not rebuild: {e.message}"}
    # A plan that would replace or remove a VM waits for a person (VMs could be lost) ^w^ *ears go flat at risky plans*
    risky = [f"{action['action']} {action['host']}" for action in plan["actions"] if action["action"] in RISKY]
    if risky:
        return {"note": f"VALOR did not rebuild on its own: the plan would {', '.join(risky)}. Review the plan on the "
                        "range page and apply it there."}
    # The draft becomes the range's spec (atomic replace), as an approved apply does, and is audited UwU
    if draft:                                # the assistant's draft becomes the range's spec, as an approved apply does
        path = cfg.ranges_dir / f"{name}.yaml"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
        drafts.discard(cfg, name)
        db.audit(conn, "range.spec.save", "ok", username=username, target=name,
                 detail={"from": "draft", "by": "assistant", "chat": chat["id"]})
    # Start the build (verified), remember it in the chat, audit, and say what changes >w<
    job = ops.start(cfg, "apply", {"spec": f"{name}.yaml", "verify": True}, f"assistant:{username}:{chat['id']}",
                    background=True)
    _set_state(conn, chat["id"], rebuild=job["job"])
    db.audit(conn, "range.apply", "ok", username=username, target=name,
             detail={"job": job["job"], "by": "assistant", "chat": chat["id"], "changes": plan["summary"]})
    changes = ", ".join(f"{count} {action}" for action, count in plan["summary"].items()
                        if count and action != "keep") or "no changes"
    return {"job": job["job"], "note": f"VALOR rebuilds the range now ({changes})."}


# fix_failed_builds: one pass of the watcher (runs every 10 s) OwO
def fix_failed_builds(app) -> list[int]:
    """Builds that ended in the last FIX_WINDOW s, while 'fix failed builds' is on and a provider is connected:
    a failed one goes to the assistant (a chat of the person who started it, or the first admin for CLI/MCP builds;
    a failed automatic rebuild continues its chat); one the worker did not finish runs once more; a successful
    automatic rebuild closes its chat. Fix turns that ended early are started again (revive_stalled)."""
    cfg, out = app.state.cfg, []
    conn = db.connect(app.state.wcfg.db)
    try:
        # Only when a provider exists and the feature is on ^w^
        store = ai_settings(conn, app.state.box)
        if not store["providers"] or not store["fix_failed_builds"]:
            return out
        # Each build among the 20 newest jobs that ended (once only, and only within FIX_WINDOW) UwU
        for listed in jobs.list_jobs(cfg, 20):
            if (listed.get("kind") != "apply" or listed.get("state") not in ("failed", "succeeded")
                    or listed["id"] in _fix_seen):
                continue
            _fix_seen.add(listed["id"])
            try:
                finished = datetime.strptime(str(listed.get("finished")), "%Y-%m-%dT%H:%M:%S%z").timestamp()
            except ValueError:
                continue
            if time.time() - finished > FIX_WINDOW:
                continue
            chat_id = _handle_finished_build(app, conn, store, jobs.status(cfg, listed["id"], tail=1))
            if chat_id:
                out.append(chat_id)
        # Then restart stalled fix turns >w<
        out += [chat_id for chat_id in revive_stalled(app, conn, store) if chat_id not in out]
    finally:
        conn.close()
    return out


# _handle_finished_build: one build that just ended OwO
def _handle_finished_build(app, conn, store: dict, job: dict) -> int | None:
    """One build that just ended. Returns the fix chat it started or continued, if any."""
    cfg = app.state.cfg
    origin = str(job.get("origin", ""))
    started_by = _origin(origin)
    # If it was an automatic fix's rebuild, find that chat ^w^
    chat = (conn.execute("SELECT * FROM chats WHERE id = ?", (started_by["chat"],)).fetchone()
            if started_by.get("chat") else None)       # set when this build was an automatic fix's rebuild
    # Success: tell the fix chat (with the scores) and close it UwU
    if job["state"] == "succeeded":
        if chat is not None:
            summary = ((job.get("result") or {}).get("verify") or {}).get("summary") or {}
            tests = (f" Verification: tests {summary.get('tests_passed')}/{summary.get('tests_total')}, baseline "
                     f"{summary.get('baseline_passed')}/{summary.get('baseline_total')}." if summary else "")
            _say(conn, chat["id"], f"VALOR rebuilt the range: the build succeeded (job {job['id']}).{tests}")
            _set_state(conn, chat["id"], done="fixed")
        return None
    # Skip ranges deleted in the meantime >w<
    name = job_range(job)
    if not (cfg.ranges_dir / f"{name}.yaml").exists():
        return None                                     # the range was deleted meanwhile
    # The worker stopped mid-build: run the same build once more (not again for a rerun) OwO
    if (job.get("result") or {}).get("error") in NOT_FIXABLE:
        if not started_by.get("rerun"):      # the worker stopped (reboot, crash): run the same build once more
            rerun = ops.start(cfg, "apply", job["target"], f"rerun:{origin}", background=True)
            db.audit(conn, "range.apply", "ok", username=started_by.get("user", ""), target=name,
                     detail={"job": rerun["job"], "rerun_of": job["id"], "by": "valor"})
        return None
    # Otherwise: continue the rebuild's chat, or start a new fix chat (unless nobody can own it or they're over
    # budget) ^w^
    owner = chat["owner"] if chat is not None else _owner(conn, origin)
    if owner is None or _over_budget(conn, store, owner):
        return None
    if chat is not None:
        return continue_fix(app, conn, chat, job)
    return start_fix(app, conn, job, owner, True, store["fix_provider"] or None, store["fix_mode"])


# fallback: when a fix chat's provider can't answer, pick the next one: the failed-builds provider, then the
# default, then the others, skipping ones already tried UwU
def fallback(app, conn, chat, settings: dict, e: ValorError) -> dict | None:
    """A fix chat whose provider could not answer (context too small, unreachable, credits, ...) moves on to the
    next one: the failed-builds provider, the default, then the others."""
    chat_state = json.loads(chat["state"] or "{}")
    if "job" not in chat_state or e.code not in PROVIDER_FAILED:
        return None
    store = ai_settings(conn, app.state.box)
    first = store["fix_provider"] or store["default"]
    order = sorted(store["providers"], key=lambda p: (p["id"] != first, p["id"] != store["default"]))
    tried = {*(chat_state.get("tried") or []), settings.get("id")}
    next_provider = next((p for p in order if p["id"] not in tried), None)
    if next_provider is not None:
        _set_state(conn, chat["id"], provider=next_provider["id"], tried=sorted(t for t in tried if t))
    return next_provider


# watch_failed_builds: the web service's background thread: run fix_failed_builds every `every` seconds until
# asked to stop; errors are logged and the next round tries again >w< *keeps watch all night*
def watch_failed_builds(app, stop: threading.Event, every: float = 10.0) -> None:
    """The web service's thread that hands failed builds to the assistant (builds run in the worker service)."""
    while not stop.wait(every):
        try:
            fix_failed_builds(app)
        except Exception:                            # pragma: no cover - logged, the next round tries again
            log.exception("handing failed builds to the assistant")
