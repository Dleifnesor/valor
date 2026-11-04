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
        if any(char.isdigit() for char in raw) and any(char.isalpha() for char in raw):
            return "-".join(raw[i:i + 5] for i in range(0, 20, 5))


def _box(cfg) -> Box | None:
    return Box.try_file(cfg.secret_key_file)


def _path(cfg, name: str):
    return state.range_dir(cfg, name) / "credentials.json"


def _context(name: str, version: int) -> str:
    """Encryption context: binds the ciphertext to this range and password version."""
    return f"range-login:{name}:{version}"


def enabled(cfg) -> bool:
    return _box(cfg) is not None


def load(cfg, name: str) -> dict | None:
    box, path = _box(cfg), _path(cfg, name)
    if box is None or not path.exists():
        return None
    record = json.loads(path.read_text())
    record["password"] = box.open_text(record.pop("password_enc"), _context(name, record["version"]))
    return record


def version(cfg, name: str) -> int:
    """Version the next build will use (1 before the first build; 0 when logins are not available)."""
    if not enabled(cfg):
        return 0
    path = _path(cfg, name)
    return json.loads(path.read_text())["version"] if path.exists() else 1


def _save(cfg, name: str, username: str, password: str, new_version: int) -> dict:
    box = _box(cfg)
    record = {"username": username, "version": new_version, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "password_enc": box.seal_text(password, _context(name, new_version))}
    path = _path(cfg, name)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)
    return {"username": username, "version": new_version, "created": record["created"], "password": password}


def ensure(cfg, name: str) -> dict | None:
    """The range's login, created on first use."""
    if not enabled(cfg):
        return None
    return load(cfg, name) or _save(cfg, name, cfg.guest_user, generate(), 1)


def rotate(cfg, name: str) -> dict | None:
    """A new password; the version bump makes the next apply set it on every VM (see plan.py)."""
    if not enabled(cfg):
        return None
    current = load(cfg, name)
    return _save(cfg, name, cfg.guest_user, generate(), (current["version"] + 1) if current else 1)


def forget(cfg, name: str) -> None:
    _path(cfg, name).unlink(missing_ok=True)


DOMAIN_ROLES = ("ad-dc", "ad-dc-replica", "ad-member")


def accounts(spec, catalog: dict, guest_user: str) -> list[dict]:
    """Where the range password signs in: Linux hosts as the guest user, Windows hosts as the local Administrator
    and, once in a domain, as DOMAIN\\Administrator (the domain's Administrator gets the same password)."""
    from .spec import ROUTER

    linux_hosts, local_windows_hosts, domain_hosts = [ROUTER], [], {}

    # NetBIOS name per domain, from the domain controllers' role parameters (default: first DNS label).
    netbios = {}
    for host in spec.hosts:
        for ref in host.roles:
            if ref.name == "ad-dc" and ref.params.get("domain"):
                domain = str(ref.params["domain"]).lower()
                netbios[domain] = str(ref.params.get("netbios") or domain.split(".")[0]).upper()

    for host in spec.hosts:
        if catalog.get(host.os, {}).get("family") != "windows":
            linux_hosts.append(host.name)
            continue
        domain_refs = [ref for ref in host.roles if ref.name in DOMAIN_ROLES and ref.params.get("domain")]
        if not any(ref.name in ("ad-dc", "ad-dc-replica") for ref in domain_refs):
            local_windows_hosts.append(host.name)          # domain controllers have no local accounts
        for ref in domain_refs:
            domain_hosts.setdefault(str(ref.params["domain"]).lower(), []).append(host.name)

    out = [{"username": guest_user, "hosts": linux_hosts, "kind": "linux"}]
    if local_windows_hosts:
        out.append({"username": "Administrator", "hosts": local_windows_hosts, "kind": "windows"})
    for domain, hosts in sorted(domain_hosts.items()):
        short_name = netbios.get(domain, domain.split(".")[0].upper())
        out.append({"username": f"{short_name}\\Administrator", "hosts": hosts, "kind": "domain", "domain": domain})
    return out


def host_usernames(account_list: list[dict], host: str) -> list[dict]:
    """The usernames to offer on one host's console (its Type username button), from accounts(). The domain
    account comes first. On a domain member the local Administrator is written .\\Administrator: a bare name
    signs in to the domain there."""
    on_host = [account for account in account_list if host in account["hosts"]]
    in_domain = any(account["kind"] == "domain" for account in on_host)
    out = []
    for account in sorted(on_host, key=lambda account: account["kind"] != "domain"):
        username = account["username"]
        if account["kind"] == "windows" and in_domain:
            username = ".\\" + username
        out.append({"username": username, "kind": account["kind"]})
    return out
