"""Web service settings: the [web] section of /etc/valor/config.toml (written by the installer)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from ..config import CONFIG_PATH


@dataclass(frozen=True)
class WebConfig:
    db: str = "/var/lib/valor/web.sqlite3"
    secret_key_file: str = "/etc/valor/secret.key"
    instance_name: str = "VALOR"            # shown in the UI and as the authenticator app's issuer
    session_idle_minutes: int = 30
    session_max_hours: int = 12
    pending_minutes: int = 10               # password accepted, MFA not yet done
    lockout_threshold: int = 5              # failed passwords or codes in a row
    lockout_minutes: int = 15
    ip_attempts: int = 20                   # failed sign-ins per client address ...
    ip_window_minutes: int = 10             # ... per this many minutes
    secure_cookies: bool = True             # False only for local development over plain HTTP
    tls_cert: str = "/etc/valor/tls/server.crt"
    ca_cert: str = "/etc/valor/tls/ca.crt"
    appliance_vmid: int = 0                 # the VALOR VM itself: the health page checks the token can't touch it
    public_url: str = ""                    # used in notification links

    @property
    def cookie_name(self) -> str:
        return "__Host-valor" if self.secure_cookies else "valor_session"


def load_web_config(path: Path | None = None) -> WebConfig:
    path = path or CONFIG_PATH
    if not path.exists():
        return WebConfig()
    with path.open("rb") as fh:
        raw = tomllib.load(fh).get("web", {})
    known = {f.name for f in fields(WebConfig)}
    return WebConfig(**{k: v for k, v in raw.items() if k in known})
