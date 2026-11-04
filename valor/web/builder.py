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
    saved = db.get_setting(conn, "ai", {}) or {}
    if "providers" in saved:
        return {"providers": saved["providers"], "default": saved.get("default", ""),
                "daily_tokens_per_user": saved.get("daily_tokens_per_user", ai.DEFAULTS["daily_tokens_per_user"]),
                "fix_failed_builds": saved.get("fix_failed_builds", True),
                "fix_provider": saved.get("fix_provider", ""), "fix_mode": saved.get("fix_mode", "auto")}

    # Settings from a version with a single provider: convert them once.
    new = {"providers": [], "default": "", "fix_failed_builds": True, "fix_provider": "", "fix_mode": "auto",
           "daily_tokens_per_user": saved.get("daily_tokens_per_user", ai.DEFAULTS["daily_tokens_per_user"])}
    if saved.get("provider", "none") != "none":
        pid = secrets.token_hex(6)
        key = ""
        if saved.get("api_key") and box is not None:        # re-sealed under the new provider's own context
            key = box.seal_text(box.open_text(saved["api_key"], "ai:api_key"), _key_context(pid))
        new["providers"].append({
            "id": pid, "name": {"openai": "Local model server"}.get(saved["provider"], saved["provider"]),
            "provider": saved["provider"], "model": saved.get("model", ""), "base_url": saved.get("base_url", ""),
            "api_key": key, "max_tokens": saved.get("max_tokens", ai.DEFAULTS["max_tokens"]),
            "timeout": saved.get("timeout", ai.DEFAULTS["timeout"])})
        new["default"] = pid
    if saved:
        db.put_setting(conn, "ai", new, by="migration")
    return new


def resolve(conn, box, pid: str | None = None) -> tuple[ai.Provider, dict]:
    """The provider to use (a chat's choice, else the default; a deleted choice falls back to the default) and its
    settings with the daily budget."""
    store = ai_settings(conn, box)
    by_id = {provider["id"]: provider for provider in store["providers"]}
    chosen = by_id.get(pid or "") or by_id.get(store["default"])
    if chosen is None:
        raise ValorError("ai_not_configured", "No AI provider is connected.",
                         hint="An administrator can connect one in Settings -> AI providers.")
    key = box.open_text(chosen["api_key"], _key_context(chosen["id"])) if chosen.get("api_key") and box is not None else ""
    settings = {**ai.DEFAULTS, **{k: v for k, v in chosen.items() if k != "api_key"},
                "daily_tokens_per_user": store["daily_tokens_per_user"]}
    return ai.provider_from(settings, key), settings


def _masked(store: dict) -> dict:
    """The settings for the admin page: a stored key shows as MASK (sending MASK back keeps it)."""
    return {"providers": [{**p, "api_key": MASK if p.get("api_key") else "", "default": p["id"] == store["default"]}
                          for p in store["providers"]],
            "daily_tokens_per_user": store["daily_tokens_per_user"], "fix_failed_builds": store["fix_failed_builds"],
            "fix_provider": store["fix_provider"], "fix_mode": store["fix_mode"]}


def _check_url(url: str) -> None:
    """A model server URL: http(s) without credentials; plain http only on a private address (the API key would
    otherwise cross the internet unencrypted)."""
    if not url:
        return
    parts = urlparse(url)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
        raise ApiError(400, "invalid_ai", "The base URL must be http(s)://host[:port]/path without credentials.")
    if parts.scheme == "http":
        try:
            ip = ipaddress.ip_address(parts.hostname)
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


def _validate_provider(provider: dict) -> None:
    name = provider["name"]
    if provider["provider"] == "openai":
        _check_url(provider["base_url"])
        if not provider["base_url"]:
            raise ApiError(400, "invalid_ai",
                           f"{name}: an OpenAI-compatible provider needs its base URL (ending in /v1).")
    else:
        provider["base_url"] = ""                           # OpenRouter has a fixed address
    if not provider["model"]:
        raise ApiError(400, "invalid_ai", f"{name}: name the model to use.")
    if provider["provider"] == "openrouter" and not provider["api_key"]:
        raise ApiError(400, "invalid_ai", f"{name}: {ai.KIND_NAMES[provider['provider']]} needs an API key.")


@router.put("/settings/ai")
def put_ai(body: AiSettingsIn, request: Request, s: Session = Depends(require("admin"))) -> dict:
    box = request.app.state.box
    old_store = ai_settings(s.conn, box)
    old = {p["id"]: p for p in old_store["providers"]}
    names = [p.name.strip().lower() for p in body.providers]
    if len(set(names)) != len(names):
        raise ApiError(400, "invalid_ai", "Give every provider its own name.")
    new, default, changed = [], "", []
    for submitted in body.providers:
        provider = submitted.model_dump()
        provider["name"] = provider["name"].strip()
        provider["id"] = provider["id"] if provider["id"] in old else secrets.token_hex(6)
        key, is_default = provider.pop("api_key"), provider.pop("default")
        previous = old.get(provider["id"], {})
        if key and key != MASK:
            provider["api_key"] = box.seal_text(key, _key_context(provider["id"]))
            changed.append(provider["name"])
        elif key == MASK and previous.get("provider") == provider["provider"]:
            provider["api_key"] = previous.get("api_key", "")   # kept (encrypted)
        else:
            provider["api_key"] = ""                        # cleared, or a new kind of provider without one
        _validate_provider(provider)
        if is_default and not default:
            default = provider["id"]
        new.append(provider)
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
        provider, settings = _provider(request, s.conn, body.id)
        started = time.time()
        reply = provider.complete("You are a connectivity check.",
                                  [{"role": "user", "content": "Reply with the word OK."}])
    except ValorError as e:
        s.audit("settings.ai.test", "fail", target=next(p["name"] for p in store["providers"] if p["id"] == body.id),
                detail={"error": e.code})
        raise ApiError(409 if e.code == "ai_not_configured" else 502, e.code, e.message, hint=e.hint)
    _record(s, settings, reply.input_tokens, reply.output_tokens, True)
    s.audit("settings.ai.test", target=settings["name"], detail={"model": settings["model"]})
    cfg = _cfg(request)
    try:
        pve = PVE(cfg)
    except ValorError:
        pve = None
    # Warn when the model's context window can't hold VALOR's prompt plus an answer (local models often load small).
    prompt = len(ai.builder_prompt(reference_for(cfg, pve))) // 4          # ~4 characters per token
    context = provider.context_length()
    need = prompt + int(settings.get("max_tokens") or ai.DEFAULTS["max_tokens"]) // 2
    advice = (f"The model is loaded with a {context:,}-token context, but VALOR's prompt alone is about {prompt:,} "
              f"tokens and answers need room too: load it with a Context Length of at least {max(32768, need):,}."
              if context and context < need else "")
    return {"ok": True, "seconds": round(time.time() - started, 1), "model": settings["model"],
            "reply": reply.text[:200], "context_length": context, "prompt_tokens": prompt, "advice": advice}


@router.get("/settings/ai/usage")
def usage(s: Session = Depends(require("admin"))) -> dict:
    day = time.time() - 86400
    month = time.time() - 30 * 86400
    rows = s.conn.execute(
        "SELECT username, COUNT(*) AS requests, SUM(input_tokens) AS input, SUM(output_tokens) AS output, "
        "SUM(CASE WHEN ts > ? THEN input_tokens + output_tokens ELSE 0 END) AS today "
        "FROM ai_usage WHERE ts > ? GROUP BY username ORDER BY username", (day, month)).fetchall()
    return {"users": [dict(row) for row in rows]}


def _record(s: Session, settings: dict, input_tokens: int, output_tokens: int, ok: bool) -> None:
    record_usage(s.conn, s.username, settings, input_tokens, output_tokens, ok)


def record_usage(conn, username: str, settings: dict, input_tokens: int, output_tokens: int, ok: bool) -> None:
    """One model call's tokens, for the daily budget and the usage page."""
    conn.execute("INSERT INTO ai_usage(ts, username, provider, model, input_tokens, output_tokens, ok) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (time.time(), username, settings["provider"],
                                                  settings.get("model", ""), input_tokens, output_tokens, int(ok)))


@router.get("/builder")
def builder_info(request: Request, s: Session = Depends(require("operator"))) -> dict:
    """What the chats may use (no secrets): every provider and the default."""
    store = ai_settings(s.conn, request.app.state.box)
    providers = [{k: p[k] for k in ("id", "name", "provider", "model")} for p in store["providers"]]
    return {"configured": bool(providers), "default": store["default"], "providers": providers}


def _facts(cfg, pve: PVE | None) -> dict:
    """Cluster facts for the prompt: which VLANs, networks and range names are taken."""
    used_vlans, nets, names = {}, [], []
    for path in sorted(cfg.ranges_dir.glob("*.yaml")):
        try:
            spec = parse_spec(path.read_text())
        except Exception:
            continue
        names.append(spec.name)
        for segment in spec.segments:
            used_vlans[segment.vlan] = spec.name
            nets.append(str(segment.cidr))
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
        recent = [stamp for stamp in _rate.get(s.username, []) if now - stamp < RATE[1]]
        if len(recent) >= RATE[0]:
            raise ApiError(429, "rate_limited", "Too many AI requests; wait a minute.")
        _rate[s.username] = recent + [now]
    try:
        provider, settings = _provider(request, s.conn, pid)
    except ValorError as e:
        raise ApiError(409, e.code, e.message, hint=e.hint)
    limit = int(settings.get("daily_tokens_per_user") or 0)
    if limit:
        spent = s.conn.execute("SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM ai_usage "
                               "WHERE username = ? AND ts > ?", (s.username, now - 86400)).fetchone()[0]
        if spent >= limit:
            raise ApiError(429, "ai_budget", f"You used your daily AI budget ({limit} tokens).")
    try:
        pve = PVE(_cfg(request))
    except ValorError:
        pve = None
    return provider, settings, pve


def reference_for(cfg, pve: PVE | None) -> str:
    """The prompt's reference part for this cluster. Without a cluster connection every catalog OS is listed."""
    try:
        if pve:
            present = {name for name, template in templates(pve, load_catalog(cfg)).items() if template["present"]}
        else:
            present = set(load_catalog(cfg))
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
            return [f"{problem.get('location', '')}: {problem.get('message', '')}"
                    for problem in (e.details or [])] or [e.message]
        problems = []
        if own_name and spec.name != own_name:
            problems.append(f"the range is called '{own_name}'; keep that name")
        if spec.name != own_name and (cfg.ranges_dir / f"{spec.name}.yaml").exists():
            problems.append(f"a range named '{spec.name}' already exists; choose another name")
        if pve is not None:
            try:
                checks = validate_cluster(pve, spec)
                problems += [f"{e['location']}: {e['message']}" + (f" ({e['hint']})" if e.get("hint") else "")
                             for e in checks["errors"]]
                check.warnings = [warning["message"] for warning in checks.get("warnings", [])]
            except ValorError as e:
                problems.append(e.message)
        elif spec.baseline == "none":
            check.warnings = [NO_BASELINE]
        return problems
    check.warnings = []
    return check
