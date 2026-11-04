# *knocks on the directory's door* OwO valor/web/ldapauth.py lets people sign in with their company accounts
# (LDAP / Active Directory)! ^w^
# How a sign-in works: VALOR binds (signs in) as a SERVICE account, searches for the user, then binds AS the user
# with the typed password (that's how the password is checked). The user's groups decide their VALOR role
# (admin / operator / viewer); someone in none of the groups can't sign in *wags tail*
"""LDAP / Active Directory sign-in: service-account search, then a bind as the user; groups map to roles."""

# Lazy type hints UwU
from __future__ import annotations

# ssl for certificate checking; dataclass for the result; Box decrypts the stored bind password >w<
import ssl
from dataclasses import dataclass

from .security import Box

# DEFAULTS: every LDAP setting and its default (the admin edits these in Settings) OwO
#   url / starttls     the server; ldaps:// (TLS from the start) or ldap:// + StartTLS. Plain LDAP is refused.
#   ca_pem             the CA that signed the directory's certificate (empty = trust the system's CAs)
#   bind_dn / _password the service account (the password is stored encrypted)
#   user_base          where to search for users; user_filter = how to find one ({username} is filled in)
#   attr_*             which attributes hold the display name and email
#   group_*            the directory group for each VALOR role
#   nested_groups      also count membership through groups inside groups (Active Directory only)
#   timeout            seconds to wait for the server ^w^
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
# SECRET_FIELDS: settings stored encrypted. _IN_CHAIN is Active Directory's special "matching rule in chain" id
# that makes a member search follow nested groups. CLIENT_STRATEGY lets tests swap in ldap3's fake server UwU
SECRET_FIELDS = ("bind_password",)
_IN_CHAIN = "1.2.840.113556.1.4.1941"
CLIENT_STRATEGY = None          # ldap3 default (SYNC); tests use MOCK_SYNC


# LdapResult: the outcome of a sign-in. ok, a reason for the log when it failed, and the user's DN (directory
# path), display name, email and VALOR role >w<
@dataclass
class LdapResult:
    ok: bool
    reason: str = ""
    dn: str = ""
    display_name: str = ""
    email: str = ""
    role: str | None = None


# _server: describe the directory server for ldap3 (imported inside the function so ldap3 is only needed when
# LDAP is actually used) OwO
def _server(cfg: dict):
    from ldap3 import Server, Tls
    # Only ldap:// or ldaps:// URLs ^w^
    if not cfg["url"].startswith(("ldaps://", "ldap://")):
        raise ValueError("the URL must start with ldaps:// or ldap://")
    # TLS settings: the server's certificate MUST be valid (CERT_REQUIRED), checked against the given CA or the
    # system's CAs UwU
    tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT, ca_certs_data=cfg["ca_pem"] or None)
    # No TLS at all (ldap:// without StartTLS) would send passwords in the clear: refused! *hisses at plain LDAP* >w<
    if not cfg["url"].startswith("ldaps://") and not cfg["starttls"]:
        raise ValueError("plain LDAP is not allowed: use ldaps:// or enable StartTLS")
    # use_ssl for ldaps://; get_info="NO_INFO" skips reading the server's schema (faster, and not needed) OwO
    return Server(cfg["url"], use_ssl=cfg["url"].startswith("ldaps://"), tls=tls, connect_timeout=cfg["timeout"],
                  get_info="NO_INFO")


# _connect: open a connection that will bind as `user` with `password` ^w^
def _connect(cfg: dict, user: str, password: str, server=None):
    from ldap3 import SYNC, Connection
    # raise_exceptions=False: failed binds/searches return False instead of raising; auto_referrals=False: don't
    # follow referrals to other servers (they'd get our credentials) UwU
    conn = Connection(server or _server(cfg), user=user, password=password, receive_timeout=cfg["timeout"],
                      raise_exceptions=False, auto_referrals=False, client_strategy=CLIENT_STRATEGY or SYNC)
    # With ldap:// + StartTLS: open the connection and upgrade it to TLS BEFORE any password is sent >w<
    if cfg["url"].startswith("ldap://") and cfg["starttls"]:
        conn.open()
        if not conn.start_tls():
            raise ValueError("StartTLS failed")
    return conn


# settings_plain: the stored LDAP settings over the defaults, with the bind password decrypted OwO
def settings_plain(stored: dict, box: Box) -> dict:
    cfg = {**DEFAULTS, **(stored or {})}
    for f in SECRET_FIELDS:
        if cfg.get(f):
            cfg[f] = box.open_text(cfg[f], f"ldap:{f}")
    return cfg


# _role_for: which VALOR role the user gets (the highest: admin, then operator, then viewer), or None ^w^
def _role_for(conn, cfg: dict, user_dn: str, member_of: list[str]) -> str | None:
    from ldap3.utils.conv import escape_filter_chars
    # The groups the user is directly in (memberOf), lower-cased so the comparison ignores case UwU
    lowered = {g.lower() for g in member_of}
    for role in ("admin", "operator", "viewer"):
        # The group set for this role (skip roles without one) >w<
        group = cfg.get(f"group_{role}", "").strip()
        if not group:
            continue
        # Directly in the group? That's the role OwO
        if group.lower() in lowered:
            return role
        # Nested groups: ask the directory "is this user a member of this group through any chain of groups?" using
        # the in-chain rule on the group itself (BASE scope = just that one object). The user's DN is escaped so special
        # characters can't change the filter ^w^
        if cfg["nested_groups"]:
            ok = conn.search(group, f"(member:{_IN_CHAIN}:={escape_filter_chars(user_dn)})", search_scope="BASE",
                             attributes=["cn"])
            if ok and conn.entries:
                return role
    return None


# authenticate: sign a user in against the directory. Wrong credentials never raise; they give ok=False with a
# reason for the audit log UwU
def authenticate(cfg: dict, username: str, password: str, server=None) -> LdapResult:
    """cfg: decrypted settings. Never raises for wrong credentials; returns ok=False with a reason for the log."""
    from ldap3.utils.conv import escape_filter_chars
    # LDAP turned off = no LDAP sign-ins >w<
    if not cfg.get("enabled"):
        return LdapResult(False, "ldap disabled")
    # An EMPTY password must be refused here: many directories treat a bind with an empty password as an ANONYMOUS
    # bind that "succeeds"! *gasps* OwO
    if not password:
        return LdapResult(False, "empty password")      # an empty password would be an anonymous bind
    try:
        # Bind as the service account ^w^
        svc = _connect(cfg, cfg["bind_dn"], cfg["bind_password"], server)
        if not svc.bind():
            return LdapResult(False, "service account bind failed")
        # Build the search filter with the username ESCAPED (so a username like "*)(x=*" can't inject filter logic) UwU
        flt = cfg["user_filter"].replace("{username}", escape_filter_chars(username))
        attrs = [cfg["attr_display_name"], cfg["attr_email"], "memberOf"]
        # Search for exactly ONE user (size_limit=2 is enough to notice "more than one"); zero or several = refuse >w<
        if not svc.search(cfg["user_base"], flt, attributes=attrs, size_limit=2) or len(svc.entries) != 1:
            return LdapResult(False, "user not found or not unique")
        # The user's entry and DN OwO
        entry = svc.entries[0]
        dn = entry.entry_dn
        # Now bind AS the user with the typed password: success means the password is right ^w^
        user_conn = _connect(cfg, dn, password, server)
        if not user_conn.bind():
            return LdapResult(False, "wrong password", dn=dn)
        user_conn.unbind()

        # attr(): read one attribute as a single string, whatever shape the directory returned UwU
        def attr(name):
            """A user attribute as one string (directories may return a list or nothing)."""
            value = entry[name].value if name in entry.entry_attributes else None
            return value if isinstance(value, str) else (value[0] if isinstance(value, list) and value else "")

        # The user's groups, and the role they map to; then close the service connection >w<
        member_of = entry["memberOf"].values if "memberOf" in entry.entry_attributes else []
        role = _role_for(svc, cfg, dn, list(member_of))
        svc.unbind()
        # Valid password but in no VALOR group = no entry *politely blocks the door* OwO
        if role is None:
            return LdapResult(False, "not in any VALOR group", dn=dn)
        # Success: everything the web app needs to create/update the local account ^w^
        return LdapResult(True, dn=dn, display_name=attr(cfg["attr_display_name"]), email=attr(cfg["attr_email"]),
                          role=role)
    # Network, TLS or protocol trouble: a failed result with the error's type and text UwU
    except Exception as e:   # network, TLS or protocol errors
        return LdapResult(False, f"directory error: {e.__class__.__name__}: {e}")


# test_connection: the "Test" button in Settings. Binds with the service account and, if a username is given,
# looks that user up and says which role they'd get >w<
def test_connection(cfg: dict, username: str = "", server=None) -> dict:
    from ldap3.utils.conv import escape_filter_chars
    try:
        svc = _connect(cfg, cfg["bind_dn"], cfg["bind_password"], server)
        if not svc.bind():
            return {"ok": False, "message": "The service account could not sign in (check the bind DN and password)."}
        out = {"ok": True, "message": "Connected and signed in with the service account."}
        # Optional user lookup, same escaping and search as a real sign-in OwO
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
        # Close the connection and return the result; any error becomes an ok=False message ^w^
        return out
    except Exception as e:
        return {"ok": False, "message": f"{e.__class__.__name__}: {e}"}
