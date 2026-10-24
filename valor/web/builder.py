"""AI settings API and what every chat shares: provider settings (admin, key encrypted, never shown again), usage,
the rate limit and daily budget, the prompt's cluster reference and the spec check the model's specs must pass."""

from __future__ import annotations

import ipaddress
import secrets
import threading
import time
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import ai
from ..cluster import load_catalog, templates, vlans_in_use
from ..errors import ValorError
from ..pve import PVE
from ..spec import normalize, parse_spec
from ..validate import NO_BASELINE, validate_cluster
from . import db
from .core import ApiError, Session, require
from .ranges import _cfg, role_list

router = APIRouter(prefix="/api")
MASK = "••••••••"
_rate: dict[str, list[float]] = {}
_rate_lock = threading.Lock()
RATE = (10, 60.0)                       # requests per window (seconds) and user


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderIn(_In):
    id: str | None = Field(default=None, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    provider: Literal["openai", "openrouter"]
    model: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9._:/-]*$")
    base_url: str = Field(default="", max_length=512)
    api_key: str | None = Field(default=None, max_length=512)
    max_tokens: int = Field(default=ai.DEFAULTS["max_tokens"], ge=1024, le=128000)
    timeout: int = Field(default=ai.DEFAULTS["timeout"], ge=30, le=900)
    default: bool = False


class AiSettingsIn(_In):
    providers: list[ProviderIn] = Field(max_length=20)
    daily_tokens_per_user: int = Field(default=ai.DEFAULTS["daily_tokens_per_user"], ge=0, le=100_000_000)
    fix_failed_builds: bool = True              # a failed build starts the assistant on it (Code mode)
    fix_provider: str = Field(default="", max_length=32)    # with this provider ("" = the default one)
    fix_mode: Literal["auto", "fix", "prepare"] = "auto"    # fix and rebuild / fix / prepare, the person approves


class TestIn(_In):
    id: str = Field(max_length=32)


def _key_context(pid: str) -> str:
    return f"ai:{pid}:api_key"


def ai_settings(conn, box) -> dict:
    """{providers: [{id, name, provider, model, base_url, api_key (encrypted), max_tokens, timeout}], default (an id),
    daily_tokens_per_user}. The single provider of earlier versions becomes the first named one (its key re-sealed)."""
    st = db.get_setting(conn, "ai", {}) or {}
    if "providers" in st:
        return {"providers": st["providers"], "default": st.get("default", ""),
                "daily_tokens_per_user": st.get("daily_tokens_per_user", ai.DEFAULTS["daily_tokens_per_user"]),
                "fix_failed_builds": st.get("fix_failed_builds", True), "fix_provider": st.get("fix_provider", ""),
                "fix_mode": st.get("fix_mode", "auto")}
    new = {"providers": [], "default": "", "fix_failed_builds": True, "fix_provider": "", "fix_mode": "auto",
           "daily_tokens_per_user": st.get("daily_tokens_per_user", ai.DEFAULTS["daily_tokens_per_user"])}
    if st.get("provider", "none") != "none":
        pid = secrets.token_hex(6)
        key = ""
        if st.get("api_key") and box is not None:
            key = box.seal_text(box.open_text(st["api_key"], "ai:api_key"), _key_context(pid))
        new["providers"].append({"id": pid, "name": {"openai": "Local model server"}.get(st["provider"], st["provider"]), "provider": st["provider"], "model": st.get("model", ""),
            "base_url": st.get("base_url", ""), "api_key": key,
            "max_tokens": st.get("max_tokens", ai.DEFAULTS["max_tokens"]), "timeout": st.get("timeout", ai.DEFAULTS["timeout"])})
        new["default"] = pid
    if st:
        db.put_setting(conn, "ai", new, by="migration")
    return new


def resolve(conn, box, pid: str | None = None) -> tuple[ai.Provider, dict]:
    """The provider to use (a chat's choice, else the default; a deleted choice falls back to the default) and its
    settings with the daily budget."""
    store = ai_settings(conn, box)
    by_id = {p["id"]: p for p in store["providers"]}
    p = by_id.get(pid or "") or by_id.get(store["default"])
    if p is None:
        raise ValorError("ai_not_configured", "No AI provider is connected.",
                         hint="An administrator can connect one in Settings -> AI providers.")
    key = box.open_text(p["api_key"], _key_context(p["id"])) if p.get("api_key") and box is not None else ""
    st = {**ai.DEFAULTS, **{k: v for k, v in p.items() if k != "api_key"},
          "daily_tokens_per_user": store["daily_tokens_per_user"]}
    return ai.provider_from(st, key), st


def _masked(store: dict) -> dict:
    return {"providers": [{**p, "api_key": MASK if p.get("api_key") else "", "default": p["id"] == store["default"]}
                          for p in store["providers"]],
            "daily_tokens_per_user": store["daily_tokens_per_user"], "fix_failed_builds": store["fix_failed_builds"],
            "fix_provider": store["fix_provider"], "fix_mode": store["fix_mode"]}


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


def _provider(request: Request, conn, pid: str | None = None) -> tuple[ai.Provider, dict]:
    return resolve(conn, request.app.state.box, pid)


@router.get("/settings/ai")
def get_ai(request: Request, s: Session = Depends(require("admin"))) -> dict:
    return {**_masked(ai_settings(s.conn, request.app.state.box)), "kinds": list(ai.PROVIDERS)}


def _validate_provider(d: dict) -> None:
    name = d["name"]
    if d["provider"] == "openai":
        _check_url(d["base_url"])
        if not d["base_url"]:
            raise ApiError(400, "invalid_ai", f"{name}: an OpenAI-compatible provider needs its base URL (ending in /v1).")
    else:
        d["base_url"] = ""                                  # OpenRouter has a fixed address
    if not d["model"]:
        raise ApiError(400, "invalid_ai", f"{name}: name the model to use.")
    if d["provider"] == "openrouter" and not d["api_key"]:
        raise ApiError(400, "invalid_ai", f"{name}: {ai.KIND_NAMES[d['provider']]} needs an API key.")


@router.put("/settings/ai")
def put_ai(body: AiSettingsIn, request: Request, s: Session = Depends(require("admin"))) -> dict:
    box = request.app.state.box
    old_store = ai_settings(s.conn, box)
    old = {p["id"]: p for p in old_store["providers"]}
    names = [p.name.strip().lower() for p in body.providers]
    if len(set(names)) != len(names):
        raise ApiError(400, "invalid_ai", "Give every provider its own name.")
    new, default, changed = [], "", []
    for p in body.providers:
        d = p.model_dump()
        d["name"] = d["name"].strip()
        d["id"] = d["id"] if d["id"] in old else secrets.token_hex(6)
        key, is_default = d.pop("api_key"), d.pop("default")
        prev = old.get(d["id"], {})
        if key and key != MASK:
            d["api_key"] = box.seal_text(key, _key_context(d["id"]))
            changed.append(d["name"])
        elif key == MASK and prev.get("provider") == d["provider"]:
            d["api_key"] = prev.get("api_key", "")          # kept (encrypted)
        else:
            d["api_key"] = ""                               # cleared, or a new kind of provider without one
        _validate_provider(d)
        if is_default and not default:
            default = d["id"]
        new.append(d)
    store = {"providers": new, "default": default or (new[0]["id"] if new else ""),
             "daily_tokens_per_user": body.daily_tokens_per_user, "fix_failed_builds": body.fix_failed_builds,
             # a removed provider: failed builds go to the default one again
             "fix_provider": body.fix_provider if body.fix_provider in {p["id"] for p in new} else "",
             "fix_mode": body.fix_mode}
    db.put_setting(s.conn, "ai", store, by=s.username)
    s.audit("settings.ai", detail={"providers": [f"{p['provider']}:{p['name']}:{p['model']}" for p in new],
                                   "default": next((p["name"] for p in new if p["id"] == store["default"]), ""),
                                   "keys_changed": changed, "daily_tokens_per_user": body.daily_tokens_per_user,
                                   "fix_failed_builds": body.fix_failed_builds, "fix_provider": store["fix_provider"],
                                   "fix_mode": body.fix_mode})
    return _masked(store)


@router.post("/settings/ai/test")
def test_ai(body: TestIn, request: Request, s: Session = Depends(require("admin"))) -> dict:
    store = ai_settings(s.conn, request.app.state.box)
    if body.id not in {p["id"] for p in store["providers"]}:
        raise ApiError(404, "not_found", "No such provider (save the settings first).")
    try:
        prov, st = _provider(request, s.conn, body.id)
        t0 = time.time()
        r = prov.complete("You are a connectivity check.", [{"role": "user", "content": "Reply with the word OK."}])
    except ValorError as e:
        s.audit("settings.ai.test", "fail", target=next(p["name"] for p in store["providers"] if p["id"] == body.id),
                detail={"error": e.code})
        raise ApiError(409 if e.code == "ai_not_configured" else 502, e.code, e.message, hint=e.hint)
    _record(s, st, r.input_tokens, r.output_tokens, True)
    s.audit("settings.ai.test", target=st["name"], detail={"model": st["model"]})
    cfg = _cfg(request)
    try:
        pve = PVE(cfg)
    except ValorError:
        pve = None
    prompt = len(ai.builder_prompt(reference_for(cfg, pve))) // 4          # ~4 characters per token
    ctx = prov.context_length()
    need = prompt + int(st.get("max_tokens") or ai.DEFAULTS["max_tokens"]) // 2
    advice = (f"The model is loaded with a {ctx:,}-token context, but VALOR's prompt alone is about {prompt:,} tokens "
              f"and answers need room too: load it with a Context Length of at least {max(32768, need):,}."
              if ctx and ctx < need else "")
    return {"ok": True, "seconds": round(time.time() - t0, 1), "model": st["model"], "reply": r.text[:200],
            "context_length": ctx, "prompt_tokens": prompt, "advice": advice}


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
    record_usage(s.conn, s.username, st, tin, tout, ok)


def record_usage(conn, username: str, st: dict, tin: int, tout: int, ok: bool) -> None:
    conn.execute("INSERT INTO ai_usage(ts, username, provider, model, input_tokens, output_tokens, ok) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (time.time(), username, st["provider"], st.get("model", ""),
                                                  tin, tout, int(ok)))


@router.get("/builder")
def builder_info(request: Request, s: Session = Depends(require("operator"))) -> dict:
    """What the chats may use (no secrets): every provider and the default."""
    store = ai_settings(s.conn, request.app.state.box)
    providers = [{k: p[k] for k in ("id", "name", "provider", "model")} for p in store["providers"]]
    return {"configured": bool(providers), "default": store["default"], "providers": providers}


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


def start_ai(request: Request, s: Session, pid: str | None = None):
    """Rate limit, provider (pid: the chat's choice) and the user's daily budget, shared by every chat.
    Returns (provider, settings, pve)."""
    now = time.time()
    with _rate_lock:
        recent = [t for t in _rate.get(s.username, []) if now - t < RATE[1]]
        if len(recent) >= RATE[0]:
            raise ApiError(429, "rate_limited", "Too many AI requests; wait a minute.")
        _rate[s.username] = recent + [now]
    try:
        prov, st = _provider(request, s.conn, pid)
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
        check.warnings = []
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
                problems += [f"{e['location']}: {e['message']}" + (f" ({e['hint']})" if e.get("hint") else "")
                             for e in v["errors"]]
                check.warnings = [w["message"] for w in v.get("warnings", [])]
            except ValorError as e:
                problems.append(e.message)
        elif spec.baseline == "none":
            check.warnings = [NO_BASELINE]
        return problems
    check.warnings = []
    return check
