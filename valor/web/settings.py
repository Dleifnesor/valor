# *tweaks the little control panel* OwO valor/web/settings.py is the admin settings API! ^w^
#   notification channels  email / Discord / Slack / Teams: list, save (secrets sealed), send a test
#   LDAP                   the directory sign-in settings: show (password masked), save (validated), test
#   audit log              search and page through who did what
#   notifications          each person's in-app list and marking items read
# *wags tail*
"""Admin settings (notification channels, LDAP), the audit log and per-user in-app notifications."""

# Lazy type hints UwU
from __future__ import annotations

# copy (deep copies for masking), secrets (channel ids), time, Literal >w<
import copy
import secrets
import time
from typing import Literal

# FastAPI router/Depends/Query/Request; pydantic models OwO
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

# db, ldapauth, notify; core helpers ^w^
from . import db, ldapauth, notify
from .core import ApiError, Session, require

# All routes under /api. MASK is what the UI sees instead of a stored secret UwU
router = APIRouter(prefix="/api", tags=["settings"])
MASK = "********"


# _In: request bodies refuse unknown fields >w<
class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- Notification channels -------------------------------------------------------------------------------------

# ChannelIn: one channel: id (None = new), type, name, enabled, which events (problems only or all), and its
# type-specific config (host/port/from/to for email, url for webhooks) OwO
class ChannelIn(_In):
    id: str | None = Field(default=None, max_length=32)
    type: Literal["email", "discord", "slack", "teams"]
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    events: Literal["problems", "all"] = "problems"
    config: dict


# ChannelsIn: the whole list (at most 20); TestIn: which channel to test ^w^
class ChannelsIn(_In):
    channels: list[ChannelIn] = Field(max_length=20)


class TestIn(_In):
    id: str = Field(max_length=32)


# _validate_channel: email needs host, from and to, and a valid security mode; webhooks need an https:// URL (or
# the MASK, meaning "unchanged") UwU
def _validate_channel(channel: dict) -> None:
    config = channel["config"]
    if channel["type"] == "email":
        for key in ("host", "from", "to"):
            if not config.get(key):
                raise ApiError(400, "invalid_channel", f"{channel['name']}: '{key}' is required for email.")
        if config.get("security", "starttls") not in ("starttls", "tls"):
            raise ApiError(400, "invalid_channel", f"{channel['name']}: security must be starttls or tls.")
    elif not str(config.get("url", "")).startswith("https://") and config.get("url") != MASK:
        raise ApiError(400, "invalid_channel", f"{channel['name']}: the webhook URL must start with https://")


# _masked: a copy of the channels with their secrets replaced by MASK, so secrets never go back to the browser >w<
def _masked(channels: list) -> list:
    """Channels for the admin page: secrets (SMTP password, webhook URLs) show as MASK."""
    out = copy.deepcopy(channels)
    for channel in out:
        for field in notify.SECRET_FIELDS.get(channel["type"], ()):
            if channel["config"].get(field):
                channel["config"][field] = MASK
    return out


# GET /api/settings/notifications: the channels (masked); admins only OwO
@router.get("/settings/notifications")
def get_channels(s: Session = Depends(require("admin"))) -> dict:
    return {"channels": _masked(db.get_setting(s.conn, "notification_channels", []))}


# PUT /api/settings/notifications: save the whole list ^w^
@router.put("/settings/notifications")
def put_channels(body: ChannelsIn, request: Request, s: Session = Depends(require("admin"))) -> dict:
    box = request.app.state.box
    # The stored channels, by id UwU
    old = {channel["id"]: channel for channel in db.get_setting(s.conn, "notification_channels", [])}
    new = []
    for submitted in body.channels:
        # Keep an existing channel's id; give new ones a random id >w<
        channel = submitted.model_dump()
        channel["id"] = channel["id"] if channel["id"] in old else secrets.token_hex(6)
        _validate_channel(channel)
        # For each secret field: MASK (or an empty password) = keep the stored sealed value (if the type didn't change);
        # a new value = seal it with the context "notify:<id>:<field>" OwO
        for field in notify.SECRET_FIELDS[channel["type"]]:
            value = channel["config"].get(field)
            if value == MASK or (value in (None, "") and field == "password"):
                # unchanged (or an empty password field): keep the stored, encrypted value
                previous = old.get(channel["id"], {}).get("config", {}).get(field)
                if previous and old[channel["id"]]["type"] == channel["type"]:
                    channel["config"][field] = previous
                else:
                    channel["config"].pop(field, None)
            elif value:
                channel["config"][field] = box.seal_text(value, f"notify:{channel['id']}:{field}")
        new.append(channel)
    # Save, audit (types and names only, never secrets) and return the masked list ^w^
    db.put_setting(s.conn, "notification_channels", new, by=s.username)
    s.audit("settings.notifications", detail={"channels": [f"{c['type']}:{c['name']}" for c in new]})
    return {"channels": _masked(new)}


# POST /api/settings/notifications/test: send a test message through one saved channel; the result (and any error)
# is shown and audited UwU
@router.post("/settings/notifications/test")
def test_channel(body: TestIn, request: Request, s: Session = Depends(require("admin"))) -> dict:
    stored = db.get_setting(s.conn, "notification_channels", [])
    plain = [c for c in notify.channels_plain(stored, request.app.state.box) if c["id"] == body.id]
    if not plain:
        raise ApiError(404, "not_found", "No such channel (save the settings first).")
    try:
        notify.send(plain[0], "info", "Test notification",
                    f"Sent by {s.username} from the VALOR settings page.")
    except Exception as e:
        s.audit("settings.notifications.test", "fail", target=plain[0]["name"], detail=str(e))
        return {"ok": False, "message": f"{e.__class__.__name__}: {e}"}
    s.audit("settings.notifications.test", target=plain[0]["name"])
    return {"ok": True, "message": "Sent."}


# ---- LDAP / Active Directory -----------------------------------------------------------------------------------

# LdapIn: every LDAP setting with limits (see ldapauth.DEFAULTS for what each means) >w<
class LdapIn(_In):
    enabled: bool = False
    url: str = Field(default="", max_length=512)
    starttls: bool = False
    ca_pem: str = Field(default="", max_length=20000)
    bind_dn: str = Field(default="", max_length=512)
    bind_password: str | None = Field(default=None, max_length=1024)
    user_base: str = Field(default="", max_length=512)
    user_filter: str = Field(default=ldapauth.DEFAULTS["user_filter"], max_length=512)
    attr_display_name: str = Field(default="displayName", max_length=64)
    attr_email: str = Field(default="mail", max_length=64)
    group_admin: str = Field(default="", max_length=512)
    group_operator: str = Field(default="", max_length=512)
    group_viewer: str = Field(default="", max_length=512)
    nested_groups: bool = True
    timeout: int = Field(default=8, ge=1, le=60)


# LdapTestIn: an optional username to look up during the test OwO
class LdapTestIn(_In):
    username: str = Field(default="", max_length=128)


# _ldap_masked: the settings over the defaults, with the bind password shown only as MASK (or empty) ^w^
def _ldap_masked(stored: dict) -> dict:
    out = {**ldapauth.DEFAULTS, **stored}
    out["bind_password"] = MASK if stored.get("bind_password") else ""
    return out


# GET /api/settings/ldap UwU
@router.get("/settings/ldap")
def get_ldap(s: Session = Depends(require("admin"))) -> dict:
    return _ldap_masked(db.get_setting(s.conn, "ldap", {}))


# PUT /api/settings/ldap: save the directory settings >w<
@router.put("/settings/ldap")
def put_ldap(body: LdapIn, request: Request, s: Session = Depends(require("admin"))) -> dict:
    old = db.get_setting(s.conn, "ldap", {})
    settings = body.model_dump()
    # When enabled: an ldap(s):// URL, never plain LDAP without StartTLS, a filter containing {username}, and at least
    # one group mapped to a role OwO
    if settings["enabled"]:
        if not settings["url"].startswith(("ldaps://", "ldap://")):
            raise ApiError(400, "invalid_ldap", "The URL must start with ldaps:// or ldap://.")
        if settings["url"].startswith("ldap://") and not settings["starttls"]:
            raise ApiError(400, "invalid_ldap", "Plain LDAP is not allowed: use ldaps:// or turn on StartTLS.")
        if "{username}" not in settings["user_filter"]:
            raise ApiError(400, "invalid_ldap", "The user filter must contain {username}.")
        if not any(settings[f"group_{role}"] for role in ("admin", "operator", "viewer")):
            raise ApiError(400, "invalid_ldap", "Map at least one directory group to a VALOR role.")
    # A new password is sealed; MASK/empty keeps the stored one ^w^
    password = settings.pop("bind_password")
    if password and password != MASK:
        settings["bind_password"] = request.app.state.box.seal_text(password, "ldap:bind_password")
    elif old.get("bind_password"):
        settings["bind_password"] = old["bind_password"]         # unchanged: keep the stored, encrypted one
    # Save, audit (enabled + URL only) and return the masked settings UwU
    db.put_setting(s.conn, "ldap", settings, by=s.username)
    s.audit("settings.ldap", detail={"enabled": settings["enabled"], "url": settings["url"]})
    return _ldap_masked(settings)


# POST /api/settings/ldap/test: try the saved settings (and optionally look a user up) >w<
@router.post("/settings/ldap/test")
def test_ldap(body: LdapTestIn, request: Request, s: Session = Depends(require("admin"))) -> dict:
    cfg = ldapauth.settings_plain(db.get_setting(s.conn, "ldap", {}), request.app.state.box)
    if not cfg["url"]:
        raise ApiError(400, "invalid_ldap", "Save the directory settings first.")
    result = ldapauth.test_connection(cfg, body.username.strip())
    s.audit("settings.ldap.test", "ok" if result["ok"] else "fail", detail=result["message"])
    return result


# ---- Audit log -------------------------------------------------------------------------------------------------

# GET /api/audit: the audit log, newest first, searchable (q matches action, user, target or detail) and paged
# with `before` (the id to continue below) OwO
@router.get("/audit")
def audit_log(s: Session = Depends(require("admin")), before: int | None = Query(default=None),
              limit: int = Query(default=100, ge=1, le=500), q: str = Query(default="", max_length=100)) -> dict:
    # Build the SQL step by step; every value goes in as a ? parameter, never pasted into the SQL text ^w^
    sql, args = "SELECT * FROM audit WHERE 1=1", []
    if before:
        sql += " AND id < ?"
        args.append(before)
    if q:
        sql += " AND (action LIKE ? OR username LIKE ? OR target LIKE ? OR detail LIKE ?)"
        args += [f"%{q}%"] * 4
    rows = [dict(row) for row in s.conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]
    # `next` = the id to pass as `before` for the next page (None on the last page) UwU
    return {"entries": rows, "next": rows[-1]["id"] if len(rows) == limit else None}   # next: id to page from


# ---- In-app notifications --------------------------------------------------------------------------------------

# GET /api/notifications: my in-app notifications (only from a day before my account was created onward), with a
# "read" flag for each and the unread count >w<
@router.get("/notifications")
def my_notifications(s: Session = Depends(require("viewer")), limit: int = Query(default=30, ge=1, le=200)) -> dict:
    rows = s.conn.execute(
        "SELECT n.*, (r.user_id IS NOT NULL) AS read FROM notifications n LEFT JOIN notification_reads r "
        "ON r.notification_id = n.id AND r.user_id = ? WHERE n.ts > ? ORDER BY n.id DESC LIMIT ?",
        (s.user["id"], s.user["created_at"] - 86400, limit)).fetchall()
    items = [dict(row) | {"read": bool(row["read"])} for row in rows]
    return {"items": items, "unread": sum(not item["read"] for item in items)}


# ReadIn: which notification ids to mark read, or all OwO
class ReadIn(_In):
    ids: list[int] = Field(default_factory=list, max_length=500)
    all: bool = False


# POST /api/notifications/read: mark them read for me (INSERT OR IGNORE: marking twice is harmless) ^w^ *checks them off*
@router.post("/notifications/read")
def mark_read(body: ReadIn, s: Session = Depends(require("viewer"))) -> dict:
    existing = {row[0] for row in s.conn.execute("SELECT id FROM notifications ORDER BY id DESC LIMIT 500")}
    ids = existing if body.all else [i for i in body.ids if i in existing]
    s.conn.executemany("INSERT OR IGNORE INTO notification_reads(notification_id, user_id) VALUES (?, ?)",
                       [(i, s.user["id"]) for i in ids])
    return {"ok": True, "ts": time.time()}
