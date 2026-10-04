"""Range logins: one generated password per range for the guest user, stored encrypted.

The password lets people sign in on the VM consoles (SSH stays key-only: the baseline disables password
logins). It is created at the first build, set in every VM over the guest agent (never through the Proxmox
VM config or a command line) and shown only to operators through the web UI, which audits every view.
"""

from __future__ import annotations

import json
import secrets
import time

from . import state
from .crypto import Box

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"       # no 0/o, 1/l/i: easy to type into a console


def generate() -> str:
    """20 random characters in groups of five. Always contains a letter and a digit, so with the dashes it meets
    Windows' complexity rule (three of: lower, upper, digit, symbol) for the Administrator account too."""
    while True:
        raw = "".join(secrets.choice(ALPHABET) for _ in range(20))
        if any(c.isdigit() for c in raw) and any(c.isalpha() for c in raw):
            return "-".join(raw[i:i + 5] for i in range(0, 20, 5))


def _box(cfg) -> Box | None:
    return Box.try_file(cfg.secret_key_file)


def _path(cfg, name: str):
    return state.range_dir(cfg, name) / "credentials.json"


def enabled(cfg) -> bool:
    return _box(cfg) is not None


def load(cfg, name: str) -> dict | None:
    box, p = _box(cfg), _path(cfg, name)
    if box is None or not p.exists():
        return None
    rec = json.loads(p.read_text())
    rec["password"] = box.open_text(rec.pop("password_enc"), f"range-login:{name}:{rec['version']}")
    return rec


def version(cfg, name: str) -> int:
    """Version the next build will use (1 before the first build; 0 when logins are not available)."""
    if not enabled(cfg):
        return 0
    p = _path(cfg, name)
    return json.loads(p.read_text())["version"] if p.exists() else 1


def _save(cfg, name: str, username: str, password: str, ver: int) -> dict:
    box = _box(cfg)
    rec = {"username": username, "version": ver, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "password_enc": box.seal_text(password, f"range-login:{name}:{ver}")}
    p = _path(cfg, name)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=2))
    tmp.chmod(0o600)
    tmp.replace(p)
    return {"username": username, "version": ver, "created": rec["created"], "password": password}


def ensure(cfg, name: str) -> dict | None:
    if not enabled(cfg):
        return None
    return load(cfg, name) or _save(cfg, name, cfg.guest_user, generate(), 1)


def rotate(cfg, name: str) -> dict | None:
    if not enabled(cfg):
        return None
    cur = load(cfg, name)
    return _save(cfg, name, cfg.guest_user, generate(), (cur["version"] + 1) if cur else 1)


def forget(cfg, name: str) -> None:
    _path(cfg, name).unlink(missing_ok=True)


DOMAIN_ROLES = ("ad-dc", "ad-dc-replica", "ad-member")


def accounts(spec, catalog: dict, guest_user: str) -> list[dict]:
    """Where the range password signs in: Linux hosts as the guest user, Windows hosts as the local Administrator
    and, once in a domain, as DOMAIN\\Administrator (the domain's Administrator gets the same password)."""
    from .spec import ROUTER

    linux, local, domains = [ROUTER], [], {}
    netbios = {}
    for h in spec.hosts:
        for ref in h.roles:
            if ref.name == "ad-dc" and ref.params.get("domain"):
                d = str(ref.params["domain"]).lower()
                netbios[d] = str(ref.params.get("netbios") or d.split(".")[0]).upper()
    for h in spec.hosts:
        if catalog.get(h.os, {}).get("family") != "windows":
            linux.append(h.name)
            continue
        refs = [r for r in h.roles if r.name in DOMAIN_ROLES and r.params.get("domain")]
        if not any(r.name in ("ad-dc", "ad-dc-replica") for r in refs):
            local.append(h.name)                    # domain controllers have no local accounts
        for r in refs:
            domains.setdefault(str(r.params["domain"]).lower(), []).append(h.name)
    out = [{"username": guest_user, "hosts": linux, "kind": "linux"}]
    if local:
        out.append({"username": "Administrator", "hosts": local, "kind": "windows"})
    for d, hosts in sorted(domains.items()):
        out.append({"username": f"{netbios.get(d, d.split('.')[0].upper())}\\Administrator", "hosts": hosts,
                    "kind": "domain", "domain": d})
    return out
