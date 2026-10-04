"""Request plumbing: database connection, client address, sessions, roles, CSRF and error responses."""

from __future__ import annotations

import secrets
import sqlite3
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response

from . import db
from .security import hash_token, new_token

ROLES = {"viewer": 1, "operator": 2, "admin": 3}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


class ApiError(HTTPException):
    def __init__(self, status: int, error: str, message: str, **extra):
        super().__init__(status_code=status, detail={"error": error, "message": message, **extra})


def get_conn(request: Request):
    conn = db.connect(request.app.state.wcfg.db)
    try:
        yield conn
    finally:
        conn.close()


def client_ip(request: Request) -> str:
    # The service listens on a unix socket that only nginx can reach; nginx sets X-Real-IP.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "")


@dataclass
class Session:
    id_hash: str
    stage: str
    csrf: str
    user: sqlite3.Row
    conn: sqlite3.Connection
    ip: str

    @property
    def username(self) -> str:
        return self.user["username"]

    def audit(self, action: str, outcome: str = "ok", target: str = "", detail=None) -> None:
        db.audit(self.conn, action, outcome, username=self.username, ip=self.ip, target=target, detail=detail)


def create_session(conn: sqlite3.Connection, response: Response, request: Request, user_id: int, stage: str,
                   pending_totp: bytes | None = None) -> tuple[str, str]:
    token, csrf = new_token(), secrets.token_urlsafe(24)
    now = time.time()
    conn.execute("INSERT INTO sessions(id_hash, user_id, stage, csrf, pending_totp, created_at, last_seen, ip, "
                 "user_agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (hash_token(token), user_id, stage, csrf, pending_totp, now, now, client_ip(request),
                  request.headers.get("user-agent", "")[:300]))
    wcfg = request.app.state.wcfg
    response.set_cookie(wcfg.cookie_name, token, httponly=True, secure=wcfg.secure_cookies, samesite="strict",
                        path="/")
    return hash_token(token), csrf


def end_session(conn: sqlite3.Connection, response: Response, request: Request, id_hash: str | None,
                clear_cookie: bool = True) -> None:
    if id_hash:
        conn.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
    if clear_cookie:
        wcfg = request.app.state.wcfg
        response.delete_cookie(wcfg.cookie_name, path="/", secure=wcfg.secure_cookies, httponly=True,
                               samesite="strict")


def _load(request: Request, conn: sqlite3.Connection) -> Session | None:
    wcfg = request.app.state.wcfg
    token = request.cookies.get(wcfg.cookie_name)
    if not token or len(token) > 100:
        return None
    id_hash = hash_token(token)
    row = conn.execute("SELECT * FROM sessions WHERE id_hash = ?", (id_hash,)).fetchone()
    if row is None:
        return None
    now = time.time()
    if row["stage"] == "full":
        expired = (now - row["last_seen"] > wcfg.session_idle_minutes * 60
                   or now - row["created_at"] > wcfg.session_max_hours * 3600)
    else:
        expired = now - row["created_at"] > wcfg.pending_minutes * 60
    user = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    if expired or user is None or user["disabled"]:
        conn.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
        return None
    if now - row["last_seen"] > 60:
        conn.execute("UPDATE sessions SET last_seen = ? WHERE id_hash = ?", (now, id_hash))
    return Session(id_hash, row["stage"], row["csrf"], user, conn, client_ip(request))


def _csrf(request: Request, s: Session) -> None:
    if request.method in UNSAFE:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not secrets.compare_digest(sent, s.csrf):
            raise ApiError(403, "csrf", "Missing or wrong CSRF token. Reload the page.")


def any_session(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Session:
    s = _load(request, conn)
    if s is None:
        raise ApiError(401, "unauthenticated", "Please sign in.")
    _csrf(request, s)
    return s


def stage(*allowed: str):
    def dep(s: Session = Depends(any_session)) -> Session:
        if s.stage not in allowed:
            raise ApiError(403, "wrong_stage", "This step is not available right now.", stage=s.stage)
        return s
    return dep


def require(role: str = "viewer", allow_password_change: bool = False):
    def dep(s: Session = Depends(any_session)) -> Session:
        if s.stage != "full":
            raise ApiError(401, "mfa_required", "Finish signing in first.", stage=s.stage)
        if s.user["must_change_password"] and not allow_password_change:
            raise ApiError(403, "password_change_required", "Choose a new password first.")
        if ROLES[s.user["role"]] < ROLES[role]:
            raise ApiError(403, "forbidden", f"This needs the {role} role.")
        return s
    return dep


def user_out(u: sqlite3.Row) -> dict:
    return {"id": u["id"], "username": u["username"], "display_name": u["display_name"], "email": u["email"],
            "role": u["role"], "source": u["source"], "mfa": u["totp_secret"] is not None,
            "disabled": bool(u["disabled"]), "locked": u["locked_until"] > time.time(),
            "must_change_password": bool(u["must_change_password"]),
            "created_at": u["created_at"], "last_login_at": u["last_login_at"]}
