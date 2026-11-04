# *puts on the admin hat* OwO valor/web/users.py is account administration, for admins only! ^w^
# Create accounts, edit them, disable/unlock them, reset a password or MFA, delete them. Safety rules built in:
# there's always at least one active admin, you can't disable or delete yourself, and directory (LDAP) accounts
# get their role from their groups *wags tail*
"""Account administration (admin role): create, edit, disable, unlock, reset password or MFA, delete."""

# Lazy type hints UwU
from __future__ import annotations

# re checks usernames; secrets makes temporary passwords; time stamps changes; Literal limits roles to three
# exact strings >w<
import re
import secrets
import time
from typing import Literal

# FastAPI router + Depends; pydantic models describe and check request bodies OwO
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

# db helpers; ApiError/Session/require/user_out from core; password hashing and the strength check ^w^
from . import db
from .core import ApiError, Session, require, user_out
from .security import hash_password, password_problem

# All routes under /api/users. USERNAME: 2-64 chars, starting with a letter or digit, then letters, digits, dot,
# underscore, @ or dash. Role: exactly one of the three role names UwU
router = APIRouter(prefix="/api/users", tags=["users"])
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{1,63}$")
Role = Literal["admin", "operator", "viewer"]


# _In: the base of every request body here; extra="forbid" rejects unknown fields >w<
class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# UserIn: a new account. Field() sets length limits. No password given = a temporary one is generated OwO
class UserIn(_In):
    username: str = Field(min_length=2, max_length=64)
    display_name: str = Field(default="", max_length=128)
    email: str = Field(default="", max_length=254)
    role: Role = "viewer"
    password: str | None = Field(default=None, max_length=1024)


# UserPatch: a change; only fields that are set (not None) get changed ^w^
class UserPatch(_In):
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=254)
    role: Role | None = None
    disabled: bool | None = None


# PasswordReset: an optional new password (none = a temporary one is generated) UwU
class PasswordReset(_In):
    password: str | None = Field(default=None, max_length=1024)


# _get: one user by id, or a 404 >w<
def _get(s: Session, uid: int):
    user = s.conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if user is None:
        raise ApiError(404, "not_found", "No such user.")
    return user


# _other_admins: how many OTHER active admins exist (the last admin must never be removed or demoted) OwO
def _other_admins(s: Session, uid: int) -> int:
    """Active admins besides this account: there must always be at least one."""
    return s.conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?",
                          (uid,)).fetchone()[0]


# _temp_password: a random temporary password (12 random bytes as ~16 URL-safe chars) ^w^
def _temp_password() -> str:
    return secrets.token_urlsafe(12)


# GET /api/users: every account, sorted by name ignoring case UwU
@router.get("")
def list_users(s: Session = Depends(require("admin"))) -> list[dict]:
    return [user_out(row) for row in s.conn.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE")]


# POST /api/users: create a local account >w<
@router.post("")
def create_user(body: UserIn, s: Session = Depends(require("admin"))) -> dict:
    # Check the username's format, and that it's not taken (409 Conflict) OwO
    if not USERNAME.match(body.username):
        raise ApiError(400, "invalid_username", "Use 2-64 letters, digits, dots, dashes, underscores or @.")
    if s.conn.execute("SELECT 1 FROM users WHERE username = ?", (body.username,)).fetchone():
        raise ApiError(409, "exists", "That username is taken.")
    # The given password or a temporary one, checked for strength ^w^
    password = body.password or _temp_password()
    problem = password_problem(password, body.username)
    if problem:
        raise ApiError(400, "weak_password", problem)
    # Insert it as a local account that must change its password at first sign-in, then audit UwU
    now = time.time()
    s.conn.execute("INSERT INTO users(username, display_name, email, role, source, password_hash, must_change_password, "
                   "created_at, updated_at) VALUES (?, ?, ?, ?, 'local', ?, 1, ?, ?)",
                   (body.username, body.display_name, body.email, body.role, hash_password(password), now, now))
    s.audit("user.create", target=body.username, detail={"role": body.role})
    # Return the new account; a generated password is included this ONE time so the admin can pass it on >w<
    user = s.conn.execute("SELECT * FROM users WHERE username = ?", (body.username,)).fetchone()
    out = user_out(user)
    if not body.password:
        out["temporary_password"] = password     # shown once to the admin who created the account
    return out


# PATCH /api/users/<id>: change display name, email, role or disabled OwO
@router.patch("/{uid}")
def update_user(uid: int, body: UserPatch, s: Session = Depends(require("admin"))) -> dict:
    user = _get(s, uid)
    # Only the fields that were sent ^w^
    changes = body.model_dump(exclude_none=True)
    # Would this remove an admin (role changed away from admin, or disabled)? Not allowed for the last one UwU
    demote = changes.get("role", user["role"]) != "admin" or changes.get("disabled") is True
    if user["role"] == "admin" and demote and not _other_admins(s, uid):
        raise ApiError(400, "last_admin", "This is the last active admin. Make someone else an admin first.")
    # No disabling yourself, and no changing a directory account's role (its groups decide) >w<
    if uid == s.user["id"] and changes.get("disabled"):
        raise ApiError(400, "self", "You can't disable your own account.")
    if user["source"] == "ldap" and "role" in changes:
        raise ApiError(400, "ldap_role", "Directory accounts get their role from their groups.")
    # Nothing to change = return the account as it is OwO
    if not changes:
        return user_out(user)
    # Build "col = ?, col = ?" from the field names (safe: they can only be UserPatch's own fields), with booleans
    # stored as 0/1, plus updated_at ^w^
    sets = ", ".join(f"{column} = ?" for column in changes)       # column names come from the UserPatch model
    s.conn.execute(f"UPDATE users SET {sets}, updated_at = ? WHERE id = ?",
                   (*[int(value) if isinstance(value, bool) else value for value in changes.values()], time.time(), uid))
    # A disabled account or a changed role ends their open sessions right away UwU
    if changes.get("disabled") or "role" in changes:            # their open sessions end now
        s.conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    s.audit("user.update", target=user["username"], detail=changes)
    return user_out(_get(s, uid))


# POST .../unlock: clear failed sign-ins and the lockout >w<
@router.post("/{uid}/unlock")
def unlock_user(uid: int, s: Session = Depends(require("admin"))) -> dict:
    user = _get(s, uid)
    s.conn.execute("UPDATE users SET failed_logins = 0, locked_until = 0 WHERE id = ?", (uid,))
    s.audit("user.unlock", target=user["username"])
    return user_out(_get(s, uid))


# POST .../reset-mfa: remove the authenticator, recovery codes and sessions in one transaction (all or nothing),
# so they set up MFA again at their next sign-in OwO
@router.post("/{uid}/reset-mfa")
def reset_mfa(uid: int, s: Session = Depends(require("admin"))) -> dict:
    user = _get(s, uid)
    with db.transaction(s.conn):
        s.conn.execute("UPDATE users SET totp_secret = NULL, totp_last_step = 0, updated_at = ? WHERE id = ?",
                       (time.time(), uid))
        s.conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (uid,))
        s.conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    s.audit("user.reset_mfa", target=user["username"])
    return user_out(_get(s, uid))


# POST .../reset-password: set a new (or temporary) password for a LOCAL account ^w^
@router.post("/{uid}/reset-password")
def reset_password(uid: int, body: PasswordReset, s: Session = Depends(require("admin"))) -> dict:
    user = _get(s, uid)
    if user["source"] != "local":
        raise ApiError(400, "not_local", "Directory accounts change their password in the directory.")
    password = body.password or _temp_password()
    problem = password_problem(password, user["username"])
    if problem:
        raise ApiError(400, "weak_password", problem)
    # In one transaction: new hash, must change at next sign-in, failed sign-ins and lockout cleared, sessions ended UwU
    with db.transaction(s.conn):
        s.conn.execute("UPDATE users SET password_hash = ?, must_change_password = 1, failed_logins = 0, "
                       "locked_until = 0, updated_at = ? WHERE id = ?", (hash_password(password), time.time(), uid))
        s.conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    s.audit("user.reset_password", target=user["username"])
    # Return the account (and the temporary password once, if one was generated) >w<
    out = user_out(_get(s, uid))
    if not body.password:
        out["temporary_password"] = password
    return out


# DELETE /api/users/<id>: delete an account (not yourself, not the last admin) OwO *waves goodbye with paw*
@router.delete("/{uid}")
def delete_user(uid: int, s: Session = Depends(require("admin"))) -> dict:
    user = _get(s, uid)
    if uid == s.user["id"]:
        raise ApiError(400, "self", "You can't delete your own account.")
    if user["role"] == "admin" and not _other_admins(s, uid):
        raise ApiError(400, "last_admin", "This is the last active admin.")
    s.conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    s.audit("user.delete", target=user["username"])
    return {"ok": True}
