# OwO *pads in* hewwo! valor/web/config.py reads the web service's settings ^w^
# The installer writes /etc/valor/config.toml; the [web] table in it holds things like where the database lives,
# how long sign-in sessions last and the lockout rules. This file turns that table into a typed, read-only
# WebConfig object, with a sensible default for every field so a missing setting never crashes anything *wags tail*
"""Web service settings: the [web] section of /etc/valor/config.toml (written by the installer)."""

# `annotations` from the future makes every type hint a lazy string, so hints like `Path | None` work
# everywhere and cost nothing at import time UwU
from __future__ import annotations

# tomllib = Python's built-in TOML reader (read-only, 3.11+); dataclass/fields build the settings class and
# let us list its field names; Path is for file paths >w<
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

# CONFIG_PATH is the one place that knows the config file's location (/etc/valor/config.toml), shared with the
# engine's config.py so both read the same file *nuzzles*
from ..config import CONFIG_PATH


# WebConfig: one attribute per [web] setting. `frozen=True` makes it immutable: once loaded, nothing can change a
# setting by accident while the service runs ^w^ Each line below is "name: type = default".
@dataclass(frozen=True)
class WebConfig:
    # db = the SQLite file with users, sessions, audit log and settings; secret_key_file = the 32-byte key that
    # encrypts secrets at rest (see valor/crypto.py) OwO
    db: str = "/var/lib/valor/web.sqlite3"
    secret_key_file: str = "/etc/valor/secret.key"
    instance_name: str = "VALOR"            # shown in the UI and as the authenticator app's issuer
    # How long a sign-in lasts: logged out after 30 idle minutes, and after 12 hours no matter what *yawns*
    session_idle_minutes: int = 30
    session_max_hours: int = 12
    pending_minutes: int = 10               # password accepted, MFA not yet done
    # Lockouts: 5 wrong passwords/codes in a row lock the account for 15 minutes >w<
    lockout_threshold: int = 5              # failed passwords or codes in a row
    lockout_minutes: int = 15
    # Per-address throttle: at most 20 failed sign-ins from one client address in any 10-minute window
    # (stops someone guessing many accounts from one place) *guards the door with fluffy tail*
    ip_attempts: int = 20                   # failed sign-ins per client address ...
    ip_window_minutes: int = 10             # ... per this many minutes
    secure_cookies: bool = True             # False only for local development over plain HTTP
    # Where the HTTPS certificate is (the health page shows when it expires) ^w^
    tls_cert: str = "/etc/valor/tls/server.crt"
    appliance_vmid: int = 0                 # the VALOR VM itself: the health page checks the token can't touch it
    public_url: str = ""                    # used in notification links

    # cookie_name: the browser cookie that holds the session. The "__Host-" prefix makes browsers refuse the cookie
    # unless it's Secure, has no Domain and Path=/ (so no other site or subdomain can plant it). Over plain HTTP
    # (local dev only) that prefix can't work, so a plain name is used instead UwU
    @property
    def cookie_name(self) -> str:
        return "__Host-valor" if self.secure_cookies else "valor_session"


# load_web_config: read the [web] table and build a WebConfig. `path` can be given by tests; otherwise the real
# config file is used *wags tail*
def load_web_config(path: Path | None = None) -> WebConfig:
    path = path or CONFIG_PATH
    # No config file at all (a dev machine, a test)? Then every setting is its default OwO
    if not path.exists():
        return WebConfig()
    # TOML must be read as bytes ("rb"); `.get("web", {})` gives an empty table when there's no [web] section >w<
    with path.open("rb") as fh:
        raw = tomllib.load(fh).get("web", {})
    # Keep only keys WebConfig knows about, so an old or extra setting in the file is ignored instead of crashing
    # the service with "unexpected keyword argument" ^w^ *happy wiggle*
    known = {f.name for f in fields(WebConfig)}
    return WebConfig(**{k: v for k, v in raw.items() if k in known})
