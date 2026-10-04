"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import Config, load_config
from ..errors import LockBusy, ValorError
from . import auth, db, ranges, settings, status, users
from .config import WebConfig, load_web_config
from .core import UNSAFE
from .security import Box, RateLimiter

log = logging.getLogger("valor.web")

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


def create_app(cfg: Config | None = None, wcfg: WebConfig | None = None, static_dir: str | None = None) -> FastAPI:
    cfg = cfg or load_config()
    wcfg = wcfg or load_web_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.migrate(wcfg.db)
        yield

    app = FastAPI(title="VALOR", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.cfg, app.state.wcfg = cfg, wcfg
    app.state.box = Box.from_file(wcfg.secret_key_file) if Path(wcfg.secret_key_file).exists() else None
    app.state.ip_limiter = RateLimiter(wcfg.ip_attempts, wcfg.ip_window_minutes * 60)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Cross-site request protection for state changes, on top of SameSite=Strict cookies and the CSRF token:
        # the browser's Origin / Sec-Fetch-Site must say "same origin", and bodies must be JSON (no HTML forms).
        if request.method in UNSAFE and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            if origin and origin.split("://", 1)[-1] != host:
                return JSONResponse({"error": "cross_origin", "message": "Cross-origin request refused."}, 403)
            if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
                return JSONResponse({"error": "cross_origin", "message": "Cross-site request refused."}, 403)
            ctype = request.headers.get("content-type", "")
            if request.headers.get("content-length", "0") != "0" and not ctype.startswith("application/json"):
                return JSONResponse({"error": "content_type", "message": "Send JSON."}, 415)
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        body = exc.detail if isinstance(exc.detail, dict) else {"error": "http_error", "message": str(exc.detail)}
        return JSONResponse(body, exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        errs = [{"location": ".".join(str(x) for x in e.get("loc", [])[1:]), "message": e.get("msg", "")}
                for e in exc.errors()]
        return JSONResponse({"error": "invalid_request", "message": "The request is not valid.", "details": errs}, 422)

    @app.exception_handler(LockBusy)
    async def busy(request: Request, exc: LockBusy):
        return JSONResponse(exc.to_dict(), 409)

    @app.exception_handler(ValorError)
    async def valor_error(request: Request, exc: ValorError):
        return JSONResponse(exc.to_dict(), 400)

    @app.exception_handler(Exception)
    async def crash(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"error": "internal_error", "message": "Something went wrong on the server."}, 500)

    for r in (auth.router, users.router, settings.router, status.router, ranges.router):
        app.include_router(r)

    # In the VALOR VM nginx serves the built UI; this fallback is for development only.
    if static_dir and Path(static_dir, "index.html").is_file():
        root = Path(static_dir).resolve()

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = (root / path).resolve()
            if path and f.is_file() and f.is_relative_to(root):
                return FileResponse(f)
            return FileResponse(root / "index.html")

    return app
