"""The chat panel on a range's map: Question (read-only answers), Plan (a proposed change as a draft plus VALOR's
plan) and Code (the YAML edited directly, shown as a diff). Conversations are kept per range; changes only ever
become drafts, built through plan -> approve."""

from __future__ import annotations

import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import ai, drafts, state
from ..cluster import range_vms
from ..errors import ValorError
from ..plan import make_plan
from ..spec import normalize, parse_spec
from ..validate import validate_cluster
from . import editing
from .builder import reference_for, run_ai, spec_check, start_ai
from .core import ApiError, Session, require
from .ranges import _cfg, _load, _name, _plan_hash

router = APIRouter(prefix="/api")
HISTORY = 20                                    # earlier turns the model sees


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["question", "plan", "code"]
    message: str = Field(min_length=1, max_length=8000)


def _row(r) -> dict:
    return {"id": r["id"], "ts": r["ts"], "username": r["username"], "role": r["role"], "mode": r["mode"],
            "content": r["content"], "meta": json.loads(r["meta"] or "{}")}


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


@router.get("/ranges/{name}/chat")
def history(name: str, request: Request, limit: int = Query(default=100, ge=1, le=500),
            s: Session = Depends(require("viewer"))) -> dict:
    rows = s.conn.execute("SELECT * FROM (SELECT * FROM range_chat WHERE range = ? ORDER BY id DESC LIMIT ?) "
                          "ORDER BY id", (_name(name), limit)).fetchall()
    return {"messages": [_row(r) for r in rows]}


@router.delete("/ranges/{name}/chat")
def clear(name: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    n = s.conn.execute("DELETE FROM range_chat WHERE range = ?", (_name(name),)).rowcount
    s.audit("range.chat.clear", target=name, detail={"messages": n})
    return {"ok": True}


@router.post("/ranges/{name}/chat")
def chat(name: str, body: ChatIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    _, saved_text = _load(cfg, _name(name))
    d = drafts.load(cfg, name)
    working = d[0] if d else saved_text
    prov, st, pve = start_ai(request, s)
    earlier = s.conn.execute("SELECT role, content FROM (SELECT * FROM range_chat WHERE range = ? ORDER BY id DESC "
                             "LIMIT ?) ORDER BY id", (name, HISTORY)).fetchall()
    msgs = [{"role": r["role"], "content": r["content"]} for r in earlier] + [{"role": "user", "content": body.message}]
    system = ai.range_prompt(body.mode, reference_for(cfg, pve), working, _live(cfg, pve, name, d is not None))
    if body.mode == "question":
        res = run_ai(s, st, lambda: ai.ask(prov, system, msgs))
    else:
        check = spec_check(cfg, pve, name)
        res = run_ai(s, st, lambda: ai.build(prov, system, msgs, check))

    meta: dict = {"usage": {"input_tokens": res["input_tokens"], "output_tokens": res["output_tokens"]}}
    plan = None
    if body.mode != "question":
        meta["problems"] = res["problems"]
        if not res["problems"]:
            meta.update(fixed=res["fixed"], warnings=check.warnings)
        if res["yaml"] and not res["problems"]:
            drafts.save(cfg, name, res["yaml"], s.username, f"chat:{body.mode}")
            v = editing.view(cfg, name)
            meta["draft"] = v["draft"]["summary"] if v["draft"] else None
            if body.mode == "code":
                meta["diff"] = v["draft"]["diff"] if v["draft"] else ""
            if body.mode == "plan" and pve is not None:
                spec = normalize(parse_spec(res["yaml"]), cfg.default_os)
                try:
                    if validate_cluster(pve, spec)["ok"]:
                        p = make_plan(pve, spec)
                        plan = {"summary": p["summary"], "plan_hash": _plan_hash(spec, p),
                                "actions": [{k: a.get(k) for k in ("host", "action", "reasons")} for a in p["actions"]
                                            if a["action"] not in ("keep", "restamp")]}
                        meta["plan"] = plan
                except ValorError as e:
                    meta["problems"] = [e.message]
    now = time.time()
    s.conn.execute("INSERT INTO range_chat(range, ts, username, role, mode, content, meta) VALUES (?, ?, ?, 'user', ?, ?, '{}')",
                   (name, now, s.username, body.mode, body.message))
    cur = s.conn.execute("INSERT INTO range_chat(range, ts, username, role, mode, content, meta) "
                         "VALUES (?, ?, ?, 'assistant', ?, ?, ?)",
                         (name, now, s.username, body.mode, res["reply"] or "(no explanation)", json.dumps(meta)))
    s.audit("range.chat", target=name, detail={"mode": body.mode, "draft": bool(meta.get("draft")),
                                               "tokens": res["input_tokens"] + res["output_tokens"]})
    reply = _row(s.conn.execute("SELECT * FROM range_chat WHERE id = ?", (cur.lastrowid,)).fetchone())
    v = editing.view(cfg, name)
    return {"message": reply, "draft": v["draft"], "topology": v["topology"], "plan": plan}
