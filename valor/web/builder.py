"""Chat builder API: AI provider settings (admin, key encrypted, never shown again), usage, and the chat itself
(operators). The model's spec is validated here; saving and building stay explicit user actions."""

from __future__ import annotations

import ipaddress
import re
import threading
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import ai
from ..cluster import load_catalog, templates, vlans_in_use
from ..errors import ValorError
from ..pve import PVE
from ..spec import normalize, parse_spec
from ..validate import validate_cluster
from . import db, topology
from .core import ApiError, Session, require
from .ranges import _cfg, role_list

router = APIRouter(prefix="/api")
MASK = "••••••••"
_rate: dict[str, list[float]] = {}
_rate_lock = threading.Lock()
RATE = (10, 60.0)                       # requests per window (seconds) and user


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AiSettingsIn(_In):
    provider: str = Field(pattern="^(none|anthropic|openai)$")
    model: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9._:/-]*$")
    base_url: str = Field(default="", max_length=512)
    api_key: str | None = Field(default=None, max_length=512)
    max_tokens: int = Field(default=ai.DEFAULTS["max_tokens"], ge=1024, le=64000)
    daily_tokens_per_user: int = Field(default=400_000, ge=0, le=100_000_000)
    timeout: int = Field(default=ai.DEFAULTS["timeout"], ge=30, le=900)


class Message(_In):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=20_000)


class ChatIn(_In):
    messages: list[Message] = Field(min_length=1, max_length=60)
    spec: str = Field(default="", max_length=256 * 1024)
    range: str = Field(default="", max_length=15)       # editing this existing range (its name is allowed)


def _settings(conn) -> dict:
    return {**ai.DEFAULTS, **db.get_setting(conn, "ai", {})}


def _masked(st: dict) -> dict:
    return {**st, "api_key": MASK if st.get("api_key") else ""}


def _check_url(url: str) -> None:
    if not url:
        return
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password:
        raise ApiError(400, "invalid_ai", "The base URL must be http(s)://host[:port]/path without credentials.")
    if u.scheme == "http":
        try:
            ip = ipaddress.ip_address(u.hostname)
        except ValueError:
            ip = None
        if not ip or not (ip.is_private or ip.is_loopback):
            raise ApiError(400, "invalid_ai", "Plain http:// is only allowed to a private IP address (e.g. a local "
                                              "model server); use https:// otherwise.")


def _provider(request: Request, conn) -> tuple[ai.Provider, dict]:
    st = _settings(conn)
    key = request.app.state.box.open_text(st["api_key"], "ai:api_key") if st.get("api_key") else ""
    return ai.provider_from(st, key), st


@router.get("/settings/ai")
def get_ai(s: Session = Depends(require("admin"))) -> dict:
    return {**_masked(_settings(s.conn)), "providers": list(ai.PROVIDERS), "anthropic_models": list(ai.ANTHROPIC_MODELS)}


@router.put("/settings/ai")
def put_ai(body: AiSettingsIn, request: Request, s: Session = Depends(require("admin"))) -> dict:
    old = _settings(s.conn)
    d = body.model_dump()
    _check_url(d["base_url"])
    if d["provider"] == "openai" and not d["base_url"]:
        raise ApiError(400, "invalid_ai", "An OpenAI-compatible provider needs its base URL (ending in /v1).")
    if d["provider"] != "none" and not d["model"]:
        d["model"] = ai.ANTHROPIC_MODELS[0] if d["provider"] == "anthropic" else ""
        if not d["model"]:
            raise ApiError(400, "invalid_ai", "Name the model to use.")
    key = d.pop("api_key")
    if key and key != MASK:
        d["api_key"] = request.app.state.box.seal_text(key, "ai:api_key")
    elif key == "" and old.get("api_key"):
        d["api_key"] = ""                                   # cleared on purpose
    else:
        d["api_key"] = old.get("api_key", "")
    if d["provider"] == "anthropic" and not d["api_key"]:
        raise ApiError(400, "invalid_ai", "Anthropic needs an API key.")
    db.put_setting(s.conn, "ai", d, by=s.username)
    s.audit("settings.ai", detail={"provider": d["provider"], "model": d["model"], "base_url": d["base_url"],
                                   "key_changed": bool(key and key != MASK)})
    return _masked(d)


@router.post("/settings/ai/test")
def test_ai(request: Request, s: Session = Depends(require("admin"))) -> dict:
    try:
        prov, st = _provider(request, s.conn)
        t0 = time.time()
        r = prov.complete("You are a connectivity check.", [{"role": "user", "content": "Reply with the word OK."}])
    except ValorError as e:
        s.audit("settings.ai.test", "fail", detail={"error": e.code})
        raise ApiError(409 if e.code == "ai_not_configured" else 502, e.code, e.message, hint=e.hint)
    _record(s, st, r.input_tokens, r.output_tokens, True)
    s.audit("settings.ai.test", detail={"model": st["model"]})
    return {"ok": True, "seconds": round(time.time() - t0, 1), "model": st["model"], "reply": r.text[:200]}


@router.get("/settings/ai/usage")
def usage(s: Session = Depends(require("admin"))) -> dict:
    day = time.time() - 86400
    month = time.time() - 30 * 86400
    rows = s.conn.execute(
        "SELECT username, COUNT(*) AS requests, SUM(input_tokens) AS input, SUM(output_tokens) AS output, "
        "SUM(CASE WHEN ts > ? THEN input_tokens + output_tokens ELSE 0 END) AS today "
        "FROM ai_usage WHERE ts > ? GROUP BY username ORDER BY username", (day, month)).fetchall()
    return {"users": [dict(r) for r in rows]}


def _record(s: Session, st: dict, tin: int, tout: int, ok: bool) -> None:
    s.conn.execute("INSERT INTO ai_usage(ts, username, provider, model, input_tokens, output_tokens, ok) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)", (time.time(), s.username, st["provider"], st.get("model", ""),
                                                    tin, tout, int(ok)))


@router.get("/builder")
def builder_info(s: Session = Depends(require("operator"))) -> dict:
    st = _settings(s.conn)
    return {"configured": st["provider"] != "none", "provider": st["provider"], "model": st["model"]}


def _facts(cfg, pve: PVE | None) -> dict:
    used_vlans, nets, names = {}, [], []
    for p in sorted(cfg.ranges_dir.glob("*.yaml")):
        try:
            sp = parse_spec(p.read_text())
        except Exception:
            continue
        names.append(sp.name)
        for seg in sp.segments:
            used_vlans[seg.vlan] = sp.name
            nets.append(str(seg.cidr))
    if pve is not None:
        try:
            used_vlans.update(vlans_in_use(pve))
        except ValorError:
            pass
    return {"vlan_range": [cfg.vlan_min, cfg.vlan_max], "vlans_in_use": sorted(used_vlans),
            "networks_in_use": nets, "reserved_networks": list(cfg.reserved_networks), "existing_ranges": names}


def start_ai(request: Request, s: Session):
    """Rate limit, provider and the user's daily budget, shared by every chat. Returns (provider, settings, pve)."""
    now = time.time()
    with _rate_lock:
        recent = [t for t in _rate.get(s.username, []) if now - t < RATE[1]]
        if len(recent) >= RATE[0]:
            raise ApiError(429, "rate_limited", "Too many AI requests; wait a minute.")
        _rate[s.username] = recent + [now]
    try:
        prov, st = _provider(request, s.conn)
    except ValorError as e:
        raise ApiError(409, e.code, e.message, hint=e.hint)
    limit = int(st.get("daily_tokens_per_user") or 0)
    if limit:
        spent = s.conn.execute("SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM ai_usage "
                               "WHERE username = ? AND ts > ?", (s.username, now - 86400)).fetchone()[0]
        if spent >= limit:
            raise ApiError(429, "ai_budget", f"You used your daily AI budget ({limit} tokens).")
    try:
        pve = PVE(_cfg(request))
    except ValorError:
        pve = None
    return prov, st, pve


def reference_for(cfg, pve: PVE | None) -> str:
    try:
        present = {k for k, v in templates(pve, load_catalog(cfg)).items() if v["present"]} if pve else set(load_catalog(cfg))
    except ValorError:
        present = set(load_catalog(cfg))
    return ai.reference(cfg, load_catalog(cfg), present, role_list(cfg), _facts(cfg, pve))


def spec_check(cfg, pve: PVE | None, own_name: str = ""):
    """The validation the model's specs must pass (schema, unique name, the cluster)."""
    def check(text: str) -> list[str]:
        try:
            spec = normalize(parse_spec(text), cfg.default_os)
        except ValorError as e:
            return [f"{d.get('location', '')}: {d.get('message', '')}" for d in (e.details or [])] or [e.message]
        problems = []
        if own_name and spec.name != own_name:
            problems.append(f"the range is called '{own_name}'; keep that name")
        if spec.name != own_name and (cfg.ranges_dir / f"{spec.name}.yaml").exists():
            problems.append(f"a range named '{spec.name}' already exists; choose another name")
        if pve is not None:
            try:
                v = validate_cluster(pve, spec)
                problems += [f"{e['location']}: {e['message']}" for e in v["errors"]]
            except ValorError as e:
                problems.append(e.message)
        return problems
    return check


def run_ai(s: Session, st: dict, fn):
    """Call the model (fn), record the tokens either way, and turn AI errors into API errors."""
    try:
        res = fn()
    except ValorError as e:
        used = e.details if isinstance(e.details, dict) else {}
        _record(s, st, int(used.get("input_tokens", 0)), int(used.get("output_tokens", 0)), False)
        status = 422 if e.code == "ai_truncated" else 502 if e.code.startswith("ai_") else 400
        raise ApiError(status, e.code, e.message, hint=e.hint)
    _record(s, st, res["input_tokens"], res["output_tokens"], not res.get("problems"))
    return res


@router.post("/builder/chat")
def chat(body: ChatIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    prov, st, pve = start_ai(request, s)
    system = ai.builder_prompt(reference_for(cfg, pve))
    msgs = [m.model_dump() for m in body.messages]
    if body.spec.strip() and msgs[-1]["role"] == "user":
        msgs[-1] = {"role": "user", "content": msgs[-1]["content"] +
                    f"\n\nThe current spec in the builder:\n```yaml\n{body.spec.strip()}\n```"}
    res = run_ai(s, st, lambda: ai.build(prov, system, msgs, spec_check(cfg, pve, body.range)))
    topo = None
    if res["yaml"] and not res["problems"]:
        topo = topology.build(normalize(parse_spec(res["yaml"]), cfg.default_os))
    s.audit("builder.chat", detail={"attempts": res["attempts"], "ok": not res["problems"],
                                    "tokens": res["input_tokens"] + res["output_tokens"]})
    return {"reply": res["reply"], "yaml": res["yaml"], "ok": not res["problems"], "problems": res["problems"],
            "attempts": res["attempts"], "topology": topo,
            "usage": {"input_tokens": res["input_tokens"], "output_tokens": res["output_tokens"]}}
