"""Security tests: who may call every API route, CSRF/cross-origin protection on every state change, sign-in
required everywhere, response headers, cookie flags, and hostile names. Adding a route fails ACCESS below until
someone decides (and reviews) who may call it."""

import re

import pytest
from fastapi.routing import APIRoute

from valor.web.app import API_ROUTERS, SECURITY_HEADERS

from test_web import PASSWORD, client, enroll, env  # noqa: F401  (the web app fixture and helpers)

ACCESS = {
    "POST /api/auth/login": "PUBLIC",
    "POST /api/auth/mfa": "stage:mfa",
    "POST /api/auth/enroll/start": "stage:enroll",
    "POST /api/auth/enroll/confirm": "stage:enroll",
    "GET /api/auth/me": "session",
    "POST /api/auth/logout": "session",
    "POST /api/auth/password": "role:viewer",
    "POST /api/auth/recovery-codes": "role:viewer",
    "GET /api/users": "role:admin",
    "POST /api/users": "role:admin",
    "PATCH /api/users/{uid}": "role:admin",
    "POST /api/users/{uid}/unlock": "role:admin",
    "POST /api/users/{uid}/reset-mfa": "role:admin",
    "POST /api/users/{uid}/reset-password": "role:admin",
    "DELETE /api/users/{uid}": "role:admin",
    "GET /api/settings/notifications": "role:admin",
    "PUT /api/settings/notifications": "role:admin",
    "POST /api/settings/notifications/test": "role:admin",
    "GET /api/settings/ldap": "role:admin",
    "PUT /api/settings/ldap": "role:admin",
    "POST /api/settings/ldap/test": "role:admin",
    "GET /api/audit": "role:admin",
    "GET /api/notifications": "role:viewer",
    "POST /api/notifications/read": "role:viewer",
    "GET /api/health": "PUBLIC",
    "GET /api/status": "role:viewer",
    "GET /api/catalog": "role:viewer",
    "GET /api/ranges": "role:viewer",
    "GET /api/ranges/{name}": "role:viewer",
    "POST /api/specs/check": "role:operator",
    "PUT /api/ranges/{name}/spec": "role:operator",
    "DELETE /api/ranges/{name}/spec": "role:operator",
    "POST /api/ranges/{name}/plan": "role:operator",
    "POST /api/ranges/{name}/apply": "role:operator",
    "POST /api/ranges/{name}/verify": "role:operator",
    "POST /api/ranges/{name}/destroy": "role:operator",
    "GET /api/ranges/{name}/credentials": "role:operator",
    "POST /api/ranges/{name}/credentials/rotate": "role:operator",
    "POST /api/ranges/{name}/power": "role:operator",
    "GET /api/ranges/{name}/snapshots": "role:viewer",
    "POST /api/ranges/{name}/snapshots": "role:operator",
    "POST /api/ranges/{name}/snapshots/{snap}/rollback": "role:operator",
    "DELETE /api/ranges/{name}/snapshots/{snap}": "role:operator",
    "GET /api/jobs": "role:viewer",
    "GET /api/jobs/{job_id}": "role:viewer",
    "POST /api/ranges/{name}/vms/{host}/console": "role:operator",
    "WS /api/console/{sid}": "PUBLIC",            # one-time ticket + session cookie, checked in the handler
    "GET /api/isos": "role:viewer",
    "GET /api/isos/catalog": "role:viewer",
    "POST /api/isos/download": "role:operator",
    "GET /api/isos/tasks": "role:viewer",
    "POST /api/isos/upload": "role:operator",
    "DELETE /api/isos/{volid:path}": "role:admin",
    "GET /api/ranges/{name}/wireguard": "role:viewer",
    "GET /api/ranges/{name}/wireguard/peers/{peer}": "role:operator",
    "POST /api/ranges/{name}/wireguard/peers/{peer}/rotate": "role:operator",
    "GET /api/blueprints": "role:viewer",
    "GET /api/blueprints/{bp_id}": "role:viewer",
    "PUT /api/blueprints/{bp_id}": "role:operator",
    "POST /api/blueprints/from-range": "role:operator",
    "DELETE /api/blueprints/{bp_id}": "role:operator",
    "POST /api/blueprints/{bp_id}/preview": "role:operator",
    "POST /api/blueprints/{bp_id}/deploy": "role:operator",
    "GET /api/settings/ai": "role:admin",
    "PUT /api/settings/ai": "role:admin",
    "POST /api/settings/ai/test": "role:admin",
    "GET /api/settings/ai/usage": "role:admin",
    "GET /api/builder": "role:operator",
    "POST /api/builder/chat": "role:operator",
    "GET /api/ranges/{name}/draft": "role:viewer",
    "PUT /api/ranges/{name}/draft": "role:operator",
    "POST /api/ranges/{name}/draft/ops": "role:operator",
    "DELETE /api/ranges/{name}/draft": "role:operator",
    "GET /api/ranges/{name}/draft/suggest-segment": "role:operator",
    "GET /api/ranges/{name}/chat": "role:viewer",
    "DELETE /api/ranges/{name}/chat": "role:operator",
    "POST /api/ranges/{name}/chat": "role:operator",
}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
SELF_SERVICE = {"POST /api/auth/login", "POST /api/auth/mfa", "POST /api/auth/enroll/start",
                "POST /api/auth/enroll/confirm", "POST /api/auth/logout", "POST /api/auth/password",
                "POST /api/auth/recovery-codes", "POST /api/notifications/read"}
SECRETS = {"GET /api/ranges/{name}/credentials", "GET /api/ranges/{name}/wireguard/peers/{peer}"}


def _access(dependant) -> str | None:
    for dep in dependant.dependencies:
        c = dep.call
        if hasattr(c, "valor_role"):
            return f"role:{c.valor_role}"
        if hasattr(c, "valor_stage"):
            return f"stage:{','.join(c.valor_stage)}"
        if getattr(c, "__name__", "") == "any_session":
            return "session"
        inner = _access(dep)
        if inner:
            return inner
    return None


def _routes() -> dict[str, str]:
    out = {}
    for router in API_ROUTERS:
        for r in router.routes:
            for m in getattr(r, "methods", None) or {"WS"}:
                out[f"{m} {r.path}"] = _access(r.dependant) or "PUBLIC"
    return out


def test_every_route_has_a_reviewed_access_level():
    assert _routes() == ACCESS


def test_access_rules():
    for key, level in ACCESS.items():
        method, path = key.split(" ", 1)
        if path.startswith(("/api/settings", "/api/users", "/api/audit")):
            assert level == "role:admin", key
        if method in UNSAFE and key not in SELF_SERVICE:
            assert level in ("role:operator", "role:admin"), key
        if key in SECRETS:
            assert level in ("role:operator", "role:admin"), key
    public = {k for k, v in ACCESS.items() if v == "PUBLIC"}
    assert public == {"POST /api/auth/login", "GET /api/health", "WS /api/console/{sid}"}


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x1", path).replace("{volid:path}", "iso-store:iso/x.iso")


def test_sign_in_required_everywhere(env):
    app, _ = env
    with client(app) as anon:
        for key, level in ACCESS.items():
            method, path = key.split(" ", 1)
            if level == "PUBLIC" or method == "WS":
                continue
            r = anon.request(method, _url(path), json={} if method in UNSAFE else None)
            assert r.status_code == 401, (key, r.status_code, r.text[:200])


def test_csrf_and_origin_on_every_state_change(env):
    app, _ = env
    with client(app) as adm:
        me, _ = enroll(adm, "admin")
        for key, level in ACCESS.items():
            method, path = key.split(" ", 1)
            if method not in UNSAFE or level == "PUBLIC":
                continue
            r = adm.request(method, _url(path), json={})
            assert r.status_code == 403 and r.json()["error"] == "csrf", (key, r.status_code, r.text[:200])
            r = adm.request(method, _url(path), json={}, headers={"X-CSRF-Token": me["csrf"],
                                                               "Origin": "https://evil.example"})
            assert r.status_code == 403 and r.json()["error"] == "cross_origin", key
            r = adm.request(method, _url(path), content=b"a=b", headers={
                "X-CSRF-Token": me["csrf"], "Content-Type": "application/x-www-form-urlencoded"})
            if path != "/api/isos/upload":
                assert r.status_code == 415, key                     # no HTML form posts


def test_headers_and_cookie(env):
    app, wcfg = env
    with client(app) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        assert r.status_code == 200
        for k, v in SECURITY_HEADERS.items():
            assert r.headers.get(k) == v, k
        assert r.headers["Cache-Control"] == "no-store"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert "unsafe-eval" not in r.headers["Content-Security-Policy"]
        cookie = r.headers["set-cookie"].lower()
        assert cookie.startswith(wcfg.cookie_name.lower() + "=") and "httponly" in cookie and "samesite=strict" in cookie
        assert "path=/" in cookie and "domain=" not in cookie                  # host-only cookie
        assert ("secure" in cookie) == wcfg.secure_cookies


@pytest.mark.parametrize("path", [
    "/api/ranges/..%2F..%2Fetc%2Fpasswd", "/api/ranges/UPPER", "/api/ranges/a", "/api/blueprints/..%2Fx",
    "/api/ranges/web2tier/wireguard/peers/..%2F..", "/api/jobs/..%2F..%2Fetc", "/api/ranges/x%00y",
])
def test_hostile_names_are_refused(env, path):
    app, _ = env
    with client(app) as op:
        enroll(op, "olivia")
        r = op.get(path)
        assert r.status_code in (400, 404, 422), (path, r.status_code)
        assert "root:" not in r.text
