# *plugs in all the tiny pipes* OwO valor/web/core.py is the plumbing every API request uses! ^w^
#   database connections   one per request, always closed
#   client_ip              who's asking (nginx tells us)
#   sessions               creating, loading (with expiry) and ending sign-in sessions
#   CSRF                   the anti-forgery token check for anything that changes data
#   roles                  require("operator") and friends, used on every endpoint
#   ApiError               errors as tidy JSON
#   qr_svg                 QR codes for authenticator setup and WireGuard configs *wags tail*
"""Request plumbing: database connection, client address, sessions, roles, CSRF and error responses."""

# Lazy type hints UwU
from __future__ import annotations

# secrets (CSRF tokens + constant-time compare), sqlite3 (the database), time (expiry), dataclass (Session) >w<
import secrets
import sqlite3
import time
from dataclasses import dataclass

# FastAPI: Depends (dependency injection), HTTPException (base of ApiError), Request/Response OwO
from fastapi import Depends, HTTPException, Request, Response

# db = database helpers; hash_token/new_token make and hash session tokens ^w^
from . import db
from .security import hash_token, new_token

# ROLES ranks the roles so they can be compared: admin > operator > viewer. UNSAFE = HTTP methods that change
# things (they need the CSRF token) UwU
ROLES = {"viewer": 1, "operator": 2, "admin": 3}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


# ApiError: raise this anywhere to answer with an HTTP status and a JSON body like
# {"error": "forbidden", "message": "...", ...extra fields} >w<
class ApiError(HTTPException):
    def __init__(self, status: int, error: str, message: str, **extra):
        super().__init__(status_code=status, detail={"error": error, "message": message, **extra})


# get_conn: a FastAPI dependency that opens the database for one request and closes it afterwards, no matter
# what (the `yield` hands the connection to the endpoint) OwO
def get_conn(request: Request):
    conn = db.connect(request.app.state.wcfg.db)
    try:
        yield conn
    finally:
        conn.close()


# client_ip: the real client address *sniffs*
def client_ip(request: Request) -> str:
    # The service listens on a unix socket that only nginx can reach; nginx sets X-Real-IP.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "")


# Session: everything about the signed-in person for this request ^w^
#   id_hash  the hash of their session token (the token itself is never stored)
#   stage    "full" = fully signed in; other stages = halfway (e.g. MFA still to do)
#   csrf     their anti-forgery token
#   user     their users row; conn = this request's database connection; ip = their address
@dataclass
class Session:
    id_hash: str
    stage: str
    csrf: str
    user: sqlite3.Row
    conn: sqlite3.Connection
    ip: str

    # username: shortcut for the user's name UwU
    @property
    def username(self) -> str:
        return self.user["username"]

    # audit: write an audit log entry as this person, from their address >w<
    def audit(self, action: str, outcome: str = "ok", target: str = "", detail=None) -> None:
        db.audit(self.conn, action, outcome, username=self.username, ip=self.ip, target=target, detail=detail)


# create_session: start a sign-in session and set the cookie. Returns (token hash, CSRF token) OwO
#   stage         "full" or a halfway stage (password OK, MFA not done yet)
#   pending_totp  a new authenticator secret being set up, kept with the session until confirmed
def create_session(conn: sqlite3.Connection, response: Response, request: Request, user_id: int, stage: str,
                   pending_totp: bytes | None = None) -> tuple[str, str]:
    # A random session token and a separate random CSRF token ^w^
    token, csrf = new_token(), secrets.token_urlsafe(24)
    now = time.time()
    # Store only the HASH of the token (a database leak can't be used to hijack sessions), plus who, when, from where
    # and which browser (cut to 300 chars) UwU
    conn.execute("INSERT INTO sessions(id_hash, user_id, stage, csrf, pending_totp, created_at, last_seen, ip, "
                 "user_agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (hash_token(token), user_id, stage, csrf, pending_totp, now, now, client_ip(request),
                  request.headers.get("user-agent", "")[:300]))
    wcfg = request.app.state.wcfg
    # The cookie: httponly (page scripts can't read it), secure (HTTPS only), samesite=strict (never sent along
    # with requests from other sites), path=/ *tucks the cookie away safely* >w<
    response.set_cookie(wcfg.cookie_name, token, httponly=True, secure=wcfg.secure_cookies, samesite="strict",
                        path="/")
    return hash_token(token), csrf


# end_session: delete the session (sign out) and, unless told not to, clear the cookie in the browser OwO
def end_session(conn: sqlite3.Connection, response: Response, request: Request, id_hash: str | None,
                clear_cookie: bool = True) -> None:
    if id_hash:
        conn.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
    if clear_cookie:
        wcfg = request.app.state.wcfg
        # Deleting a cookie must use the same attributes it was set with, or browsers ignore the delete ^w^
        response.delete_cookie(wcfg.cookie_name, path="/", secure=wcfg.secure_cookies, httponly=True,
                               samesite="strict")


# _load: find the session for this request's cookie, or None UwU
def _load(request: Request, conn: sqlite3.Connection) -> Session | None:
    wcfg = request.app.state.wcfg
    token = request.cookies.get(wcfg.cookie_name)
    # No cookie, or an absurdly long one (never valid, and not worth hashing) = no session >w<
    if not token or len(token) > 100:
        return None
    # Look the token up by its hash OwO
    id_hash = hash_token(token)
    row = conn.execute("SELECT * FROM sessions WHERE id_hash = ?", (id_hash,)).fetchone()
    if row is None:
        return None
    now = time.time()
    # Expiry ^w^: a full session ends after session_idle_minutes without activity OR session_max_hours in total.
    # A halfway session (waiting for MFA) only lives pending_minutes
    if row["stage"] == "full":
        expired = (now - row["last_seen"] > wcfg.session_idle_minutes * 60
                   or now - row["created_at"] > wcfg.session_max_hours * 3600)
    else:
        expired = now - row["created_at"] > wcfg.pending_minutes * 60
    user = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    # Expired, user deleted, or user disabled: remove the session and treat the request as signed out UwU
    if expired or user is None or user["disabled"]:
        conn.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
        return None
    # Remember activity, but write it at most once a minute (saves a database write on every request) >w<
    if now - row["last_seen"] > 60:
        conn.execute("UPDATE sessions SET last_seen = ? WHERE id_hash = ?", (now, id_hash))
    return Session(id_hash, row["stage"], row["csrf"], user, conn, client_ip(request))


# _csrf: for requests that change things, the X-CSRF-Token header must match the session's token (the web UI
# sends it; another website can't know it). compare_digest = a constant-time comparison OwO
def _csrf(request: Request, s: Session) -> None:
    if request.method in UNSAFE:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not secrets.compare_digest(sent, s.csrf):
            raise ApiError(403, "csrf", "Missing or wrong CSRF token. Reload the page.")


# any_session: the base dependency: a signed-in session of ANY stage, with the CSRF check done, or a 401 ^w^
def any_session(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Session:
    s = _load(request, conn)
    if s is None:
        raise ApiError(401, "unauthenticated", "Please sign in.")
    _csrf(request, s)
    return s


# stage("x", "y"): a dependency for the halfway steps of sign-in (e.g. the MFA page): only sessions in one of the
# allowed stages get through UwU
def stage(*allowed: str):
    def dep(s: Session = Depends(any_session)) -> Session:
        if s.stage not in allowed:
            raise ApiError(403, "wrong_stage", "This step is not available right now.", stage=s.stage)
        return s
    # Tag the dependency with its stages; the security tests read this to check every route says who may call it >w<
    dep.valor_stage = allowed                       # read by the security tests (route -> required access)
    return dep


# require(role): THE dependency on almost every endpoint: fully signed in, password changed if required (unless
# the endpoint is the password change itself), and at least `role` OwO *guards the door*
def require(role: str = "viewer", allow_password_change: bool = False):
    def dep(s: Session = Depends(any_session)) -> Session:
        # Halfway signed in = finish signing in first ^w^
        if s.stage != "full":
            raise ApiError(401, "mfa_required", "Finish signing in first.", stage=s.stage)
        # An admin-forced password change comes before anything else UwU
        if s.user["must_change_password"] and not allow_password_change:
            raise ApiError(403, "password_change_required", "Choose a new password first.")
        # Too low a role = 403 Forbidden, naming the role needed >w<
        if ROLES[s.user["role"]] < ROLES[role]:
            raise ApiError(403, "forbidden", f"This needs the {role} role.")
        return s
    # Tag the dependency with the role, for the security tests OwO
    dep.valor_role = role
    return dep


# user_out: a user row as JSON for the UI. Note what's NOT in it: no password hash and no TOTP secret, just
# "mfa": true/false ^w^ *keeps secrets hidden behind fluffy tail*
def user_out(u: sqlite3.Row) -> dict:
    return {"id": u["id"], "username": u["username"], "display_name": u["display_name"], "email": u["email"],
            "role": u["role"], "source": u["source"], "mfa": u["totp_secret"] is not None,
            "disabled": bool(u["disabled"]), "locked": u["locked_until"] > time.time(),
            "must_change_password": bool(u["must_change_password"]),
            "created_at": u["created_at"], "last_login_at": u["last_login_at"]}


# qr_svg: draw `text` as a QR code SVG. error = error-correction level (l/m/q/h); scale 5 px per module, a
# 2-module border, near-black on white UwU
def qr_svg(text: str, error: str = "m") -> str:
    """A standalone SVG QR code (with the SVG namespace, so it also works as an <img> data URI - segno's
    svg_inline() leaves the namespace out, which browsers only accept inside HTML)."""
    # Imported here so segno is only loaded when a QR code is actually needed >w<
    import io

    import segno
    # Draw it into an in-memory buffer and return the SVG text OwO
    buf = io.BytesIO()
    segno.make(text, error=error).save(buf, kind="svg", xmldecl=False, svgns=True, nl=False, scale=5, border=2,
                                       dark="#111111", light="#ffffff")
    return buf.getvalue().decode()
