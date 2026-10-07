"""Saved chats: the chat builder and the chat panel on a range's map, kept per user (owners see and continue their
chats; administrators can read everyone's).

A turn runs in the background: the browser polls the assistant message, whose `meta.steps` is a live feed of what
the model does - its reasoning (when the provider exposes it), text, every tool call with its output, VALOR's checks
and spec edits. On a range, Question mode may look inside the VMs with read-only tools; Code mode operates the range
(commands, files, power, snapshots, the spec) and stops at every action that changes something until the owner
approves or denies it, unless they allowed all actions for that chat. The model's side of a turn (frozen system
prompt, history, tool calls and results) is kept with the message, so a turn resumes exactly where it stopped.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import agent, ai, drafts, state
from ..cluster import load_catalog, range_vms
from ..errors import ValorError
from ..plan import make_plan
from ..pve import PVE
from ..spec import ROUTER, normalize, parse_spec
from ..validate import validate_cluster
from . import db, editing, topology
from .builder import _settings, record_usage, reference_for, spec_check, start_ai
from .core import ApiError, Session, require
from .ranges import _cfg, _load, _name, _plan_hash

router = APIRouter(prefix="/api")
log = logging.getLogger("valor.chats")

MODES = {"builder": ("build",), "range": ("question", "plan", "code")}
MAX_ROUNDS = 40                 # model calls per turn in the agent loop
FLUSH = 0.4                     # seconds between feed writes while the model streams
_active: dict[int, threading.Event] = {}          # assistant message id -> stop flag of the thread running it
_lock = threading.Lock()


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["builder", "range"]
    range: str | None = Field(default=None, max_length=15)


class PatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=120)
    allow_all: bool | None = None


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)
    mode: Literal["build", "question", "plan", "code"] = "build"


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: dict[str, Literal["approve", "deny"]] = Field(max_length=64)
    allow_all: bool = False


# ---------------------------------------------------------------------- rows and access
def _chat_row(r, extra: dict | None = None) -> dict:
    st = json.loads(r["state"] or "{}")
    return {"id": r["id"], "owner": r["owner"], "kind": r["kind"], "range": r["range"], "title": r["title"],
            "created": r["created"], "updated": r["updated"], "allow_all": bool(st.get("allow_all")), **(extra or {})}


def _msg_row(r) -> dict:
    return {"id": r["id"], "chat_id": r["chat_id"], "ts": r["ts"], "role": r["role"], "mode": r["mode"],
            "content": r["content"], "status": r["status"], "version": r["version"], "meta": json.loads(r["meta"] or "{}")}


def _get_chat(s: Session, chat_id: int, write: bool = False):
    r = s.conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    mine = r is not None and r["owner"].lower() == s.username.lower()
    if r is None or not (mine or (not write and s.user["role"] == "admin")):
        raise ApiError(404, "no_chat", "There is no such chat.")
    return r


def _interrupted(conn, r) -> None:
    """A turn still marked running whose thread is gone (VALOR restarted): mark it failed. A thread is registered
    under _lock in the same step that marks its message running and writes its final status before it unregisters,
    so checking and updating under _lock (only while the row still says running) never hits a live turn."""
    if r["status"] != "running":
        return
    meta = json.loads(r["meta"] or "{}")
    meta["error"] = {"code": "interrupted", "message": "VALOR restarted while this answer was being written"}
    with _lock:
        if r["id"] not in _active:
            conn.execute("UPDATE chat_messages SET status = 'failed', meta = ?, version = version + 1 "
                         "WHERE id = ? AND status = 'running'", (json.dumps(meta), r["id"]))


# ---------------------------------------------------------------------- API
@router.get("/chats")
def list_chats(scope: Literal["mine", "all"] = "mine", range: str | None = Query(default=None, max_length=15),
               kind: Literal["builder", "range"] | None = None, limit: int = Query(default=100, ge=1, le=500),
               s: Session = Depends(require("viewer"))) -> dict:
    where, args = [], []
    if scope == "mine" or s.user["role"] != "admin":
        where.append("c.owner = ?")
        args.append(s.username)
    if range:
        where.append("c.range = ?")
        args.append(range)
    if kind:
        where.append("c.kind = ?")
        args.append(kind)
    rows = s.conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.chat_id = c.id) AS n, "
        "(SELECT status FROM chat_messages m WHERE m.chat_id = c.id AND m.role = 'assistant' ORDER BY m.id DESC "
        "LIMIT 1) AS last FROM chats c" + (" WHERE " + " AND ".join(where) if where else "") +
        " ORDER BY c.updated DESC LIMIT ?", (*args, limit)).fetchall()
    return {"chats": [_chat_row(r, {"messages": r["n"], "status": r["last"] or "done"}) for r in rows]}


@router.post("/chats")
def create_chat(body: ChatIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    if body.kind == "range":
        if not body.range:
            raise ApiError(400, "invalid_chat", "A range chat needs the range's name.")
        _load(_cfg(request), _name(body.range))
    now = time.time()
    cur = s.conn.execute("INSERT INTO chats(owner, kind, range, created, updated) VALUES (?, ?, ?, ?, ?)",
                         (s.username, body.kind, body.range if body.kind == "range" else None, now, now))
    s.audit("chat.create", target=body.range or "builder", detail={"chat": cur.lastrowid})
    return _chat_row(s.conn.execute("SELECT * FROM chats WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.get("/chats/{chat_id}")
def get_chat(chat_id: int, request: Request, s: Session = Depends(require("viewer"))) -> dict:
    c = _get_chat(s, chat_id)
    rows = s.conn.execute("SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()
    for r in rows:
        _interrupted(s.conn, r)
    rows = s.conn.execute("SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()
    out = _chat_row(c, {"spec": c["spec"], "messages": [_msg_row(r) for r in rows],
                        "readonly": c["owner"].lower() != s.username.lower()})
    if c["kind"] == "builder" and c["spec"]:
        try:
            out["topology"] = topology.build(normalize(parse_spec(c["spec"]), _cfg(request).default_os))
        except ValorError:
            out["topology"] = None
    return out


@router.patch("/chats/{chat_id}")
def patch_chat(chat_id: int, body: PatchIn, s: Session = Depends(require("operator"))) -> dict:
    c = _get_chat(s, chat_id, write=True)
    st = json.loads(c["state"] or "{}")
    if body.allow_all is not None:
        st["allow_all"] = body.allow_all
        s.audit("chat.allow_all", target=c["range"] or "builder", detail={"chat": chat_id, "on": body.allow_all})
    title = body.title.strip() if body.title is not None else c["title"]
    s.conn.execute("UPDATE chats SET title = ?, state = ? WHERE id = ?", (title, json.dumps(st), chat_id))
    return _chat_row(s.conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone())


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: int, s: Session = Depends(require("operator"))) -> dict:
    c = _get_chat(s, chat_id)
    if c["owner"].lower() != s.username.lower() and s.user["role"] != "admin":
        raise ApiError(404, "no_chat", "There is no such chat.")
    running = s.conn.execute("SELECT id FROM chat_messages WHERE chat_id = ? AND status = 'running'", (chat_id,)).fetchall()
    for r in running:
        ev = _active.get(r["id"])
        if ev:
            ev.set()
    s.conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    s.audit("chat.delete", target=c["range"] or "builder", detail={"chat": chat_id, "owner": c["owner"]})
    return {"ok": True}


@router.get("/chats/{chat_id}/messages/{msg_id}")
def get_message(chat_id: int, msg_id: int, v: int = Query(default=-1), s: Session = Depends(require("viewer"))):
    _get_chat(s, chat_id)
    r = s.conn.execute("SELECT * FROM chat_messages WHERE id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
    if r is None:
        raise ApiError(404, "no_message", "There is no such message.")
    _interrupted(s.conn, r)
    r = s.conn.execute("SELECT * FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
    if r["version"] == v:
        return {"unchanged": True, "version": v, "status": r["status"]}
    return _msg_row(r)


@router.post("/chats/{chat_id}/messages")
def send(chat_id: int, body: MessageIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    c = _get_chat(s, chat_id, write=True)
    if body.mode not in MODES[c["kind"]]:
        raise ApiError(400, "invalid_mode", f"A {c['kind']} chat has the modes {', '.join(MODES[c['kind']])}.")
    busy = s.conn.execute("SELECT id FROM chat_messages WHERE chat_id = ? AND status IN ('running', 'waiting')",
                          (chat_id,)).fetchall()
    for r in busy:
        _interrupted(s.conn, s.conn.execute("SELECT * FROM chat_messages WHERE id = ?", (r["id"],)).fetchone())
    if s.conn.execute("SELECT 1 FROM chat_messages WHERE chat_id = ? AND status IN ('running', 'waiting')",
                      (chat_id,)).fetchone():
        raise ApiError(409, "chat_busy", "The assistant is still working on this chat (or waiting for your approval).")
    if c["kind"] == "range":
        _load(_cfg(request), _name(c["range"]))
    start_ai(request, s)                         # rate limit, provider configured, daily budget
    now = time.time()
    u = s.conn.execute("INSERT INTO chat_messages(chat_id, ts, role, mode, content) VALUES (?, ?, 'user', ?, ?)",
                       (chat_id, now, body.mode, body.message)).lastrowid
    with _lock:
        a = s.conn.execute("INSERT INTO chat_messages(chat_id, ts, role, mode, status, meta) VALUES (?, ?, 'assistant', "
                           "?, 'running', ?)", (chat_id, now, body.mode, json.dumps({"steps": []}))).lastrowid
        ev = _active[a] = threading.Event()
    title = c["title"] or re.sub(r"\s+", " ", body.message).strip()[:80]
    s.conn.execute("UPDATE chats SET updated = ?, title = ? WHERE id = ?", (now, title, chat_id))
    s.audit("chat.message", target=c["range"] or "builder", detail={"chat": chat_id, "mode": body.mode})
    _spawn(request.app, chat_id, a, s.username, False, ev)
    rows = s.conn.execute("SELECT * FROM chat_messages WHERE id IN (?, ?) ORDER BY id", (u, a)).fetchall()
    return {"messages": [_msg_row(r) for r in rows]}


@router.post("/chats/{chat_id}/messages/{msg_id}/decide")
def decide(chat_id: int, msg_id: int, body: DecideIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    c = _get_chat(s, chat_id, write=True)
    r = s.conn.execute("SELECT * FROM chat_messages WHERE id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
    if r is None or r["status"] != "waiting":
        raise ApiError(409, "not_waiting", "This answer is not waiting for a decision.")
    meta = json.loads(r["meta"])
    open_ = {st["id"] for st in meta["steps"] if st.get("type") == "tool" and st.get("status") == "pending"}
    for call_id, d in body.decisions.items():
        if call_id not in open_:
            raise ApiError(400, "invalid_decision", f"{call_id} is not waiting for a decision.")
    for st in meta["steps"]:
        if st.get("type") == "tool" and st["id"] in body.decisions:
            st["status"] = "approved" if body.decisions[st["id"]] == "approve" else "denied"
            st["decided_by"] = s.username
            s.audit("chat.decision", target=c["range"] or "", detail={"chat": chat_id, "tool": st["name"],
                                                                     "summary": st.get("summary", "")[:300],
                                                                     "decision": body.decisions[st["id"]]})
    if body.allow_all:
        cs = json.loads(c["state"] or "{}")
        cs["allow_all"] = True
        s.conn.execute("UPDATE chats SET state = ? WHERE id = ?", (json.dumps(cs), chat_id))
        s.audit("chat.allow_all", target=c["range"] or "", detail={"chat": chat_id, "on": True})
    left = any(st.get("type") == "tool" and st.get("status") == "pending" for st in meta["steps"])
    if not left:
        start_ai(request, s)
    with _lock:
        s.conn.execute("UPDATE chat_messages SET meta = ?, status = ?, version = version + 1 WHERE id = ?",
                       (json.dumps(meta), "waiting" if left else "running", msg_id))
        ev = None if left else _active.setdefault(msg_id, threading.Event())
    if ev is not None:
        _spawn(request.app, chat_id, msg_id, s.username, True, ev)
    return _msg_row(s.conn.execute("SELECT * FROM chat_messages WHERE id = ?", (msg_id,)).fetchone())


@router.post("/chats/{chat_id}/messages/{msg_id}/stop")
def stop(chat_id: int, msg_id: int, s: Session = Depends(require("operator"))) -> dict:
    _get_chat(s, chat_id, write=True)
    r = s.conn.execute("SELECT * FROM chat_messages WHERE id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
    if r is None:
        raise ApiError(404, "no_message", "There is no such message.")
    ev = _active.get(msg_id)
    if ev is not None:
        ev.set()                                  # the thread finishes the current step, then stops
    elif r["status"] in ("running", "waiting"):
        meta = json.loads(r["meta"])
        for st in meta["steps"]:
            if st.get("type") == "tool" and st.get("status") in ("pending", "queued", "approved"):
                st["status"] = "cancelled"
        s.conn.execute("UPDATE chat_messages SET status = 'stopped', meta = ?, version = version + 1, "
                       "content = CASE WHEN content = '' THEN '(stopped)' ELSE content END WHERE id = ?",
                       (json.dumps(meta), msg_id))
    return {"ok": True}


# ---------------------------------------------------------------------- the background turn
def _spawn(app, chat_id: int, msg_id: int, username: str, resume: bool, ev: threading.Event) -> None:
    """Start the turn's thread; the caller registered ev in _active when it marked the message running."""
    t = threading.Thread(target=_run, args=(app, chat_id, msg_id, username, resume, ev), daemon=True,
                         name=f"chat-{msg_id}")
    t.start()


class Stopped(Exception):
    pass


class Feed:
    """The assistant message as the browser sees it: steps appended and streamed, written at most every FLUSH s."""

    def __init__(self, conn, msg_id: int, stop: threading.Event):
        self.conn, self.id, self.stop = conn, msg_id, stop
        r = conn.execute("SELECT meta FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
        self.meta = json.loads(r["meta"] or "{}")
        self.meta.setdefault("steps", [])
        self.last = 0.0
        self.n = len(self.meta["steps"])

    @property
    def steps(self) -> list[dict]:
        return self.meta["steps"]

    def add(self, step: dict) -> dict:
        self.close()
        self.n += 1
        step.setdefault("id", f"s{self.n}")
        step.setdefault("ts", time.time())
        self.steps.append(step)
        self.meta.pop("preparing", None)
        self.flush(True)
        return step

    def close(self) -> None:
        for st in self.steps:
            if st.get("type") in ("thinking", "text") and st.get("open"):
                st["open"] = False
                st["ended"] = time.time()

    def delta(self, kind: str, text: str) -> None:
        if self.stop.is_set():
            raise Stopped()
        if kind == "tool":
            self.close()
            self.meta["preparing"] = text
        else:
            last = self.steps[-1] if self.steps else None
            if last and last.get("type") == kind and last.get("open"):
                last["text"] += text
            else:
                self.close()
                self.n += 1
                self.steps.append({"id": f"s{self.n}", "type": kind, "text": text, "open": True, "ts": time.time()})
        self.flush()

    def flush(self, force: bool = False, status: str | None = None, content: str | None = None) -> None:
        now = time.time()
        if not force and now - self.last < FLUSH:
            return
        self.last = now
        sets, args = ["meta = ?", "version = version + 1"], [json.dumps(self.meta, default=str)]
        if status:
            sets.append("status = ?")
            args.append(status)
        if content is not None:
            sets.append("content = ?")
            args.append(content)
        self.conn.execute(f"UPDATE chat_messages SET {', '.join(sets)} WHERE id = ?", (*args, self.id))


def _provider(app, conn):
    st = _settings(conn)
    key = app.state.box.open_text(st["api_key"], "ai:api_key") if st.get("api_key") else ""
    return ai.provider_from(st, key), st


def _run(app, chat_id: int, msg_id: int, username: str, resume: bool, ev: threading.Event) -> None:
    conn = db.connect(app.state.wcfg.db)
    feed = None
    st: dict = {}
    try:
        feed = Feed(conn, msg_id, ev)
        chat = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        msg = conn.execute("SELECT * FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
        prov, st = _provider(app, conn)
        turn = Turn(app, conn, chat, msg, username, prov, st, feed)
        content, status = turn.run(resume)
        feed.close()
        feed.flush(True, status=status, content=content)
    except Stopped:
        feed.close()
        for s in feed.steps:
            if s.get("type") == "tool" and s.get("status") in ("pending", "queued", "approved", "running"):
                s["status"] = "cancelled"
        feed.flush(True, status="stopped", content="(stopped)")
    except ValorError as e:
        used = e.details if isinstance(e.details, dict) else {}
        if st and used:
            record_usage(conn, username, st, int(used.get("input_tokens", 0)), int(used.get("output_tokens", 0)), False)
        if feed is not None:
            feed.close()
            feed.meta["error"] = {"code": e.code, "message": e.message, "hint": e.hint}
            feed.flush(True, status="failed")
    except Exception as e:                           # pragma: no cover - logged, shown as failed
        log.exception("chat turn %s failed", msg_id)
        if feed is not None:
            feed.meta["error"] = {"code": "internal", "message": f"internal error: {e.__class__.__name__}"}
            feed.flush(True, status="failed")
    finally:
        with _lock:
            if _active.get(msg_id) is ev:          # a resumed turn may already have registered its own thread
                _active.pop(msg_id)
        conn.execute("UPDATE chats SET updated = ? WHERE id = ?", (time.time(), chat_id))
        conn.close()


class Turn:
    def __init__(self, app, conn, chat, msg, username: str, prov: ai.Provider, settings: dict, feed: Feed):
        self.app, self.conn, self.chat, self.msg, self.username = app, conn, chat, msg, username
        self.prov, self.settings, self.feed = prov, settings, feed
        self.cfg = app.state.cfg
        self.mode = msg["mode"]
        self.used = [0, 0]
        try:
            self.pve = PVE(self.cfg)
        except ValorError:
            self.pve = None

    # ------------------------------------------------------------------ shared
    def history(self) -> list[dict]:
        """Earlier turns as plain text (their tool calls summarized), then this turn's question."""
        rows = self.conn.execute("SELECT * FROM chat_messages WHERE chat_id = ? AND id < ? ORDER BY id",
                                 (self.chat["id"], self.msg["id"])).fetchall()
        out = []
        for r in rows:
            if r["role"] == "user":
                out.append({"role": "user", "content": r["content"]})
                continue
            meta = json.loads(r["meta"] or "{}")
            text = r["content"] or ""
            acts = [f"- {s.get('summary')} ({s.get('status')}{', exit ' + str(s['exit']) if s.get('exit') is not None else ''})"
                    for s in meta.get("steps", []) if s.get("type") == "tool"]
            if acts:
                text += "\n\n[Actions in that answer]\n" + "\n".join(acts[-30:])
            if meta.get("questions"):
                text += "\n\n[Questions asked]\n" + "\n".join(
                    f"{q['id']}. {q['question']}" + (f" Options: {', '.join(q['options'])}" if q.get("options") else "")
                    for q in meta["questions"])
            out.append({"role": "assistant", "content": text or "(no answer)"})
        return ai.trim(out)

    def record(self, r: ai.Reply) -> None:
        self.used[0] += r.input_tokens
        self.used[1] += r.output_tokens
        record_usage(self.conn, self.username, self.settings, r.input_tokens, r.output_tokens, True)
        self.feed.meta["usage"] = {"input_tokens": self.used[0], "output_tokens": self.used[1]}
        limit = int(self.settings.get("daily_tokens_per_user") or 0)
        if limit:
            spent = self.conn.execute("SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM ai_usage WHERE "
                                      "username = ? AND ts > ?", (self.username, time.time() - 86400)).fetchone()[0]
            if spent >= limit:
                raise ValorError("ai_budget", f"You used your daily AI budget ({limit} tokens).")

    def run(self, resume: bool) -> tuple[str, str]:
        if self.chat["kind"] == "builder":
            return self.builder()
        if self.mode == "plan":
            return self.plan()
        return self.agent(resume)

    def finish_text(self, text: str) -> str:
        """The final answer: questions become cards, and the streamed copy of it leaves the feed."""
        questions, text = ai.extract_questions(text)
        if questions:
            self.feed.meta["questions"] = questions
        steps = self.feed.steps
        if steps and steps[-1].get("type") == "text":
            steps.pop()
        return text.strip() or ("(questions below)" if questions else "(no answer)")

    def note(self, problems: list[str]) -> None:
        self.feed.add({"type": "check", "text": "VALOR checked the spec and asked the model to fix: " + "; ".join(problems)[:1500]})

    # ------------------------------------------------------------------ chat builder
    def builder(self) -> tuple[str, str]:
        system = ai.builder_prompt(reference_for(self.cfg, self.pve))
        msgs = self.history()
        spec = self.chat["spec"]
        if spec.strip() and msgs and msgs[-1]["role"] == "user":
            msgs[-1] = {"role": "user", "content": msgs[-1]["content"] +
                        f"\n\nThe current spec in the builder:\n```yaml\n{spec.strip()}\n```"}
        own = (re.search(r"^name:\s*([a-z0-9-]+)", spec, re.M) or [None, ""])[1]
        base = spec_check(self.cfg, self.pve, own)

        def check(text: str) -> list[str]:
            problems = [p for p in base(text) if not p.startswith("the range is called")]
            check.warnings = base.warnings
            return problems
        check.warnings = []
        res = ai.build(self.prov, system, msgs, check, on_delta=self.feed.delta, on_check=self.note)
        self.record(ai.Reply("", res["input_tokens"], res["output_tokens"]))
        meta = self.feed.meta
        if res["questions"]:
            meta["questions"] = res["questions"]
        if res["yaml"] and not res["problems"]:
            meta.update(yaml=res["yaml"], fixed=res["fixed"], warnings=check.warnings)
            self.conn.execute("UPDATE chats SET spec = ? WHERE id = ?", (res["yaml"], self.chat["id"]))
        elif res["problems"]:
            meta["problems"] = res["problems"]
        steps = self.feed.steps
        if steps and steps[-1].get("type") == "text":
            steps.pop()
        return res["reply"] or ("(questions below)" if res["questions"] else "(no explanation)"), "done"

    # ------------------------------------------------------------------ range: plan (spec + VALOR's plan)
    def working(self) -> tuple[str, bool]:
        _, saved = _load(self.cfg, self.chat["range"])
        d = drafts.load(self.cfg, self.chat["range"])
        return (d[0] if d else saved), d is not None

    def plan(self) -> tuple[str, str]:
        name = self.chat["range"]
        working, has_draft = self.working()
        system = ai.range_prompt("plan", reference_for(self.cfg, self.pve), working, _live(self.cfg, self.pve, name, has_draft))
        check = spec_check(self.cfg, self.pve, name)
        res = ai.build(self.prov, system, self.history(), check, on_delta=self.feed.delta, on_check=self.note)
        self.record(ai.Reply("", res["input_tokens"], res["output_tokens"]))
        meta = self.feed.meta
        meta["problems"] = res["problems"]
        if res["questions"]:
            meta["questions"] = res["questions"]
        if not res["problems"]:
            meta.update(fixed=res["fixed"], warnings=check.warnings)
        if res["yaml"] and not res["problems"]:
            drafts.save(self.cfg, name, res["yaml"], self.username, "chat:plan")
            v = editing.view(self.cfg, name)
            meta["draft"] = v["draft"]["summary"] if v["draft"] else None
            meta["draft_changed"] = True
            if self.pve is not None:
                spec = normalize(parse_spec(res["yaml"]), self.cfg.default_os)
                try:
                    if validate_cluster(self.pve, spec)["ok"]:
                        p = make_plan(self.pve, spec)
                        meta["plan"] = {"summary": p["summary"], "plan_hash": _plan_hash(spec, p),
                                        "actions": [{k: a.get(k) for k in ("host", "action", "reasons")}
                                                    for a in p["actions"] if a["action"] not in ("keep", "restamp")]}
                except ValorError as e:
                    meta["problems"] = [e.message]
        steps = self.feed.steps
        if steps and steps[-1].get("type") == "text":
            steps.pop()
        return res["reply"] or "(no explanation)", "done"

    # ------------------------------------------------------------------ range: question / code (tools)
    def context(self) -> agent.Context:
        name = self.chat["range"]
        if self.pve is None:
            raise ValorError("cluster_unreachable", "VALOR cannot reach the Proxmox cluster")
        spec, _ = _load(self.cfg, name)
        catalog = load_catalog(self.cfg)
        families = {ROUTER: catalog.get(spec.router.os or "", {}).get("family", "debian"),
                    **{h.name: catalog.get(h.os or "", {}).get("family", "debian") for h in spec.hosts}}
        vms = {vm.host: vm for vm in range_vms(self.pve, name) if vm.host}

        def status() -> dict:
            working, has_draft = self.working()
            live = _live(self.cfg, self.pve, name, has_draft)
            live["hosts"] = {ROUTER: {"status": vms.get(ROUTER).status if ROUTER in vms else "missing",
                                      "family": families[ROUTER]}}
            for h in spec.hosts:
                live["hosts"][h.name] = {"status": vms[h.name].status if h.name in vms else "missing", "os": h.os,
                                         "family": families[h.name], "address": str(h.address), "segment": h.segment}
            return live

        def check_spec(text: str):
            problems = spec_check(self.cfg, self.pve, name)(text)
            working, _ = self.working()
            return problems, drafts.unified_diff(working, text, name), None

        def save_spec(text: str) -> dict:
            drafts.save(self.cfg, name, text, self.username, "chat:code")
            v = editing.view(self.cfg, name)
            self.feed.meta["draft_changed"] = True
            return {"draft": v["draft"]["summary"] if v["draft"] else None, "diff": v["draft"]["diff"] if v["draft"] else ""}
        return agent.Context(self.cfg, self.pve, name, self.username, vms, families, status, check_spec, save_spec)

    def agent(self, resume: bool) -> tuple[str, str]:
        name = self.chat["range"]
        state_ = json.loads(self.msg["model"] or "{}")
        ctx = self.context()
        tools = agent.tool_specs(self.mode)
        if not resume or not state_:
            working, has_draft = self.working()
            live = _live(self.cfg, self.pve, name, has_draft)
            # frozen for the whole turn: resuming after an approval must send the model the same conversation
            state_ = {"system": ai.range_prompt(self.mode, reference_for(self.cfg, self.pve), working, live),
                      "history": self.history(), "transcript": []}
        else:
            self.run_pending(state_, ctx)
        for _ in range(MAX_ROUNDS):
            if self.feed.stop.is_set():
                raise Stopped()
            r = self.prov.complete(state_["system"], state_["history"] + state_["transcript"], tools=tools,
                                   on_delta=self.feed.delta)
            self.record(r)
            self.feed.close()
            if r.truncated:
                e = ai.cut_off(self.prov, r)
                e.details = None                 # its tokens are counted already
                raise e
            state_["transcript"].append({"role": "assistant", "content": r.text, "tool_calls": r.tool_calls,
                                         **({"raw": r.raw} if r.raw else {})})
            if not r.tool_calls:
                self.save_state(state_)
                return self.finish_text(r.text), "done"
            waiting = self.handle_calls(r.tool_calls, state_, ctx)
            self.save_state(state_)
            if waiting:
                return "", "waiting"
        raise ValorError("ai_too_many_steps", f"the assistant used {MAX_ROUNDS} steps without finishing",
                         hint="Ask it to continue, or split the task.")

    def save_state(self, state_: dict) -> None:
        self.conn.execute("UPDATE chat_messages SET model = ? WHERE id = ?", (json.dumps(state_), self.msg["id"]))

    def allow_all(self) -> bool:
        c = self.conn.execute("SELECT state FROM chats WHERE id = ?", (self.chat["id"],)).fetchone()
        return bool(json.loads(c["state"] or "{}").get("allow_all"))

    def handle_calls(self, calls: list[dict], state_: dict, ctx: agent.Context) -> bool:
        """Run the calls in order until one needs the person: it and the ones after it wait. True when waiting."""
        results: dict[str, dict] = {}
        waiting = False
        allow_all = self.allow_all()
        for call in calls:
            args = call["input"]
            problem = agent.validate(call["name"], args, ctx)
            step = {"type": "tool", "id": call["id"], "name": call["name"], "summary": agent.summary(call["name"], args or {}),
                    "purpose": (args or {}).get("purpose", ""), "input": _shown(call["name"], args or {})}
            if problem:
                self.feed.add({**step, "status": "failed", "output": problem})
                results[call["id"]] = {"content": problem, "error": True}
                continue
            family = ctx.families.get(args.get("host", ""), "debian")
            readonly = agent.is_readonly(call["name"], args, family)
            step["readonly"] = readonly
            if call["name"] == "update_spec":
                problems, diff, _ = ctx.check_spec(args["yaml"])
                if problems:
                    msg = "VALOR could not use that spec:\n" + "\n".join(f"- {p}" for p in problems)
                    self.feed.add({**step, "status": "failed", "output": msg})
                    results[call["id"]] = {"content": msg, "error": True}
                    continue
                step["diff"] = diff
            if not readonly and self.mode != "code":
                msg = "Question mode cannot change anything: tell the person to switch to Code mode for this."
                self.feed.add({**step, "status": "refused", "output": msg})
                results[call["id"]] = {"content": msg, "error": True}
                continue
            if waiting or (not readonly and not allow_all):
                needs = not readonly and not allow_all
                self.feed.add({**step, "status": "pending" if needs else "queued", "approval": needs})
                waiting = True
                continue
            results[call["id"]] = self.execute(step, call, ctx, approved_by="auto" if readonly else "allow_all")
        state_["results"] = results
        if not waiting:
            self.append_results(state_, calls)
        else:
            state_["pending_calls"] = calls
        return waiting

    def execute(self, step: dict, call: dict, ctx: agent.Context, approved_by: str) -> dict:
        existing = next((s for s in self.feed.steps if s.get("id") == call["id"] and s.get("type") == "tool"), None)
        st = existing or self.feed.add(step)
        st["status"], st["started"] = "running", time.time()
        self.feed.flush(True)
        res = agent.execute(call["name"], call["input"], ctx)
        st.update(status="failed" if res.get("error") else "done", output=res.get("output") or res["content"],
                  exit=res.get("exit"), seconds=res.get("seconds") or round(time.time() - st["started"], 1),
                  **({"meta": res["meta"]} if res.get("meta") else {}))
        self.feed.flush(True)
        if call["name"] != "range_status":
            db.audit(self.conn, "chat.action", "fail" if res.get("error") else "ok", username=self.username,
                     target=ctx.range, detail={"chat": self.chat["id"], "tool": call["name"],
                                               "summary": st.get("summary", "")[:500], "approved_by": approved_by})
        return {"content": res["content"], "error": bool(res.get("error"))}

    def run_pending(self, state_: dict, ctx: agent.Context) -> None:
        """After the person decided: run approved and queued calls in order, answer denied ones, then continue."""
        calls = state_.pop("pending_calls", [])
        results = state_.get("results", {})
        by_id = {s["id"]: s for s in self.feed.steps if s.get("type") == "tool"}
        for call in calls:
            if call["id"] in results:
                continue
            st = by_id.get(call["id"])
            if st is None:
                continue
            if self.feed.stop.is_set():
                raise Stopped()
            if st["status"] == "denied":
                results[call["id"]] = {"content": "The person denied this action. Do not run it again; continue "
                                                  "another way or ask them.", "error": True}
            elif st["status"] in ("approved", "queued"):
                results[call["id"]] = self.execute(st, call, ctx, approved_by=st.get("decided_by") or "auto")
        state_["results"] = results
        self.append_results(state_, calls)

    @staticmethod
    def append_results(state_: dict, calls: list[dict]) -> None:
        results = state_.pop("results", {})
        state_["transcript"].append({"role": "tool", "results": [
            {"id": c["id"], "name": c["name"], **results.get(c["id"], {"content": "not run", "error": True})}
            for c in calls]})


def _shown(name: str, args: dict) -> dict:
    """The call's input as the approval card shows it (long fields shortened; the spec is shown as a diff)."""
    out = {}
    for k, v in args.items():
        if k == "yaml":
            continue
        if isinstance(v, str) and len(v) > 4000:
            v = v[:4000] + f"\n... ({len(v):,} characters)"
        out[k] = v
    return out


def _live(cfg, pve, name: str, has_draft: bool) -> dict:
    """What the model should know beyond the spec: VM states and the last build / verification."""
    out: dict = {"draft": has_draft}
    if pve is not None:
        try:
            out["vms"] = {vm.host: vm.status for vm in range_vms(pve, name) if vm.host}
        except ValorError:
            pass
    ap = state.load(cfg, name, "apply") or {}
    if ap:
        out["last_build"] = {k: ap.get(k) for k in ("ok", "started", "error", "message")}
    vr = state.load(cfg, name, "verify") or {}
    if vr:
        out["last_verification"] = {"summary": vr.get("summary"),
                                    "failed_tests": [{k: t.get(k) for k in ("name", "target", "expect", "observed", "detail")}
                                                     for t in vr.get("tests", []) if not t.get("pass")][:20],
                                    "failed_controls": {h: [r.get("control") for r in rows if r.get("after") != "pass"]
                                                        for h, rows in (vr.get("baseline") or {}).items()
                                                        if any(r.get("after") != "pass" for r in rows)}}
    return out
