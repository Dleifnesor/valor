import base64
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from valor.config import Config
from valor.web import db
from valor.web.app import create_app
from valor.web.config import WebConfig
from valor.web.security import Box, hash_password, totp_code

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "correct horse battery staple"


def _secret(b32: str) -> bytes:
    return base64.b32decode(b32 + "=" * (-len(b32) % 8))


def _now_step() -> int:
    return int(time.time() // 30)


@pytest.fixture
def env(tmp_path):
    key = tmp_path / "secret.key"
    Box.create_key_file(key)
    wcfg = WebConfig(db=str(tmp_path / "web.sqlite3"), secret_key_file=str(key), tls_cert=str(tmp_path / "none.crt"))
    cfg = Config(project_dir=str(ROOT), state_dir=str(tmp_path / "state"), token_file=str(tmp_path / "no-token.json"),
                 node="pve1", pool="valor-ranges", template_pool="valor-templates", storage="local-lvm",
                 segment_bridge="vmbr100", uplink_bridge="vmbr0", job_runner="worker", secret_key_file=str(key))
    db.migrate(wcfg.db)
    conn = db.connect(wcfg.db)
    now = time.time()
    for name, role in (("admin", "admin"), ("olivia", "operator"), ("vic", "viewer")):
        conn.execute("INSERT INTO users(username, role, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                     (name, role, hash_password(PASSWORD), now, now))
    conn.close()
    app = create_app(cfg, wcfg)
    return app, wcfg


def client(app) -> TestClient:
    return TestClient(app, base_url="https://testserver")


def enroll(c: TestClient, username: str) -> tuple[dict, bytes]:
    r = c.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200 and r.json()["stage"] == "enroll"
    csrf = r.json()["csrf"]
    r = c.post("/api/auth/enroll/start", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and "<svg" in r.json()["qr_svg"] and r.json()["uri"].startswith("otpauth://totp/")
    secret = _secret(r.json()["secret"])
    r = c.post("/api/auth/enroll/confirm", json={"code": totp_code(secret, _now_step())}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["stage"] == "full" and len(me["recovery_codes"]) == 10
    return me, secret


def test_enroll_then_mfa_login(env):
    app, wcfg = env
    with client(app) as c:
        me, secret = enroll(c, "admin")
        pending_cookie = c.cookies.get(wcfg.cookie_name)
        assert c.get("/api/auth/me").json()["user"]["mfa"] is True
        # unsafe request without the CSRF token is refused
        assert c.post("/api/auth/logout").status_code == 403
        assert c.post("/api/auth/logout", headers={"X-CSRF-Token": me["csrf"]}).status_code == 200
        assert c.get("/api/auth/me").status_code == 401

        r = c.post("/api/auth/login", json={"username": "ADMIN", "password": PASSWORD})   # case-insensitive
        assert r.json()["stage"] == "mfa"
        csrf = r.json()["csrf"]
        assert c.get("/api/users").status_code == 401                                   # not past MFA yet
        assert c.post("/api/auth/mfa", json={"code": "000000"}, headers={"X-CSRF-Token": csrf}).status_code == 401
        before = c.cookies.get(wcfg.cookie_name)
        code = totp_code(secret, _now_step() + 1)          # the enrollment used the current step
        r = c.post("/api/auth/mfa", json={"code": code}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200 and r.json()["stage"] == "full"
        assert c.cookies.get(wcfg.cookie_name) not in (before, pending_cookie)          # new session ID
        assert c.get("/api/users").status_code == 200


def test_totp_replay_and_recovery_code(env):
    app, _ = env
    with client(app) as c:
        me, secret = enroll(c, "olivia")
        c.post("/api/auth/logout", headers={"X-CSRF-Token": me["csrf"]})
        csrf = c.post("/api/auth/login", json={"username": "olivia", "password": PASSWORD}).json()["csrf"]
        replay = totp_code(secret, _now_step())             # already used for the enrollment
        assert c.post("/api/auth/mfa", json={"code": replay}, headers={"X-CSRF-Token": csrf}).status_code == 401
        rc = me["recovery_codes"][0]
        r = c.post("/api/auth/mfa", json={"code": rc.upper().replace("-", " ")}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        c.post("/api/auth/logout", headers={"X-CSRF-Token": r.json()["csrf"]})
        csrf = c.post("/api/auth/login", json={"username": "olivia", "password": PASSWORD}).json()["csrf"]
        assert c.post("/api/auth/mfa", json={"code": rc}, headers={"X-CSRF-Token": csrf}).status_code == 401   # used


def test_lockout_and_generic_errors(env):
    app, wcfg = env
    with client(app) as c:
        r1 = c.post("/api/auth/login", json={"username": "nobody", "password": "x" * 12})
        for _ in range(wcfg.lockout_threshold):
            r2 = c.post("/api/auth/login", json={"username": "vic", "password": "wrong password!"})
        assert r1.status_code == r2.status_code == 401 and r1.json()["message"] == r2.json()["message"]
        r = c.post("/api/auth/login", json={"username": "vic", "password": PASSWORD})
        assert r.status_code == 401                      # locked even with the right password
    conn = db.connect(wcfg.db)
    assert conn.execute("SELECT locked_until FROM users WHERE username='vic'").fetchone()[0] > time.time()
    assert conn.execute("SELECT COUNT(*) FROM notifications WHERE event='security.lockout'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM audit WHERE action='account.lock'").fetchone()[0] == 1


def test_ip_rate_limit(env):
    app, wcfg = env
    with client(app) as c:
        codes = [c.post("/api/auth/login", json={"username": f"u{i}", "password": "x" * 12}).status_code
                 for i in range(wcfg.ip_attempts + 1)]
    assert codes[-1] == 429 and codes[0] == 401


def test_cross_origin_and_content_type(env):
    app, _ = env
    with client(app) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD},
                   headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        r = c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD},
                   headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403
        r = c.post("/api/auth/login", content="username=admin&password=x",
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert r.status_code == 415
        r = c.get("/api/health")
        assert r.json() == {"ok": True} and r.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_roles_and_user_admin(env):
    app, _ = env
    with client(app) as admin, client(app) as op:
        me, _ = enroll(admin, "admin")
        h = {"X-CSRF-Token": me["csrf"]}
        ome, _ = enroll(op, "olivia")
        assert op.get("/api/users").status_code == 403
        r = admin.post("/api/users", json={"username": "newbie", "role": "viewer"}, headers=h)
        assert r.status_code == 200 and len(r.json()["temporary_password"]) >= 12 and r.json()["must_change_password"]
        assert admin.post("/api/users", json={"username": "newbie"}, headers=h).status_code == 409
        assert admin.post("/api/users", json={"username": "weak", "password": "short"}, headers=h).status_code == 400
        admin_id = me["user"]["id"]
        r = admin.patch(f"/api/users/{admin_id}", json={"role": "viewer"}, headers=h)
        assert r.status_code == 400 and r.json()["error"] == "last_admin"
        assert admin.delete(f"/api/users/{admin_id}", headers=h).status_code == 400
        oid = ome["user"]["id"]
        assert admin.post(f"/api/users/{oid}/reset-mfa", headers=h).json()["mfa"] is False
        assert op.get("/api/auth/me").status_code == 401             # their sessions were ended
        audit = admin.get("/api/audit").json()["entries"]
        assert {"user.create", "user.reset_mfa", "login"} <= {e["action"] for e in audit}


def test_must_change_password(env):
    app, _ = env
    with client(app) as admin, client(app) as u:
        me, _ = enroll(admin, "admin")
        tmp = admin.post("/api/users", json={"username": "newbie", "role": "operator"},
                         headers={"X-CSRF-Token": me["csrf"]}).json()["temporary_password"]
        r = u.post("/api/auth/login", json={"username": "newbie", "password": tmp})
        csrf = r.json()["csrf"]
        sec = _secret(u.post("/api/auth/enroll/start", headers={"X-CSRF-Token": csrf}).json()["secret"])
        me2 = u.post("/api/auth/enroll/confirm", json={"code": totp_code(sec, _now_step())},
                     headers={"X-CSRF-Token": csrf}).json()
        assert me2["user"]["must_change_password"]
        assert u.get("/api/ranges").status_code == 403
        r = u.post("/api/auth/password", json={"current": tmp, "new": "a much better passphrase"},
                   headers={"X-CSRF-Token": me2["csrf"]})
        assert r.status_code == 200 and not r.json()["user"]["must_change_password"]
        assert u.get("/api/ranges").status_code == 200


def test_notification_secrets_are_encrypted_and_masked(env):
    app, wcfg = env
    url = "https://discord.com/api/webhooks/123/very-secret-token"
    with client(app) as c:
        me, _ = enroll(c, "admin")
        h = {"X-CSRF-Token": me["csrf"]}
        body = {"channels": [{"type": "discord", "name": "ops", "events": "all", "config": {"url": url}}]}
        r = c.put("/api/settings/notifications", json=body, headers=h)
        assert r.status_code == 200 and r.json()["channels"][0]["config"]["url"] == "********"
        ch = r.json()["channels"][0]
        # saving the masked value again keeps the stored secret
        assert c.put("/api/settings/notifications", json={"channels": [ch]}, headers=h).status_code == 200
        bad = {"channels": [{"type": "slack", "name": "x", "config": {"url": "http://insecure"}}]}
        assert c.put("/api/settings/notifications", json=bad, headers=h).status_code == 400
    conn = db.connect(wcfg.db)
    raw = conn.execute("SELECT value FROM settings WHERE key='notification_channels'").fetchone()[0]
    assert "very-secret-token" not in raw
    from valor.web import notify
    plain = notify.channels_plain(db.get_setting(conn, "notification_channels"), app.state.box)
    assert plain[0]["config"]["url"] == url


def test_ldap_settings_validation(env):
    app, _ = env
    with client(app) as c:
        me, _ = enroll(c, "admin")
        h = {"X-CSRF-Token": me["csrf"]}
        base = {"enabled": True, "url": "ldap://dc.example.com", "bind_dn": "cn=svc", "bind_password": "pw",
                "user_base": "dc=example,dc=com", "group_admin": "cn=admins,dc=example,dc=com"}
        assert c.put("/api/settings/ldap", json=base, headers=h).status_code == 400          # plain LDAP
        r = c.put("/api/settings/ldap", json={**base, "url": "ldaps://dc.example.com"}, headers=h)
        assert r.status_code == 200 and r.json()["bind_password"] == "********"


def test_ranges_without_cluster(env):
    app, _ = env
    with client(app) as c:
        me, _ = enroll(c, "olivia")
        r = c.get("/api/ranges")
        assert r.status_code == 200
        body = r.json()
        assert {"web2tier", "pentest", "devenv"} <= {x["name"] for x in body["ranges"]}
        assert body["cluster_error"]                       # no token in the test: reported, not fatal
        spec = (ROOT / "ranges" / "web2tier.yaml").read_text()
        r = c.post("/api/specs/check", json={"yaml": spec}, headers={"X-CSRF-Token": me["csrf"]})
        assert r.status_code == 200 and r.json()["topology"]["nodes"][0]["id"] == "internet"
        r = c.post("/api/specs/check", json={"yaml": "name: [oops"}, headers={"X-CSRF-Token": me["csrf"]})
        assert r.json()["ok"] is False
        r = c.put("/api/ranges/other/spec", json={"yaml": spec}, headers={"X-CSRF-Token": me["csrf"]})
        assert r.status_code == 400 and r.json()["error"] == "name_mismatch"
        assert c.get("/api/ranges/Bad_Name").status_code == 400


def test_viewer_cannot_change_ranges(env):
    app, _ = env
    with client(app) as c:
        me, _ = enroll(c, "vic")
        spec = (ROOT / "ranges" / "web2tier.yaml").read_text()
        r = c.post("/api/specs/check", json={"yaml": spec}, headers={"X-CSRF-Token": me["csrf"]})
        assert r.status_code == 403
        r = c.post("/api/ranges/web2tier/destroy", json={"confirm": "web2tier"}, headers={"X-CSRF-Token": me["csrf"]})
        assert r.status_code == 403


def test_ldap_authentication_with_mock_directory(monkeypatch):
    from ldap3 import MOCK_SYNC, OFFLINE_AD_2012_R2, Connection, Server
    from valor.web import ldapauth
    server = Server("fake-dc", get_info=OFFLINE_AD_2012_R2)
    setup = Connection(server, user="cn=svc,dc=ex,dc=com", password="svcpw", client_strategy=MOCK_SYNC)
    setup.strategy.add_entry("cn=svc,dc=ex,dc=com", {"userPassword": "svcpw", "objectClass": "user",
                                                     "sAMAccountName": "svc"})
    setup.strategy.add_entry("cn=Alice,ou=people,dc=ex,dc=com", {
        "userPassword": "alicepw", "objectClass": "user", "sAMAccountName": "alice", "displayName": "Alice A",
        "mail": "alice@ex.com", "memberOf": ["cn=valor-ops,ou=groups,dc=ex,dc=com"]})
    setup.strategy.add_entry("cn=Bob,ou=people,dc=ex,dc=com", {
        "userPassword": "bobpw", "objectClass": "user", "sAMAccountName": "bob", "memberOf": ["cn=other,dc=ex,dc=com"]})
    monkeypatch.setattr(ldapauth, "CLIENT_STRATEGY", MOCK_SYNC)
    cfg = {**ldapauth.DEFAULTS, "enabled": True, "url": "ldaps://fake-dc", "bind_dn": "cn=svc,dc=ex,dc=com",
           "bind_password": "svcpw", "user_base": "dc=ex,dc=com", "nested_groups": False,
           "group_operator": "cn=valor-ops,ou=groups,dc=ex,dc=com"}
    res = ldapauth.authenticate(cfg, "alice", "alicepw", server=server)
    assert res.ok and res.role == "operator" and res.display_name == "Alice A" and res.email == "alice@ex.com"
    assert ldapauth.authenticate(cfg, "alice", "wrong", server=server).reason == "wrong password"
    assert ldapauth.authenticate(cfg, "alice", "", server=server).reason == "empty password"
    assert not ldapauth.authenticate(cfg, "al*", "alicepw", server=server).ok          # filter input is escaped
    assert ldapauth.authenticate(cfg, "bob", "bobpw", server=server).reason == "not in any VALOR group"
    assert ldapauth.test_connection(cfg, "alice", server=server)["ok"]


def test_range_logins_power_and_snapshot_endpoints(env):
    app, wcfg = env
    from valor import credentials, jobs
    cfg = app.state.cfg
    with client(app) as op, client(app) as viewer:
        me, _ = enroll(op, "olivia")
        h = {"X-CSRF-Token": me["csrf"]}
        vme, _ = enroll(viewer, "vic")
        assert op.get("/api/ranges/web2tier/credentials").status_code == 404           # not built yet
        login = credentials.ensure(cfg, "web2tier")
        r = op.get("/api/ranges/web2tier/credentials")
        assert r.status_code == 200 and r.json()["password"] == login["password"]
        assert viewer.get("/api/ranges/web2tier/credentials").status_code == 403
        audit = db.connect(wcfg.db).execute("SELECT COUNT(*) FROM audit WHERE action='range.credentials.view'").fetchone()[0]
        assert audit == 1
        j = op.post("/api/ranges/web2tier/power", json={"action": "reboot", "hosts": ["web"]}, headers=h)
        assert j.status_code == 200 and j.json()["state"] == "queued"
        assert jobs.status(cfg, j.json()["job"])["target"] == {"range": "web2tier", "action": "reboot", "hosts": ["web"]}
        assert op.post("/api/ranges/web2tier/power", json={"action": "suspend"}, headers=h).status_code == 422
        assert op.post("/api/ranges/web2tier/snapshots", json={"name": "valor-clean"}, headers=h).status_code == 400
        assert op.post("/api/ranges/web2tier/snapshots", json={"name": "a b"}, headers=h).status_code == 422
        assert op.post("/api/ranges/web2tier/snapshots", json={"name": "before-test"}, headers=h).status_code == 200
        r = op.post("/api/ranges/web2tier/snapshots/valor-clean/rollback", json={"confirm": "nope"}, headers=h)
        assert r.status_code == 400
        r = op.post("/api/ranges/web2tier/snapshots/valor-clean/rollback", json={"confirm": "web2tier"}, headers=h)
        assert r.status_code == 200
        assert viewer.post("/api/ranges/web2tier/power", json={"action": "start"},
                           headers={"X-CSRF-Token": vme["csrf"]}).status_code == 403
        assert op.post("/api/ranges/web2tier/credentials/rotate", headers=h).status_code == 200
