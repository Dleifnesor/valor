"""The chats API: saved chats (the chat builder and the chat panel on a range's map), kept per user - owners see and
continue their chats, administrators can read everyone's - and handing a failed build to the assistant."""

from __future__ import annotations

import json
import re
import time
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ... import jobs
from ...errors import ValorError
from ...spec import normalize, parse_spec
from .. import topology
from ..builder import ai_settings, start_ai
from ..core import ApiError, Session, require
from ..ranges import _cfg, _load, _name
from . import autofix, turn

router = APIRouter(prefix="/api")

MODES = {"builder": ("build",), "range": ("question", "plan", "code")}


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["builder", "range"]
    range: str | None = Field(default=None, max_length=15)
    provider: str | None = Field(default=None, max_length=32)      # an AI provider's id (default: the default one)


class PatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=120)
    allow_all: bool | None = None
    provider: str | None = Field(default=None, max_length=32)      # "" = the default provider


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)
    mode: Literal["build", "question", "plan", "code"] = "build"


class FixIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str | None = Field(default=None, max_length=32)      # an AI provider's id (default: the default one)


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: dict[str, Literal["approve", "deny"]] = Field(max_length=64)
    allow_all: bool = False


# ---- Rows and access -------------------------------------------------------------------------------------------

def _chat_row(row, extra: dict | None = None) -> dict:
    """A chat as the API returns it (its internal state reduced to what the UI needs)."""
    chat_state = json.loads(row["state"] or "{}")
    return {"id": row["id"], "owner": row["owner"], "kind": row["kind"], "range": row["range"], "title": row["title"],
            "created": row["created"], "updated": row["updated"], "allow_all": bool(chat_state.get("allow_all")),
            "provider": chat_state.get("provider") or None, **(extra or {})}


def _known_provider(request: Request, s: Session, pid: str | None) -> None:
    if pid and pid not in {p["id"] for p in ai_settings(s.conn, request.app.state.box)["providers"]}:
        raise ApiError(400, "invalid_chat", "There is no such AI provider.")


def _msg_row(row) -> dict:
    return {"id": row["id"], "chat_id": row["chat_id"], "ts": row["ts"], "role": row["role"], "mode": row["mode"],
            "content": row["content"], "status": row["status"], "version": row["version"],
            "meta": json.loads(row["meta"] or "{}")}


def _get_chat(s: Session, chat_id: int, write: bool = False):
    """The chat if this user may see it (owners; administrators read everyone's) or change it (owners only).
    Someone else's chat answers 404, so ids don't reveal which chats exist."""
    row = s.conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    mine = row is not None and row["owner"].lower() == s.username.lower()
    if row is None or not (mine or (not write and s.user["role"] == "admin")):
        raise ApiError(404, "no_chat", "There is no such chat.")
    return row


# ---- API -------------------------------------------------------------------------------------------------------

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
    return {"chats": [_chat_row(row, {"messages": row["n"], "status": row["last"] or "done"}) for row in rows]}


@router.post("/chats")
def create_chat(body: ChatIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    if body.kind == "range":
        if not body.range:
            raise ApiError(400, "invalid_chat", "A range chat needs the range's name.")
        _load(_cfg(request), _name(body.range))
    _known_provider(request, s, body.provider)
    now = time.time()
    cur = s.conn.execute("INSERT INTO chats(owner, kind, range, created, updated, state) VALUES (?, ?, ?, ?, ?, ?)",
                         (s.username, body.kind, body.range if body.kind == "range" else None, now, now,
                          json.dumps({"provider": body.provider} if body.provider else {})))
    s.audit("chat.create", target=body.range or "builder", detail={"chat": cur.lastrowid})
    return _chat_row(s.conn.execute("SELECT * FROM chats WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.get("/chats/{chat_id}")
def get_chat(chat_id: int, request: Request, s: Session = Depends(require("viewer"))) -> dict:
    chat = _get_chat(s, chat_id)
    rows = s.conn.execute("SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()
    for row in rows:
        turn.mark_interrupted(s.conn, row)
    rows = s.conn.execute("SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()
    out = _chat_row(chat, {"spec": chat["spec"], "messages": [_msg_row(row) for row in rows],
                           "readonly": chat["owner"].lower() != s.username.lower()})
    if chat["kind"] == "builder" and chat["spec"]:
        try:
            out["topology"] = topology.build(normalize(parse_spec(chat["spec"]), _cfg(request).default_os))
        except ValorError:
            out["topology"] = None
    return out


@router.patch("/chats/{chat_id}")
def patch_chat(chat_id: int, body: PatchIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    chat = _get_chat(s, chat_id, write=True)
    chat_state = json.loads(chat["state"] or "{}")
    if body.provider is not None:
        _known_provider(request, s, body.provider)
        chat_state["provider"] = body.provider or None
    if body.allow_all is not None:
        chat_state["allow_all"] = body.allow_all
        s.audit("chat.allow_all", target=chat["range"] or "builder", detail={"chat": chat_id, "on": body.allow_all})
    title = body.title.strip() if body.title is not None else chat["title"]
    s.conn.execute("UPDATE chats SET title = ?, state = ? WHERE id = ?", (title, json.dumps(chat_state), chat_id))
    return _chat_row(s.conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone())


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: int, s: Session = Depends(require("operator"))) -> dict:
    chat = _get_chat(s, chat_id)
    if chat["owner"].lower() != s.username.lower() and s.user["role"] != "admin":
        raise ApiError(404, "no_chat", "There is no such chat.")
    running = s.conn.execute("SELECT id FROM chat_messages WHERE chat_id = ? AND status = 'running'",
                             (chat_id,)).fetchall()
    for row in running:
        turn.stop(row["id"])
    s.conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    s.audit("chat.delete", target=chat["range"] or "builder", detail={"chat": chat_id, "owner": chat["owner"]})
    return {"ok": True}


@router.get("/chats/{chat_id}/messages/{msg_id}")
def get_message(chat_id: int, msg_id: int, v: int = Query(default=-1), s: Session = Depends(require("viewer"))):
    """Poll an answer. v is the version the browser has: an unchanged answer comes back as a few bytes."""
    _get_chat(s, chat_id)
    row = s.conn.execute("SELECT * FROM chat_messages WHERE id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
    if row is None:
        raise ApiError(404, "no_message", "There is no such message.")
    turn.mark_interrupted(s.conn, row)
    row = s.conn.execute("SELECT * FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
    if row["version"] == v:
        return {"unchanged": True, "version": v, "status": row["status"]}
    return _msg_row(row)


@router.post("/chats/{chat_id}/messages")
def send(chat_id: int, body: MessageIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    chat = _get_chat(s, chat_id, write=True)
    if body.mode not in MODES[chat["kind"]]:
        raise ApiError(400, "invalid_mode", f"A {chat['kind']} chat has the modes {', '.join(MODES[chat['kind']])}.")
    # One answer at a time: a turn whose thread died (VALOR restarted) no longer counts as busy.
    busy = s.conn.execute("SELECT id FROM chat_messages WHERE chat_id = ? AND status IN ('running', 'waiting')",
                          (chat_id,)).fetchall()
    for row in busy:
        turn.mark_interrupted(s.conn, s.conn.execute("SELECT * FROM chat_messages WHERE id = ?", (row["id"],)).fetchone())
    if s.conn.execute("SELECT 1 FROM chat_messages WHERE chat_id = ? AND status IN ('running', 'waiting')",
                      (chat_id,)).fetchone():
        raise ApiError(409, "chat_busy", "The assistant is still working on this chat (or waiting for your approval).")
    if chat["kind"] == "range":
        _load(_cfg(request), _name(chat["range"]))
    start_ai(request, s, json.loads(chat["state"] or "{}").get("provider"))    # rate limit, provider, daily budget
    title = chat["title"] or re.sub(r"\s+", " ", body.message).strip()[:80]
    s.conn.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))
    s.audit("chat.message", target=chat["range"] or "builder", detail={"chat": chat_id, "mode": body.mode})
    user_msg_id, answer_id = turn.start(request.app, s.conn, chat_id, s.username, body.mode, body.message)
    rows = s.conn.execute("SELECT * FROM chat_messages WHERE id IN (?, ?) ORDER BY id",
                          (user_msg_id, answer_id)).fetchall()
    return {"messages": [_msg_row(row) for row in rows]}


@router.post("/chats/{chat_id}/messages/{msg_id}/decide")
def decide(chat_id: int, msg_id: int, body: DecideIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    chat = _get_chat(s, chat_id, write=True)
    row = s.conn.execute("SELECT * FROM chat_messages WHERE id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
    if row is None or row["status"] != "waiting":
        raise ApiError(409, "not_waiting", "This answer is not waiting for a decision.")
    meta = json.loads(row["meta"])
    pending = {step["id"] for step in meta["steps"] if step.get("type") == "tool" and step.get("status") == "pending"}
    for call_id in body.decisions:
        if call_id not in pending:
            raise ApiError(400, "invalid_decision", f"{call_id} is not waiting for a decision.")
    for step in meta["steps"]:
        if step.get("type") == "tool" and step["id"] in body.decisions:
            step["status"] = "approved" if body.decisions[step["id"]] == "approve" else "denied"
            step["decided_by"] = s.username
            s.audit("chat.decision", target=chat["range"] or "", detail={"chat": chat_id, "tool": step["name"],
                                                                        "summary": step.get("summary", "")[:300],
                                                                        "decision": body.decisions[step["id"]]})
    if body.allow_all:
        chat_state = json.loads(chat["state"] or "{}")
        chat_state["allow_all"] = True
        s.conn.execute("UPDATE chats SET state = ? WHERE id = ?", (json.dumps(chat_state), chat_id))
        s.audit("chat.allow_all", target=chat["range"] or "", detail={"chat": chat_id, "on": True})
    still_pending = any(step.get("type") == "tool" and step.get("status") == "pending" for step in meta["steps"])
    if still_pending:                   # the person may decide card by card; the turn goes on once all are decided
        s.conn.execute("UPDATE chat_messages SET meta = ?, version = version + 1 WHERE id = ?",
                       (json.dumps(meta), msg_id))
    else:
        start_ai(request, s)
        turn.resume(request.app, s.conn, chat_id, msg_id, s.username, meta)
    return _msg_row(s.conn.execute("SELECT * FROM chat_messages WHERE id = ?", (msg_id,)).fetchone())


@router.post("/chats/{chat_id}/messages/{msg_id}/stop")
def stop(chat_id: int, msg_id: int, s: Session = Depends(require("operator"))) -> dict:
    _get_chat(s, chat_id, write=True)
    row = s.conn.execute("SELECT * FROM chat_messages WHERE id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
    if row is None:
        raise ApiError(404, "no_message", "There is no such message.")
    if not turn.stop(msg_id) and row["status"] in ("running", "waiting"):     # no thread: mark it stopped here
        meta = json.loads(row["meta"])
        for step in meta["steps"]:
            if step.get("type") == "tool" and step.get("status") in ("pending", "queued", "approved"):
                step["status"] = "cancelled"
        s.conn.execute("UPDATE chat_messages SET status = 'stopped', meta = ?, version = version + 1, "
                       "content = CASE WHEN content = '' THEN '(stopped)' ELSE content END WHERE id = ?",
                       (json.dumps(meta), msg_id))
    return {"ok": True}


@router.post("/jobs/{job_id}/fix")
def fix_job(job_id: str, request: Request, body: FixIn = FixIn(), s: Session = Depends(require("operator"))) -> dict:
    """Hand a failed build to the assistant (or open the chat that already works on it)."""
    if not autofix.JOB_ID.fullmatch(job_id):
        raise ApiError(400, "invalid_job", "Invalid job id.")
    try:
        job = jobs.status(_cfg(request), job_id, tail=1)
    except ValorError:
        raise ApiError(404, "no_job", "There is no such job.")
    if job.get("kind") != "apply" or job.get("state") != "failed":
        raise ApiError(409, "not_failed", "Only a failed build can be handed to the assistant.")
    name = autofix.job_range(job)
    _load(_cfg(request), _name(name))
    for row in s.conn.execute("SELECT id, state FROM chats WHERE owner = ? AND kind = 'range' AND range = ?",
                              (s.username, name)).fetchall():
        if json.loads(row["state"] or "{}").get("job") == job_id:
            return {"chat": row["id"], "range": name}
    _known_provider(request, s, body.provider)
    start_ai(request, s, body.provider)          # rate limit, provider, daily budget
    mode = ai_settings(s.conn, request.app.state.box)["fix_mode"]
    return {"chat": autofix.start_fix(request.app, s.conn, job, s.username, False, body.provider, mode), "range": name}


@router.get("/jobs/{job_id}/fix")
def fix_chats(job_id: str, s: Session = Depends(require("viewer"))) -> dict:
    """The chats in which the assistant works on this build that the person may open."""
    if not autofix.JOB_ID.fullmatch(job_id):
        raise ApiError(400, "invalid_job", "Invalid job id.")
    rows = s.conn.execute("SELECT * FROM chats WHERE kind = 'range' AND state LIKE ?", (f'%"{job_id}"%',)).fetchall()
    visible = [row for row in rows if row["owner"].lower() == s.username.lower() or s.user["role"] == "admin"]
    return {"chats": [_chat_row(row) for row in visible]}
