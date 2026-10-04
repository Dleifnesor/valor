"""Admin settings (notification channels, LDAP), the audit log and per-user in-app notifications."""

from __future__ import annotations

import copy
import secrets
import time
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from . import db, ldapauth, notify
from .core import ApiError, Session, require

router = APIRouter(prefix="/api", tags=["settings"])
MASK = "********"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------- notification channels
class ChannelIn(_In):
    id: str | None = Field(default=None, max_length=32)
    type: Literal["email", "discord", "slack", "teams"]
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    events: Literal["problems", "all"] = "problems"
    config: dict


class ChannelsIn(_In):
    channels: list[ChannelIn] = Field(max_length=20)


class TestIn(_In):
    id: str = Field(max_length=32)


def _validate_channel(ch: dict) -> None:
    c = ch["config"]
    if ch["type"] == "email":
        for k in ("host", "from", "to"):
            if not c.get(k):
                raise ApiError(400, "invalid_channel", f"{ch['name']}: '{k}' is required for email.")
        if c.get("security", "starttls") not in ("starttls", "tls"):
            raise ApiError(400, "invalid_channel", f"{ch['name']}: security must be starttls or tls.")
    elif not str(c.get("url", "")).startswith("https://") and c.get("url") != MASK:
        raise ApiError(400, "invalid_channel", f"{ch['name']}: the webhook URL must start with https://")


def _masked(channels: list) -> list:
    out = copy.deepcopy(channels)
    for ch in out:
        for f in notify.SECRET_FIELDS.get(ch["type"], ()):
            if ch["config"].get(f):
                ch["config"][f] = MASK
    return out


@router.get("/settings/notifications")
def get_channels(s: Session = Depends(require("admin"))) -> dict:
    return {"channels": _masked(db.get_setting(s.conn, "notification_channels", []))}


@router.put("/settings/notifications")
def put_channels(body: ChannelsIn, request: Request, s: Session = Depends(require("admin"))) -> dict:
    box = request.app.state.box
    old = {c["id"]: c for c in db.get_setting(s.conn, "notification_channels", [])}
    new = []
    for ch in body.channels:
        d = ch.model_dump()
        d["id"] = d["id"] if d["id"] in old else secrets.token_hex(6)
        _validate_channel(d)
        for f in notify.SECRET_FIELDS[d["type"]]:
            v = d["config"].get(f)
            if v == MASK or (v in (None, "") and f == "password"):
                prev = old.get(d["id"], {}).get("config", {}).get(f)   # keep the stored (encrypted) value
                if prev and old[d["id"]]["type"] == d["type"]:
                    d["config"][f] = prev
                else:
                    d["config"].pop(f, None)
            elif v:
                d["config"][f] = box.seal_text(v, f"notify:{d['id']}:{f}")
        new.append(d)
    db.put_setting(s.conn, "notification_channels", new, by=s.username)
    s.audit("settings.notifications", detail={"channels": [f"{c['type']}:{c['name']}" for c in new]})
    return {"channels": _masked(new)}


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


# ---------------------------------------------------------------------- LDAP / Active Directory
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


class LdapTestIn(_In):
    username: str = Field(default="", max_length=128)


def _ldap_masked(stored: dict) -> dict:
    out = {**ldapauth.DEFAULTS, **stored}
    out["bind_password"] = MASK if stored.get("bind_password") else ""
    return out


@router.get("/settings/ldap")
def get_ldap(s: Session = Depends(require("admin"))) -> dict:
    return _ldap_masked(db.get_setting(s.conn, "ldap", {}))


@router.put("/settings/ldap")
def put_ldap(body: LdapIn, request: Request, s: Session = Depends(require("admin"))) -> dict:
    old = db.get_setting(s.conn, "ldap", {})
    d = body.model_dump()
    if d["enabled"]:
        if not d["url"].startswith(("ldaps://", "ldap://")):
            raise ApiError(400, "invalid_ldap", "The URL must start with ldaps:// or ldap://.")
        if d["url"].startswith("ldap://") and not d["starttls"]:
            raise ApiError(400, "invalid_ldap", "Plain LDAP is not allowed: use ldaps:// or turn on StartTLS.")
        if "{username}" not in d["user_filter"]:
            raise ApiError(400, "invalid_ldap", "The user filter must contain {username}.")
        if not any(d[f"group_{r}"] for r in ("admin", "operator", "viewer")):
            raise ApiError(400, "invalid_ldap", "Map at least one directory group to a VALOR role.")
    pw = d.pop("bind_password")
    if pw and pw != MASK:
        d["bind_password"] = request.app.state.box.seal_text(pw, "ldap:bind_password")
    elif old.get("bind_password"):
        d["bind_password"] = old["bind_password"]
    db.put_setting(s.conn, "ldap", d, by=s.username)
    s.audit("settings.ldap", detail={"enabled": d["enabled"], "url": d["url"]})
    return _ldap_masked(d)


@router.post("/settings/ldap/test")
def test_ldap(body: LdapTestIn, request: Request, s: Session = Depends(require("admin"))) -> dict:
    cfg = ldapauth.settings_plain(db.get_setting(s.conn, "ldap", {}), request.app.state.box)
    if not cfg["url"]:
        raise ApiError(400, "invalid_ldap", "Save the directory settings first.")
    res = ldapauth.test_connection(cfg, body.username.strip())
    s.audit("settings.ldap.test", "ok" if res["ok"] else "fail", detail=res["message"])
    return res


# ---------------------------------------------------------------------- audit log
@router.get("/audit")
def audit_log(s: Session = Depends(require("admin")), before: int | None = Query(default=None),
              limit: int = Query(default=100, ge=1, le=500), q: str = Query(default="", max_length=100)) -> dict:
    sql, args = "SELECT * FROM audit WHERE 1=1", []
    if before:
        sql += " AND id < ?"
        args.append(before)
    if q:
        sql += " AND (action LIKE ? OR username LIKE ? OR target LIKE ? OR detail LIKE ?)"
        args += [f"%{q}%"] * 4
    rows = [dict(r) for r in s.conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]
    return {"entries": rows, "next": rows[-1]["id"] if len(rows) == limit else None}


# ---------------------------------------------------------------------- in-app notifications
@router.get("/notifications")
def my_notifications(s: Session = Depends(require("viewer")), limit: int = Query(default=30, ge=1, le=200)) -> dict:
    rows = s.conn.execute(
        "SELECT n.*, (r.user_id IS NOT NULL) AS read FROM notifications n LEFT JOIN notification_reads r "
        "ON r.notification_id = n.id AND r.user_id = ? WHERE n.ts > ? ORDER BY n.id DESC LIMIT ?",
        (s.user["id"], s.user["created_at"] - 86400, limit)).fetchall()
    items = [dict(r) | {"read": bool(r["read"])} for r in rows]
    return {"items": items, "unread": sum(not i["read"] for i in items)}


class ReadIn(_In):
    ids: list[int] = Field(default_factory=list, max_length=500)
    all: bool = False


@router.post("/notifications/read")
def mark_read(body: ReadIn, s: Session = Depends(require("viewer"))) -> dict:
    existing = {r[0] for r in s.conn.execute("SELECT id FROM notifications ORDER BY id DESC LIMIT 500")}
    ids = existing if body.all else [i for i in body.ids if i in existing]
    s.conn.executemany("INSERT OR IGNORE INTO notification_reads(notification_id, user_id) VALUES (?, ?)",
                       [(i, s.user["id"]) for i in ids])
    return {"ok": True, "ts": time.time()}
