# *builds the whole house out of blocks* OwO valor/web/app.py creates the FastAPI web application! ^w^
# create_app() wires everything together: settings, database setup, the background build watcher, security
# checks on every request, security headers on every response, error handlers that turn errors into tidy JSON,
# and all the API routers. The uvicorn service (valor-web) calls it once at startup *wags tail*
"""FastAPI application factory."""

# Lazy type hints UwU
from __future__ import annotations

# logging for crash logs; threading for the background watcher; asynccontextmanager for startup/shutdown;
# Path for files >w<
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

# FastAPI itself and its Request; RequestValidationError = a request that doesn't match its schema; FileResponse
# / JSONResponse to answer; Starlette's HTTPException = the base of FastAPI's HTTP errors OwO
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Engine config + loader; the error types handled below; every router module; the web config + loader; UNSAFE
# (the HTTP methods that change things); Box (encryption) and RateLimiter (sign-in throttling) ^w^
from ..config import Config, load_config
from ..errors import LockBusy, ValorError
from . import (access, auth, blueprints, builder, chats, consoles, db, editing, isos, ranges, settings, status,
               users)
from .config import WebConfig, load_web_config
from .core import UNSAFE
from .security import Box, RateLimiter

# The logger for this module UwU
log = logging.getLogger("valor.web")

# SECURITY_HEADERS: added to EVERY response, to make browsers extra careful *puts on armor*:
#   Content-Security-Policy  only load scripts/styles/images from VALOR itself; nothing may frame the page;
#                            forms only post to VALOR; no plugins. Blocks most cross-site scripting tricks.
#   X-Content-Type-Options   don't guess file types (a text file can't turn into a script)
#   X-Frame-Options          older browsers: never show VALOR inside a frame (clickjacking)
#   Referrer-Policy          never tell other sites which VALOR page a link came from
#   Cross-Origin-*           keep VALOR's windows and files to itself
#   Permissions-Policy       no camera, microphone, location or USB for any page >w<
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                               "base-uri 'none'; form-action 'self'; object-src 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), usb=()",
}


# API_ROUTERS: every router, in one list. The security tests loop over it to check that EVERY route declares who
# may call it OwO
# Every API router; the security tests walk this list (each route must state who may call it).
API_ROUTERS = (auth.router, users.router, settings.router, status.router, ranges.router, consoles.router, isos.router,
               access.router, blueprints.router, builder.router, editing.router, chats.router)


# create_app: make the app. Tests pass their own configs and a static folder; watch_builds=False turns off the
# background watcher (tests don't want threads) ^w^
def create_app(cfg: Config | None = None, wcfg: WebConfig | None = None, static_dir: str | None = None,
               watch_builds: bool = True) -> FastAPI:
    # Without arguments, read the real config files UwU
    cfg = cfg or load_config()
    wcfg = wcfg or load_web_config()

    # lifespan: code that runs at startup (before `yield`) and at shutdown (after it) >w<
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: bring the database schema up to date, then start the watcher that hands failed builds to the AI
        # assistant. `stop` is an Event the watcher checks, so shutdown can tell it to finish OwO
        db.migrate(wcfg.db)
        stop = threading.Event()
        if watch_builds:                            # failed builds -> the assistant (chats.fix_failed_builds)
            threading.Thread(target=chats.watch_failed_builds, args=(app, stop), daemon=True, name="fix-builds").start()
        # The app runs while we're paused here; at shutdown, signal the watcher to stop ^w^
        yield
        stop.set()

    # The app. docs_url/redoc_url/openapi_url = None turn OFF the auto-generated API docs, so strangers can't browse
    # the whole API's shape UwU
    app = FastAPI(title="VALOR", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    # Shared things every request can reach through request.app.state: both configs, the encryption box (None when
    # there's no key file) and the per-address sign-in rate limiter >w<
    app.state.cfg, app.state.wcfg = cfg, wcfg
    app.state.box = Box.from_file(wcfg.secret_key_file) if Path(wcfg.secret_key_file).exists() else None
    app.state.ip_limiter = RateLimiter(wcfg.ip_attempts, wcfg.ip_window_minutes * 60)

    # guard: a middleware = code that wraps EVERY request *stands guard with fluffy tail*
    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Cross-site request protection for state changes, on top of SameSite=Strict cookies and the CSRF token:
        # the browser's Origin / Sec-Fetch-Site must say "same origin", and bodies must be JSON (no HTML forms).
        # Only for requests that change things (POST/PUT/PATCH/DELETE) on the API OwO
        if request.method in UNSAFE and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            # If the browser sent an Origin, it must be this very host; otherwise another website is trying to make the
            # person's browser do something here: 403 ^w^
            if origin and origin.split("://", 1)[-1] != host:
                return JSONResponse({"error": "cross_origin", "message": "Cross-origin request refused."}, 403)
            # Sec-Fetch-Site (sent by modern browsers) must say same-origin (or "none" = typed by the person) UwU
            if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
                return JSONResponse({"error": "cross_origin", "message": "Cross-site request refused."}, 403)
            # Requests with a body must be JSON. HTML forms on other sites can't send JSON, so this blocks another class of
            # cross-site tricks. The ISO upload is the one exception (its body is the raw ISO file) >w<
            ctype = request.headers.get("content-type", "")
            raw_ok = request.url.path == "/api/isos/upload"          # the body is the ISO itself
            if (request.headers.get("content-length", "0") != "0" and not ctype.startswith("application/json")
                    and not raw_ok):
                return JSONResponse({"error": "content_type", "message": "Send JSON."}, 415)
        # All checks passed: let the request through to its endpoint OwO
        response = await call_next(request)
        # Add the security headers (setdefault = keep any header an endpoint set itself) ^w^
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        # API answers must never be cached (they may hold private data) UwU
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # http_error: HTTP errors (404, 401...) as JSON. If the detail is already a dict (our ApiError), send it as is;
    # otherwise wrap it as {"error": "http_error", "message": ...}. Extra headers (like WWW-Authenticate) are kept >w<
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        body = exc.detail if isinstance(exc.detail, dict) else {"error": "http_error", "message": str(exc.detail)}
        return JSONResponse(body, exc.status_code, headers=getattr(exc, "headers", None))

    # invalid: a request that doesn't match its schema -> 422 with one entry per problem, so the UI can show the
    # problem next to the right field OwO
    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        # loc[0] is "body" / "query": the rest names the field, as the UI shows it
        problems = [{"location": ".".join(str(part) for part in error.get("loc", [])[1:]),
                     "message": error.get("msg", "")} for error in exc.errors()]
        return JSONResponse({"error": "invalid_request", "message": "The request is not valid.", "details": problems},
                            422)

    # busy: the engine lock is held by another job -> 409 Conflict, with the error's JSON ^w^
    @app.exception_handler(LockBusy)
    async def busy(request: Request, exc: LockBusy):
        return JSONResponse(exc.to_dict(), 409)

    # valor_error: any other ValorError -> 400 Bad Request, with its JSON (code, message, hint...) UwU
    @app.exception_handler(ValorError)
    async def valor_error(request: Request, exc: ValorError):
        return JSONResponse(exc.to_dict(), 400)

    # crash: anything unexpected. The full traceback goes to the log, but the person only gets a generic message,
    # so no internal details leak out >w< *hides the scary traceback*
    @app.exception_handler(Exception)
    async def crash(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"error": "internal_error", "message": "Something went wrong on the server."}, 500)

    # Plug in every API router OwO
    for api_router in API_ROUTERS:
        app.include_router(api_router)

    # Dev only: also serve the built web UI from static_dir (in the VALOR VM, nginx does this) ^w^
    # In the VALOR VM nginx serves the built UI; this fallback is for development only.
    if static_dir and Path(static_dir, "index.html").is_file():
        root = Path(static_dir).resolve()

        # A catch-all route: serve the file if it exists INSIDE the static folder (resolve + is_relative_to stops tricks
        # like "../../etc/passwd"), and index.html for anything else, so the single-page app's routes work UwU
        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = (root / path).resolve()
            if path and f.is_file() and f.is_relative_to(root):
                return FileResponse(f)
            return FileResponse(root / "index.html")

    # Done! Here's your app *proudly presents it* ^w^
    return app
