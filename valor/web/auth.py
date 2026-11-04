# *knocks on the door: password, then secret code* OwO valor/web/auth.py is how people SIGN IN! ^w^
# Step 1: username + password (a local account, or LDAP). Step 2: a 6-digit authenticator code (or a recovery code);
# at the very first sign-in, set up the authenticator instead (enroll). Only then is the session "full". Along the
# way: rate limits per address, lockouts after too many failures, constant-time checks, audit entries, and
# notifications for lockouts and recovery-code use *guards the door with fluffy tail*
"""Sign-in: password (local or LDAP) -> TOTP code (or enrollment at first sign-in) -> full session."""

# Lazy type hints UwU
from __future__ import annotations

# sqlite3 (rows), time (lockouts, timestamps) >w<
import sqlite3
import time

# FastAPI and pydantic OwO
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

# The version (shown after sign-in), db/ldapauth/notify, the session helpers from core, and the password/TOTP/
# recovery-code building blocks from security ^w^
from .. import __version__
from . import db, ldapauth, notify
from .core import (ApiError, Session, any_session, client_ip, create_session, end_session, get_conn, qr_svg, require,
                   stage, user_out)
from .security import (b32, hash_password, hash_token, new_recovery_codes, new_totp_secret, normalize_recovery_code,
                       otpauth_uri, password_problem, verify_password, verify_totp)

# All routes under /api/auth. GENERIC: ONE vague message for every sign-in failure, so nobody can learn which
# usernames exist or which accounts are locked UwU
router = APIRouter(prefix="/api/auth", tags=["auth"])
GENERIC = "Wrong username or password, or the account is temporarily locked."


# _In: request bodies refuse unknown fields >w<
class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# LoginIn (username + password), CodeIn (a 6-digit or recovery code), PasswordIn (current + new), ReauthIn (the
# password again, before sensitive actions) OwO
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


# me_out: what the UI gets about "me": my account, my session stage, my CSRF token, the version and instance name ^w^
def me_out(request: Request, s: Session) -> dict:
    return {"user": user_out(s.user), "stage": s.stage, "csrf": s.csrf, "version": __version__,
            "instance": request.app.state.wcfg.instance_name}


# _fail: record a failed attempt UwU
def _fail(request: Request, conn: sqlite3.Connection, user: sqlite3.Row | None, username: str, reason: str,
          action: str = "login") -> None:
    wcfg = request.app.state.wcfg
    ip = client_ip(request)
    # Count it against the client address (rate limiting) >w<
    request.app.state.ip_limiter.hit(ip)
    # For a known account: count it, and on reaching the threshold, lock the account for lockout_minutes (resetting
    # the counter), audit the lock and notify admins OwO
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
    # Always audit the failure with the reason (the reason stays in the log, never in the answer) ^w^
    db.audit(conn, action, "fail", username=username, ip=ip, detail=reason)


# _ldap_settings: the LDAP settings with the bind password decrypted UwU
def _ldap_settings(request: Request, conn: sqlite3.Connection) -> dict:
    return ldapauth.settings_plain(db.get_setting(conn, "ldap", {}), request.app.state.box)


# _upsert_ldap_user: create or update the local copy of a directory account (name, email, role from groups) >w<
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


# POST /api/auth/login: step 1 OwO
@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    ip = client_ip(request)
    # Too many failures from this address = 429, before even looking at the password ^w^
    if request.app.state.ip_limiter.blocked(ip):
        db.audit(conn, "login", "fail", username=body.username, ip=ip, detail="rate limited")
        raise ApiError(429, "rate_limited", "Too many failed attempts from this address. Wait a few minutes.")
    username = body.username.strip()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    # A locked account: still burn the same time as a real check (so locked vs. wrong can't be told apart by timing),
    # audit, and answer with the generic message UwU
    if user is not None and user["locked_until"] > time.time():
        verify_password(None, body.password)        # same timing as a real check
        db.audit(conn, "login", "fail", username=username, ip=ip, detail="account locked")
        raise ApiError(401, "invalid_credentials", GENERIC)

    # Unknown users and directory accounts go to LDAP (when enabled); local accounts check their Argon2 hash >w<
    ok, reason, rehash = False, "wrong password", None
    if user is None or user["source"] == "ldap":
        ldap_cfg = _ldap_settings(request, conn)
        if ldap_cfg.get("enabled"):
            res = ldapauth.authenticate(ldap_cfg, username, body.password)
            ok, reason = res.ok, res.reason
            if ok:
                user = _upsert_ldap_user(conn, user["username"] if user else username, res)
        # LDAP off: waste the same time anyway, and note why it failed OwO
        else:
            verify_password(None, body.password)
            reason = "unknown user" if user is None else "directory sign-in is disabled"
    else:
        ok, rehash = verify_password(user["password_hash"], body.password)
    # A disabled account fails too ^w^
    if ok and user["disabled"]:
        ok, reason = False, "account disabled"
    if not ok:
        _fail(request, conn, user, username, reason)
        raise ApiError(401, "invalid_credentials", GENERIC)

    # Upgrade an old password hash while we know the password UwU
    if rehash:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (rehash, user["id"]))
    # Next stage: "mfa" if they have an authenticator, else "enroll" to set one up. A HALF session is created >w<
    step = "mfa" if user["totp_secret"] is not None else "enroll"
    _, csrf = create_session(conn, response, request, user["id"], step)
    db.audit(conn, "login.password", "ok", username=user["username"], ip=ip, detail=f"next: {step}")
    return {"stage": step, "csrf": csrf}


# _complete: MFA is done! Reset failure counters, end the half session and create a brand-new FULL one (a new id
# stops session-fixation tricks), audit, and return "me" OwO
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


# POST /api/auth/mfa: step 2 with an authenticator code or a recovery code ^w^
@router.post("/mfa")
def mfa(body: CodeIn, request: Request, response: Response, s: Session = Depends(stage("mfa"))) -> dict:
    conn, user, box = s.conn, s.user, request.app.state.box
    if user["locked_until"] > time.time():
        raise ApiError(401, "invalid_code", "That code is not valid, or the account is temporarily locked.")
    # Decrypt their TOTP secret and check the code (only newer steps than the last used one) UwU
    secret = box.open(user["totp_secret"], f"totp:{user['id']}")
    step = verify_totp(secret, body.code, user["totp_last_step"])
    if step is not None:
        conn.execute("UPDATE users SET totp_last_step = ? WHERE id = ?", (step, user["id"]))
        return _complete(request, response, s, "totp")
    # Not a TOTP code? Try it as a recovery code: mark it used (only if unused), notify admins, and finish >w<
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
    # Neither worked: count the failure OwO
    _fail(request, conn, user, user["username"], "wrong code", action="login.mfa")
    raise ApiError(401, "invalid_code", "That code is not valid, or the account is temporarily locked.")


# _issuer: the name shown in the authenticator app ^w^
def _issuer(request: Request) -> str:
    return request.app.state.wcfg.instance_name


# POST /api/auth/enroll/start: make a new TOTP secret, keep it (sealed) on the half session, and return it as text,
# an otpauth:// link and a QR code UwU
@router.post("/enroll/start")
def enroll_start(request: Request, s: Session = Depends(stage("enroll"))) -> dict:
    box = request.app.state.box
    secret = new_totp_secret()
    s.conn.execute("UPDATE sessions SET pending_totp = ? WHERE id_hash = ?",
                   (box.seal(secret, f"pending:{s.id_hash}"), s.id_hash))
    uri = otpauth_uri(secret, s.username, _issuer(request))
    svg = qr_svg(uri)
    return {"secret": b32(secret), "uri": uri, "qr_svg": svg, "issuer": _issuer(request)}


# POST /api/auth/enroll/confirm: the person types a code from their app to prove the setup works >w<
@router.post("/enroll/confirm")
def enroll_confirm(body: CodeIn, request: Request, response: Response, s: Session = Depends(stage("enroll"))) -> dict:
    conn, box = s.conn, request.app.state.box
    row = conn.execute("SELECT pending_totp FROM sessions WHERE id_hash = ?", (s.id_hash,)).fetchone()
    if row is None or row["pending_totp"] is None:
        raise ApiError(400, "no_enrollment", "Start the enrollment first.")
    secret = box.open(row["pending_totp"], f"pending:{s.id_hash}")
    # Check it (any step: nothing was used before) OwO
    step = verify_totp(secret, body.code, 0)
    if step is None:
        _fail(request, conn, s.user, s.username, "wrong enrollment code", action="mfa.enroll")
        raise ApiError(400, "invalid_code", "That code doesn't match. Check the time on your phone and try again.")
    # Save the secret on the account, make 10 fresh recovery codes (stored hashed), in one transaction ^w^
    codes = new_recovery_codes()
    with db.transaction(conn):
        conn.execute("UPDATE users SET totp_secret = ?, totp_last_step = ?, updated_at = ? WHERE id = ?",
                     (box.seal(secret, f"totp:{s.user['id']}"), step, time.time(), s.user["id"]))
        conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (s.user["id"],))
        conn.executemany("INSERT INTO recovery_codes(user_id, code_hash) VALUES (?, ?)",
                         [(s.user["id"], hash_token(code)) for code in codes])
    db.audit(conn, "mfa.enroll", "ok", username=s.username, ip=s.ip)
    # Finish signing in, and show the recovery codes ONCE UwU
    out = _complete(request, response, s, "enrollment")
    out["recovery_codes"] = codes
    return out


# GET /api/auth/me: who am I (any stage) >w<
@router.get("/me")
def me(request: Request, s: Session = Depends(any_session)) -> dict:
    return me_out(request, s)


# POST /api/auth/logout: end my session and clear the cookie OwO
@router.post("/logout")
def logout(request: Request, response: Response, s: Session = Depends(any_session)) -> dict:
    end_session(s.conn, response, request, s.id_hash)
    db.audit(s.conn, "logout", "ok", username=s.username, ip=s.ip)
    return {"ok": True}


# POST /api/auth/password: change my password (allowed even when a change is required) ^w^
@router.post("/password")
def change_password(body: PasswordIn, request: Request, response: Response,
                    s: Session = Depends(require("viewer", allow_password_change=True))) -> dict:
    if s.user["source"] != "local":
        raise ApiError(400, "not_local", "Directory accounts change their password in the directory.")
    # The current password must be right (a wrong one counts as a failure) UwU
    ok, _ = verify_password(s.user["password_hash"], body.current)
    if not ok:
        _fail(request, s.conn, s.user, s.username, "wrong current password", action="password.change")
        raise ApiError(400, "wrong_password", "The current password is wrong.")
    problem = password_problem(body.new, s.username)
    if problem:
        raise ApiError(400, "weak_password", problem)
    # Save the new hash, clear "must change", and sign out my OTHER sessions (this one stays) >w<
    with db.transaction(s.conn):
        s.conn.execute("UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE id = ?",
                       (hash_password(body.new), time.time(), s.user["id"]))
        s.conn.execute("DELETE FROM sessions WHERE user_id = ? AND id_hash != ?", (s.user["id"], s.id_hash))
    s.audit("password.change")
    user = s.conn.execute("SELECT * FROM users WHERE id = ?", (s.user["id"],)).fetchone()
    return me_out(request, Session(s.id_hash, s.stage, s.csrf, user, s.conn, s.ip))


# POST /api/auth/recovery-codes: new recovery codes, after typing the password again (old codes stop working) OwO *hands
# over fresh codes*
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
                           [(s.user["id"], hash_token(code)) for code in codes])
    s.audit("mfa.recovery_codes")
    return {"recovery_codes": codes}
