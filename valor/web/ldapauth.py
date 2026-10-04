"""LDAP / Active Directory sign-in: service-account search, then a bind as the user; groups map to roles."""

from __future__ import annotations

import ssl
from dataclasses import dataclass

from .security import Box

DEFAULTS = {
    "enabled": False,
    "url": "",                     # ldaps://dc1.example.com  or  ldap://dc1.example.com (with starttls)
    "starttls": False,
    "ca_pem": "",                  # CA that signed the directory's certificate (empty: system CAs)
    "bind_dn": "",
    "bind_password": "",           # stored encrypted
    "user_base": "",
    "user_filter": "(&(objectClass=user)(sAMAccountName={username}))",
    "attr_display_name": "displayName",
    "attr_email": "mail",
    "group_admin": "",
    "group_operator": "",
    "group_viewer": "",
    "nested_groups": True,         # Active Directory LDAP_MATCHING_RULE_IN_CHAIN
    "timeout": 8,
}
SECRET_FIELDS = ("bind_password",)
_IN_CHAIN = "1.2.840.113556.1.4.1941"
CLIENT_STRATEGY = None          # ldap3 default (SYNC); tests use MOCK_SYNC


@dataclass
class LdapResult:
    ok: bool
    reason: str = ""
    dn: str = ""
    display_name: str = ""
    email: str = ""
    role: str | None = None


def _server(cfg: dict):
    from ldap3 import Server, Tls
    if not cfg["url"].startswith(("ldaps://", "ldap://")):
        raise ValueError("the URL must start with ldaps:// or ldap://")
    tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT, ca_certs_data=cfg["ca_pem"] or None)
    if not cfg["url"].startswith("ldaps://") and not cfg["starttls"]:
        raise ValueError("plain LDAP is not allowed: use ldaps:// or enable StartTLS")
    return Server(cfg["url"], use_ssl=cfg["url"].startswith("ldaps://"), tls=tls, connect_timeout=cfg["timeout"],
                  get_info="NO_INFO")


def _connect(cfg: dict, user: str, password: str, server=None):
    from ldap3 import SYNC, Connection
    conn = Connection(server or _server(cfg), user=user, password=password, receive_timeout=cfg["timeout"],
                      raise_exceptions=False, auto_referrals=False, client_strategy=CLIENT_STRATEGY or SYNC)
    if cfg["url"].startswith("ldap://") and cfg["starttls"]:
        conn.open()
        if not conn.start_tls():
            raise ValueError("StartTLS failed")
    return conn


def settings_plain(stored: dict, box: Box) -> dict:
    cfg = {**DEFAULTS, **(stored or {})}
    for f in SECRET_FIELDS:
        if cfg.get(f):
            cfg[f] = box.open_text(cfg[f], f"ldap:{f}")
    return cfg


def _role_for(conn, cfg: dict, user_dn: str, member_of: list[str]) -> str | None:
    from ldap3.utils.conv import escape_filter_chars
    lowered = {g.lower() for g in member_of}
    for role in ("admin", "operator", "viewer"):
        group = cfg.get(f"group_{role}", "").strip()
        if not group:
            continue
        if group.lower() in lowered:
            return role
        if cfg["nested_groups"]:
            ok = conn.search(group, f"(member:{_IN_CHAIN}:={escape_filter_chars(user_dn)})", search_scope="BASE",
                             attributes=["cn"])
            if ok and conn.entries:
                return role
    return None


def authenticate(cfg: dict, username: str, password: str, server=None) -> LdapResult:
    """cfg: decrypted settings. Never raises for wrong credentials; returns ok=False with a reason for the log."""
    from ldap3.utils.conv import escape_filter_chars
    if not cfg.get("enabled"):
        return LdapResult(False, "ldap disabled")
    if not password:
        return LdapResult(False, "empty password")      # an empty password would be an anonymous bind
    try:
        svc = _connect(cfg, cfg["bind_dn"], cfg["bind_password"], server)
        if not svc.bind():
            return LdapResult(False, "service account bind failed")
        flt = cfg["user_filter"].replace("{username}", escape_filter_chars(username))
        attrs = [cfg["attr_display_name"], cfg["attr_email"], "memberOf"]
        if not svc.search(cfg["user_base"], flt, attributes=attrs, size_limit=2) or len(svc.entries) != 1:
            return LdapResult(False, "user not found or not unique")
        entry = svc.entries[0]
        dn = entry.entry_dn
        user_conn = _connect(cfg, dn, password, server)
        if not user_conn.bind():
            return LdapResult(False, "wrong password", dn=dn)
        user_conn.unbind()

        def attr(name):
            v = entry[name].value if name in entry.entry_attributes else None
            return v if isinstance(v, str) else (v[0] if isinstance(v, list) and v else "")

        member_of = entry["memberOf"].values if "memberOf" in entry.entry_attributes else []
        role = _role_for(svc, cfg, dn, list(member_of))
        svc.unbind()
        if role is None:
            return LdapResult(False, "not in any VALOR group", dn=dn)
        return LdapResult(True, dn=dn, display_name=attr(cfg["attr_display_name"]), email=attr(cfg["attr_email"]),
                          role=role)
    except Exception as e:   # network, TLS or protocol errors
        return LdapResult(False, f"directory error: {e.__class__.__name__}: {e}")


def test_connection(cfg: dict, username: str = "", server=None) -> dict:
    from ldap3.utils.conv import escape_filter_chars
    try:
        svc = _connect(cfg, cfg["bind_dn"], cfg["bind_password"], server)
        if not svc.bind():
            return {"ok": False, "message": "The service account could not sign in (check the bind DN and password)."}
        out = {"ok": True, "message": "Connected and signed in with the service account."}
        if username:
            flt = cfg["user_filter"].replace("{username}", escape_filter_chars(username))
            svc.search(cfg["user_base"], flt, attributes=["memberOf"], size_limit=2)
            if len(svc.entries) != 1:
                out.update(ok=False, message=f"Signed in, but found {len(svc.entries)} entries for '{username}'.")
            else:
                dn = svc.entries[0].entry_dn
                member_of = svc.entries[0]["memberOf"].values if "memberOf" in svc.entries[0].entry_attributes else []
                role = _role_for(svc, cfg, dn, list(member_of))
                out["message"] += f" Found {dn}; VALOR role: {role or 'none (not in any VALOR group)'}."
        svc.unbind()
        return out
    except Exception as e:
        return {"ok": False, "message": f"{e.__class__.__name__}: {e}"}
