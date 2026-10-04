"""Account administration (admin role): create, edit, disable, unlock, reset password or MFA, delete."""

from __future__ import annotations

import re
import secrets
import time
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from . import db
from .core import ApiError, Session, require, user_out
from .security import hash_password, password_problem

router = APIRouter(prefix="/api/users", tags=["users"])
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{1,63}$")
Role = Literal["admin", "operator", "viewer"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserIn(_In):
    username: str = Field(min_length=2, max_length=64)
    display_name: str = Field(default="", max_length=128)
    email: str = Field(default="", max_length=254)
    role: Role = "viewer"
    password: str | None = Field(default=None, max_length=1024)


class UserPatch(_In):
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=254)
    role: Role | None = None
    disabled: bool | None = None


class PasswordReset(_In):
    password: str | None = Field(default=None, max_length=1024)


def _get(s: Session, uid: int):
    u = s.conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if u is None:
        raise ApiError(404, "not_found", "No such user.")
    return u


def _other_admins(s: Session, uid: int) -> int:
    return s.conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?",
                          (uid,)).fetchone()[0]


def _temp_password() -> str:
    return secrets.token_urlsafe(12)


@router.get("")
def list_users(s: Session = Depends(require("admin"))) -> list[dict]:
    return [user_out(u) for u in s.conn.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE")]


@router.post("")
def create_user(body: UserIn, s: Session = Depends(require("admin"))) -> dict:
    if not USERNAME.match(body.username):
        raise ApiError(400, "invalid_username", "Use 2-64 letters, digits, dots, dashes, underscores or @.")
    if s.conn.execute("SELECT 1 FROM users WHERE username = ?", (body.username,)).fetchone():
        raise ApiError(409, "exists", "That username is taken.")
    password = body.password or _temp_password()
    problem = password_problem(password, body.username)
    if problem:
        raise ApiError(400, "weak_password", problem)
    now = time.time()
    s.conn.execute("INSERT INTO users(username, display_name, email, role, source, password_hash, must_change_password, "
                   "created_at, updated_at) VALUES (?, ?, ?, ?, 'local', ?, 1, ?, ?)",
                   (body.username, body.display_name, body.email, body.role, hash_password(password), now, now))
    s.audit("user.create", target=body.username, detail={"role": body.role})
    u = s.conn.execute("SELECT * FROM users WHERE username = ?", (body.username,)).fetchone()
    out = user_out(u)
    if not body.password:
        out["temporary_password"] = password     # shown once to the admin who created the account
    return out


@router.patch("/{uid}")
def update_user(uid: int, body: UserPatch, s: Session = Depends(require("admin"))) -> dict:
    u = _get(s, uid)
    changes = body.model_dump(exclude_none=True)
    demote = changes.get("role", u["role"]) != "admin" or changes.get("disabled") is True
    if u["role"] == "admin" and demote and not _other_admins(s, uid):
        raise ApiError(400, "last_admin", "This is the last active admin. Make someone else an admin first.")
    if uid == s.user["id"] and changes.get("disabled"):
        raise ApiError(400, "self", "You can't disable your own account.")
    if u["source"] == "ldap" and "role" in changes:
        raise ApiError(400, "ldap_role", "Directory accounts get their role from their groups.")
    if not changes:
        return user_out(u)
    sets = ", ".join(f"{k} = ?" for k in changes)
    s.conn.execute(f"UPDATE users SET {sets}, updated_at = ? WHERE id = ?",
                   (*[int(v) if isinstance(v, bool) else v for v in changes.values()], time.time(), uid))
    if changes.get("disabled") or "role" in changes:
        s.conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    s.audit("user.update", target=u["username"], detail=changes)
    return user_out(_get(s, uid))


@router.post("/{uid}/unlock")
def unlock_user(uid: int, s: Session = Depends(require("admin"))) -> dict:
    u = _get(s, uid)
    s.conn.execute("UPDATE users SET failed_logins = 0, locked_until = 0 WHERE id = ?", (uid,))
    s.audit("user.unlock", target=u["username"])
    return user_out(_get(s, uid))


@router.post("/{uid}/reset-mfa")
def reset_mfa(uid: int, s: Session = Depends(require("admin"))) -> dict:
    u = _get(s, uid)
    with db.transaction(s.conn):
        s.conn.execute("UPDATE users SET totp_secret = NULL, totp_last_step = 0, updated_at = ? WHERE id = ?",
                       (time.time(), uid))
        s.conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (uid,))
        s.conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    s.audit("user.reset_mfa", target=u["username"])
    return user_out(_get(s, uid))


@router.post("/{uid}/reset-password")
def reset_password(uid: int, body: PasswordReset, s: Session = Depends(require("admin"))) -> dict:
    u = _get(s, uid)
    if u["source"] != "local":
        raise ApiError(400, "not_local", "Directory accounts change their password in the directory.")
    password = body.password or _temp_password()
    problem = password_problem(password, u["username"])
    if problem:
        raise ApiError(400, "weak_password", problem)
    with db.transaction(s.conn):
        s.conn.execute("UPDATE users SET password_hash = ?, must_change_password = 1, failed_logins = 0, "
                       "locked_until = 0, updated_at = ? WHERE id = ?", (hash_password(password), time.time(), uid))
        s.conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    s.audit("user.reset_password", target=u["username"])
    out = user_out(_get(s, uid))
    if not body.password:
        out["temporary_password"] = password
    return out


@router.delete("/{uid}")
def delete_user(uid: int, s: Session = Depends(require("admin"))) -> dict:
    u = _get(s, uid)
    if uid == s.user["id"]:
        raise ApiError(400, "self", "You can't delete your own account.")
    if u["role"] == "admin" and not _other_admins(s, uid):
        raise ApiError(400, "last_admin", "This is the last active admin.")
    s.conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    s.audit("user.delete", target=u["username"])
    return {"ok": True}
