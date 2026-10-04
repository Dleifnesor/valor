"""Sign-in: password (local or LDAP) -> TOTP code (or enrollment at first sign-in) -> full session."""

from __future__ import annotations

import sqlite3
import time

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import __version__
from . import db, ldapauth, notify
from .core import (ApiError, Session, any_session, client_ip, create_session, end_session, get_conn, qr_svg, require,
                   stage, user_out)
from .security import (b32, hash_password, hash_token, new_recovery_codes, new_totp_secret, normalize_recovery_code,
                       otpauth_uri, password_problem, verify_password, verify_totp)

router = APIRouter(prefix="/api/auth", tags=["auth"])
GENERIC = "Wrong username or password, or the account is temporarily locked."


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(_In):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class CodeIn(_In):
    code: str = Field(min_length=6, max_length=32)


class PasswordIn(_In):
    current: str = Field(min_length=1, max_length=1024)
    new: str = Field(min_length=1, max_length=1024)


class ReauthIn(_In):
    password: str = Field(min_length=1, max_length=1024)


def me_out(request: Request, s: Session) -> dict:
    return {"user": user_out(s.user), "stage": s.stage, "csrf": s.csrf, "version": __version__,
            "instance": request.app.state.wcfg.instance_name}


def _fail(request: Request, conn: sqlite3.Connection, user: sqlite3.Row | None, username: str, reason: str,
          action: str = "login") -> None:
    wcfg = request.app.state.wcfg
    ip = client_ip(request)
    request.app.state.ip_limiter.hit(ip)
    if user is not None:
        n = user["failed_logins"] + 1
        locked = n >= wcfg.lockout_threshold
        conn.execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                     (0 if locked else n, time.time() + wcfg.lockout_minutes * 60 if locked else user["locked_until"],
                      user["id"]))
        if locked:
            db.audit(conn, "account.lock", "ok", username=user["username"], ip=ip,
                     detail=f"locked for {wcfg.lockout_minutes} min after {n} failures")
            notify.emit(conn, request.app.state.box, "warning", "security.lockout",
                        f"Account {user['username']} locked",
                        f"{n} failed sign-in attempts in a row (last from {ip}). Locked for {wcfg.lockout_minutes} minutes.")
    db.audit(conn, action, "fail", username=username, ip=ip, detail=reason)


def _ldap_settings(request: Request, conn: sqlite3.Connection) -> dict:
    return ldapauth.settings_plain(db.get_setting(conn, "ldap", {}), request.app.state.box)


def _upsert_ldap_user(conn: sqlite3.Connection, username: str, res) -> sqlite3.Row:
    now = time.time()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO users(username, display_name, email, role, source, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, 'ldap', ?, ?)", (username, res.display_name, res.email, res.role, now, now))
    else:
        conn.execute("UPDATE users SET display_name = ?, email = ?, role = ?, updated_at = ? WHERE id = ?",
                     (res.display_name or row["display_name"], res.email or row["email"], res.role, now, row["id"]))
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    ip = client_ip(request)
    if request.app.state.ip_limiter.blocked(ip):
        db.audit(conn, "login", "fail", username=body.username, ip=ip, detail="rate limited")
        raise ApiError(429, "rate_limited", "Too many failed attempts from this address. Wait a few minutes.")
    username = body.username.strip()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user is not None and user["locked_until"] > time.time():
        verify_password(None, body.password)        # same timing as a real check
        db.audit(conn, "login", "fail", username=username, ip=ip, detail="account locked")
        raise ApiError(401, "invalid_credentials", GENERIC)

    ok, reason, rehash = False, "wrong password", None
    if user is None or user["source"] == "ldap":
        ldap_cfg = _ldap_settings(request, conn)
        if ldap_cfg.get("enabled"):
            res = ldapauth.authenticate(ldap_cfg, username, body.password)
            ok, reason = res.ok, res.reason
            if ok:
                user = _upsert_ldap_user(conn, user["username"] if user else username, res)
        else:
            verify_password(None, body.password)
            reason = "unknown user" if user is None else "directory sign-in is disabled"
    else:
        ok, rehash = verify_password(user["password_hash"], body.password)
    if ok and user["disabled"]:
        ok, reason = False, "account disabled"
    if not ok:
        _fail(request, conn, user, username, reason)
        raise ApiError(401, "invalid_credentials", GENERIC)

    if rehash:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (rehash, user["id"]))
    step = "mfa" if user["totp_secret"] is not None else "enroll"
    _, csrf = create_session(conn, response, request, user["id"], step)
    db.audit(conn, "login.password", "ok", username=user["username"], ip=ip, detail=f"next: {step}")
    return {"stage": step, "csrf": csrf}


def _complete(request: Request, response: Response, s: Session, method: str) -> dict:
    """MFA done: reset counters, replace the pending session with a full one (new ID)."""
    conn = s.conn
    conn.execute("UPDATE users SET failed_logins = 0, locked_until = 0, last_login_at = ? WHERE id = ?",
                 (time.time(), s.user["id"]))
    end_session(conn, response, request, s.id_hash, clear_cookie=False)
    id_hash, csrf = create_session(conn, response, request, s.user["id"], "full")
    db.audit(conn, "login", "ok", username=s.username, ip=s.ip, detail=f"mfa: {method}")
    user = conn.execute("SELECT * FROM users WHERE id = ?", (s.user["id"],)).fetchone()
    return me_out(request, Session(id_hash, "full", csrf, user, conn, s.ip))


@router.post("/mfa")
def mfa(body: CodeIn, request: Request, response: Response, s: Session = Depends(stage("mfa"))) -> dict:
    conn, user, box = s.conn, s.user, request.app.state.box
    if user["locked_until"] > time.time():
        raise ApiError(401, "invalid_code", "That code is not valid, or the account is temporarily locked.")
    secret = box.open(user["totp_secret"], f"totp:{user['id']}")
    step = verify_totp(secret, body.code, user["totp_last_step"])
    if step is not None:
        conn.execute("UPDATE users SET totp_last_step = ? WHERE id = ?", (step, user["id"]))
        return _complete(request, response, s, "totp")
    code = normalize_recovery_code(body.code)
    if code:
        cur = conn.execute("UPDATE recovery_codes SET used_at = ? WHERE user_id = ? AND code_hash = ? AND used_at IS NULL",
                           (time.time(), user["id"], hash_token(code)))
        if cur.rowcount == 1:
            left = conn.execute("SELECT COUNT(*) FROM recovery_codes WHERE user_id = ? AND used_at IS NULL",
                                (user["id"],)).fetchone()[0]
            notify.emit(conn, box, "warning", "security.recovery_code", f"{user['username']} used a recovery code",
                        f"{left} recovery codes left. If this wasn't them, reset their MFA.")
            return _complete(request, response, s, f"recovery code ({left} left)")
    _fail(request, conn, user, user["username"], "wrong code", action="login.mfa")
    raise ApiError(401, "invalid_code", "That code is not valid, or the account is temporarily locked.")


def _issuer(request: Request) -> str:
    return request.app.state.wcfg.instance_name


@router.post("/enroll/start")
def enroll_start(request: Request, s: Session = Depends(stage("enroll"))) -> dict:
    box = request.app.state.box
    secret = new_totp_secret()
    s.conn.execute("UPDATE sessions SET pending_totp = ? WHERE id_hash = ?",
                   (box.seal(secret, f"pending:{s.id_hash}"), s.id_hash))
    uri = otpauth_uri(secret, s.username, _issuer(request))
    svg = qr_svg(uri)
    return {"secret": b32(secret), "uri": uri, "qr_svg": svg, "issuer": _issuer(request)}


@router.post("/enroll/confirm")
def enroll_confirm(body: CodeIn, request: Request, response: Response, s: Session = Depends(stage("enroll"))) -> dict:
    conn, box = s.conn, request.app.state.box
    row = conn.execute("SELECT pending_totp FROM sessions WHERE id_hash = ?", (s.id_hash,)).fetchone()
    if row is None or row["pending_totp"] is None:
        raise ApiError(400, "no_enrollment", "Start the enrollment first.")
    secret = box.open(row["pending_totp"], f"pending:{s.id_hash}")
    step = verify_totp(secret, body.code, 0)
    if step is None:
        _fail(request, conn, s.user, s.username, "wrong enrollment code", action="mfa.enroll")
        raise ApiError(400, "invalid_code", "That code doesn't match. Check the time on your phone and try again.")
    codes = new_recovery_codes()
    with db.transaction(conn):
        conn.execute("UPDATE users SET totp_secret = ?, totp_last_step = ?, updated_at = ? WHERE id = ?",
                     (box.seal(secret, f"totp:{s.user['id']}"), step, time.time(), s.user["id"]))
        conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (s.user["id"],))
        conn.executemany("INSERT INTO recovery_codes(user_id, code_hash) VALUES (?, ?)",
                         [(s.user["id"], hash_token(c)) for c in codes])
    db.audit(conn, "mfa.enroll", "ok", username=s.username, ip=s.ip)
    out = _complete(request, response, s, "enrollment")
    out["recovery_codes"] = codes
    return out


@router.get("/me")
def me(request: Request, s: Session = Depends(any_session)) -> dict:
    return me_out(request, s)


@router.post("/logout")
def logout(request: Request, response: Response, s: Session = Depends(any_session)) -> dict:
    end_session(s.conn, response, request, s.id_hash)
    db.audit(s.conn, "logout", "ok", username=s.username, ip=s.ip)
    return {"ok": True}


@router.post("/password")
def change_password(body: PasswordIn, request: Request, response: Response,
                    s: Session = Depends(require("viewer", allow_password_change=True))) -> dict:
    if s.user["source"] != "local":
        raise ApiError(400, "not_local", "Directory accounts change their password in the directory.")
    ok, _ = verify_password(s.user["password_hash"], body.current)
    if not ok:
        _fail(request, s.conn, s.user, s.username, "wrong current password", action="password.change")
        raise ApiError(400, "wrong_password", "The current password is wrong.")
    problem = password_problem(body.new, s.username)
    if problem:
        raise ApiError(400, "weak_password", problem)
    with db.transaction(s.conn):
        s.conn.execute("UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE id = ?",
                       (hash_password(body.new), time.time(), s.user["id"]))
        s.conn.execute("DELETE FROM sessions WHERE user_id = ? AND id_hash != ?", (s.user["id"], s.id_hash))
    s.audit("password.change")
    user = s.conn.execute("SELECT * FROM users WHERE id = ?", (s.user["id"],)).fetchone()
    return me_out(request, Session(s.id_hash, s.stage, s.csrf, user, s.conn, s.ip))


@router.post("/recovery-codes")
def regenerate_codes(body: ReauthIn, request: Request, s: Session = Depends(require("viewer"))) -> dict:
    if s.user["source"] == "local":
        ok, _ = verify_password(s.user["password_hash"], body.password)
    else:
        ok = ldapauth.authenticate(_ldap_settings(request, s.conn), s.username, body.password).ok
    if not ok:
        _fail(request, s.conn, s.user, s.username, "wrong password", action="mfa.recovery_codes")
        raise ApiError(400, "wrong_password", "The password is wrong.")
    codes = new_recovery_codes()
    with db.transaction(s.conn):
        s.conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (s.user["id"],))
        s.conn.executemany("INSERT INTO recovery_codes(user_id, code_hash) VALUES (?, ?)",
                           [(s.user["id"], hash_token(c)) for c in codes])
    s.audit("mfa.recovery_codes")
    return {"recovery_codes": codes}
