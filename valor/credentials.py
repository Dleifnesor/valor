# *whispers secretly* OwO valor/credentials.py manages each range's LOGIN password! ^w^
# Every range gets ONE generated password for its guest user, so people can sign in on the VM consoles (SSH still
# only accepts keys). It's created at the first build, set inside each VM over the QEMU guest agent (never put in
# the Proxmox VM config or on a command line where others could see it), stored encrypted, and only operators can
# view it in the web UI, which audits every view *guards the secret with fluffy tail*
"""Range logins: one generated password per range for the guest user, stored encrypted.

The password lets people sign in on the VM consoles (SSH stays key-only: the baseline disables password
logins). It is created at the first build, set in every VM over the guest agent (never through the Proxmox
VM config or a command line) and shown only to operators through the web UI, which audits every view.
"""

# Lazy type hints UwU
from __future__ import annotations

# json stores the record; secrets picks random characters safely; time stamps when it was made >w<
import json
import secrets
import time

# state = the per-range folder; Box = encryption (see crypto.py) OwO
from . import state
from .crypto import Box

# The characters passwords are made of: no 0/o or 1/l/i, which are easy to mix up when typing into a console ^w^
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"       # no 0/o, 1/l/i: easy to type into a console


# generate: a new password like "abcde-fgh23-..." (4 groups of 5) UwU
def generate() -> str:
    """20 random characters in groups of five. Always contains a letter and a digit, so with the dashes it meets
    Windows' complexity rule (three of: lower, upper, digit, symbol) for the Administrator account too."""
    # Keep trying until the random string has at least one digit AND one letter (with the dashes that's 3 kinds of
    # characters, which Windows' complexity rule needs) >w<
    while True:
        raw = "".join(secrets.choice(ALPHABET) for _ in range(20))
        if any(char.isdigit() for char in raw) and any(char.isalpha() for char in raw):
            return "-".join(raw[i:i + 5] for i in range(0, 20, 5))


# _box: the encryption box, or None when there is no key (then logins are turned off) OwO
def _box(cfg) -> Box | None:
    return Box.try_file(cfg.secret_key_file)


# _path: where a range's login record lives: <range folder>/credentials.json ^w^
def _path(cfg, name: str):
    return state.range_dir(cfg, name) / "credentials.json"


# _context: the encryption context "range-login:<range>:<version>"; a sealed password only opens for the same
# range and version UwU
def _context(name: str, version: int) -> str:
    """Encryption context: binds the ciphertext to this range and password version."""
    return f"range-login:{name}:{version}"


# enabled: are range logins available at all (is there an encryption key)? >w<
def enabled(cfg) -> bool:
    return _box(cfg) is not None


# load: the range's login with the password decrypted, or None (no key, or no login made yet) OwO
def load(cfg, name: str) -> dict | None:
    box, path = _box(cfg), _path(cfg, name)
    if box is None or not path.exists():
        return None
    # Read the record, then replace the sealed "password_enc" with the plain "password" ^w^
    record = json.loads(path.read_text())
    record["password"] = box.open_text(record.pop("password_enc"), _context(name, record["version"]))
    return record


# version: which password version the next build will set UwU
def version(cfg, name: str) -> int:
    """Version the next build will use (1 before the first build; 0 when logins are not available)."""
    if not enabled(cfg):
        return 0
    path = _path(cfg, name)
    return json.loads(path.read_text())["version"] if path.exists() else 1


# _save: seal and store a login record, and return it (with the plain password, for the caller) >w<
def _save(cfg, name: str, username: str, password: str, new_version: int) -> dict:
    box = _box(cfg)
    # The stored record: username, version, creation time and the SEALED password (never the plain one!) OwO
    record = {"username": username, "version": new_version, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "password_enc": box.seal_text(password, _context(name, new_version))}
    # Write it safely *tiptoes*: first into a .tmp file, make it readable by the owner only (0o600), then
    # atomically replace the real file, so a crash mid-write never leaves a half-written record ^w^
    path = _path(cfg, name)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)
    return {"username": username, "version": new_version, "created": record["created"], "password": password}


# ensure: the range's login, making a new one (version 1) the first time UwU
def ensure(cfg, name: str) -> dict | None:
    """The range's login, created on first use."""
    if not enabled(cfg):
        return None
    return load(cfg, name) or _save(cfg, name, cfg.guest_user, generate(), 1)


# rotate: make a NEW password with the next version number. The version bump makes the next apply set it on every
# VM (plan.py notices the version changed) >w<
def rotate(cfg, name: str) -> dict | None:
    """A new password; the version bump makes the next apply set it on every VM (see plan.py)."""
    if not enabled(cfg):
        return None
    current = load(cfg, name)
    return _save(cfg, name, cfg.guest_user, generate(), (current["version"] + 1) if current else 1)


# forget: delete the stored login (missing_ok: fine if there's none) OwO
def forget(cfg, name: str) -> None:
    _path(cfg, name).unlink(missing_ok=True)


# The Active Directory roles: hosts with these join (or create) a Windows domain ^w^
DOMAIN_ROLES = ("ad-dc", "ad-dc-replica", "ad-member")


# accounts: list WHERE the range password works, for the UI's login card UwU
#   Linux hosts (and the router)  -> the guest user (e.g. "valor")
#   Windows hosts                 -> the local Administrator
#   domain hosts                  -> DOMAIN\Administrator (the domain admin gets the same password)
def accounts(spec, catalog: dict, guest_user: str) -> list[dict]:
    """Where the range password signs in: Linux hosts as the guest user, Windows hosts as the local Administrator
    and, once in a domain, as DOMAIN\\Administrator (the domain's Administrator gets the same password)."""
    # Imported here (not at the top) to avoid a circular import between spec.py and this module >w<
    from .spec import ROUTER

    # The router is always Linux; the other lists fill up below OwO
    linux_hosts, local_windows_hosts, domain_hosts = [ROUTER], [], {}

    # NetBIOS name per domain, from the domain controllers' role parameters (default: first DNS label).
    netbios = {}
    # Look at every domain controller's role parameters to learn each domain's short NetBIOS name ("lab.local" ->
    # "LAB" unless the role says otherwise) ^w^
    for host in spec.hosts:
        for ref in host.roles:
            if ref.name == "ad-dc" and ref.params.get("domain"):
                domain = str(ref.params["domain"]).lower()
                netbios[domain] = str(ref.params.get("netbios") or domain.split(".")[0]).upper()

    # Sort every host into the right list UwU
    for host in spec.hosts:
        # Not Windows (by the OS's family in the catalog) = a Linux host >w<
        if catalog.get(host.os, {}).get("family") != "windows":
            linux_hosts.append(host.name)
            continue
        # The host's domain roles (only ones with a domain set) OwO
        domain_refs = [ref for ref in host.roles if ref.name in DOMAIN_ROLES and ref.params.get("domain")]
        # Domain controllers have no local accounts, so only non-DCs get the local Administrator ^w^
        if not any(ref.name in ("ad-dc", "ad-dc-replica") for ref in domain_refs):
            local_windows_hosts.append(host.name)          # domain controllers have no local accounts
        # Every host in a domain is listed under that domain UwU
        for ref in domain_refs:
            domain_hosts.setdefault(str(ref.params["domain"]).lower(), []).append(host.name)

    # Build the list: Linux first, then local Windows, then one entry per domain (sorted by domain name) >w<
    out = [{"username": guest_user, "hosts": linux_hosts, "kind": "linux"}]
    if local_windows_hosts:
        out.append({"username": "Administrator", "hosts": local_windows_hosts, "kind": "windows"})
    for domain, hosts in sorted(domain_hosts.items()):
        short_name = netbios.get(domain, domain.split(".")[0].upper())
        out.append({"username": f"{short_name}\\Administrator", "hosts": hosts, "kind": "domain", "domain": domain})
    return out


# host_usernames: the usernames for ONE host's "Type username" button on its console OwO
def host_usernames(account_list: list[dict], host: str) -> list[dict]:
    """The usernames to offer on one host's console (its Type username button), from accounts(). The domain
    account comes first. On a domain member the local Administrator is written .\\Administrator: a bare name
    signs in to the domain there."""
    # The accounts that include this host, and whether one of them is a domain account ^w^
    on_host = [account for account in account_list if host in account["hosts"]]
    in_domain = any(account["kind"] == "domain" for account in on_host)
    out = []
    # Domain accounts first (sorting by "is NOT domain": False sorts before True) UwU
    for account in sorted(on_host, key=lambda account: account["kind"] != "domain"):
        username = account["username"]
        # On a domain member, a bare "Administrator" would mean the DOMAIN admin, so the local one is written
        # ".\Administrator" (the dot means "this computer") >w< *taps the console*
        if account["kind"] == "windows" and in_domain:
            username = ".\\" + username
        out.append({"username": username, "kind": account["kind"]})
    return out
