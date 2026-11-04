# *hands out the smallest possible keys* OwO installer/identity.py: VALOR's LEAST-PRIVILEGE Proxmox identity! ^w^
# Roles, an API user that can only use tokens, a privilege-separated token, pools and the permissions (ACLs) tying them
# together. The token may only touch VMs in VALOR's range pool, clone templates, use VALOR's storage and bridges, and
# read node status. The VALOR VM itself sits in a pool the token has NO rights on, so the AI-driven engine can never
# touch VALOR *wags tail*
"""The least-privilege Proxmox identity: roles, a token-only user, a privilege-separated token, pools and ACLs.

The token may act on VMs in VALOR's range pool, clone from the template pool, allocate disks on the range storage,
attach NICs to the two bridges and read node status - nothing else. The VALOR VM sits in a third pool the token
has no rights on, so the AI-driven engine can never touch VALOR itself.
"""

# Lazy type hints UwU
from __future__ import annotations

# json (the token file), ssl/urllib (checking the token against the local API) >w<
import json
import ssl
import urllib.error
import urllib.request

# ui, the answers, the discovered facts, the record, and the command helpers OwO
from . import ui
from .answers import Answers
from .discover import Facts
from .record import Record
from .sh import CommandError, pvesh

# ENGINE_PRIVS: what the engine may do to range VMs (create, clone, configure, power, console, snapshots). The guest
# agent privileges differ: Proxmox 9 has VM.GuestAgent.*, Proxmox 8 bundles them into VM.Monitor. TOKEN: the token's
# name ^w^
ENGINE_PRIVS = ["Pool.Audit", "VM.Allocate", "VM.Audit", "VM.Clone", "VM.Config.CDROM", "VM.Config.CPU",
                "VM.Config.Cloudinit", "VM.Config.Disk", "VM.Config.HWType", "VM.Config.Memory", "VM.Config.Network",
                "VM.Config.Options", "VM.PowerMgmt", "VM.Console", "VM.Snapshot", "VM.Snapshot.Rollback"]
AGENT_PRIVS_9 = ["VM.GuestAgent.Audit", "VM.GuestAgent.FileRead", "VM.GuestAgent.FileWrite", "VM.GuestAgent.Unrestricted"]
AGENT_PRIVS_8 = ["VM.Monitor"]          # PVE 8: guest agent access is part of VM.Monitor
TOKEN = "appliance"


# roles(): VALOR's roles and their privileges, limited to privileges this Proxmox version knows UwU
def roles(facts: Facts) -> dict[str, list[str]]:
    """VALOR's roles with their privileges, limited to the privileges this Proxmox version knows."""
    agent = AGENT_PRIVS_9 if "VM.GuestAgent.Unrestricted" in facts.privileges else AGENT_PRIVS_8
    # ValorEngine (range VMs), ValorTemplateUser (clone templates), ValorStorage (disks), ValorNodeAudit (read node
    # status), ValorISO/ValorISOReader (the ISO library), ValorDownload (let Proxmox fetch URLs) >w<
    want = {
        "ValorEngine": ENGINE_PRIVS + agent,
        "ValorTemplateUser": ["VM.Audit", "VM.Clone"],
        "ValorStorage": ["Datastore.AllocateSpace", "Datastore.Audit"],
        "ValorNodeAudit": ["Sys.Audit"],
        # ISO library on VALOR's ISO storage: add (AllocateTemplate), list (Audit), delete (Allocate - Proxmox
        # demands it for deleting content; changing the storage's configuration needs it on /storage, not granted)
        "ValorISO": ["Datastore.Allocate", "Datastore.AllocateTemplate", "Datastore.Audit"],
        "ValorISOReader": ["Datastore.Audit"],
        "ValorDownload": ["Sys.AccessNetwork"],                              # Proxmox fetches a URL for VALOR
    }
    # Drop (and warn about) privileges this Proxmox doesn't know OwO
    if facts.privileges:
        for name, privs in want.items():
            missing = [priv for priv in privs if priv not in facts.privileges]
            if missing:
                ui.warn(f"role {name}: this Proxmox VE does not know {missing}; leaving them out")
            want[name] = [priv for priv in privs if priv in facts.privileges]
    return want


# acls(): (path, role) for every permission: the COMPLETE list of what VALOR may do ^w^
def acls(settings: Answers, facts: Facts, bridge: str) -> list[tuple[str, str]]:
    """(path, role) for every permission the user and its token get - the complete list of what VALOR may do."""
    paths = [(f"/pool/{settings.pool_ranges}", "ValorEngine"),
             (f"/pool/{settings.pool_templates}", "ValorTemplateUser"),
             (f"/storage/{settings.range_storage}", "ValorStorage"),
             (f"/sdn/zones/localnetwork/{bridge}", "PVESDNUser"),
             (f"/nodes/{facts.node}", "ValorNodeAudit")]
    # The uplink bridge too, unless it's the same bridge UwU
    uplink = f"/sdn/zones/localnetwork/{settings.vm_bridge}"
    if uplink not in [path for path, _ in paths]:
        paths.append((uplink, "PVESDNUser"))
    # The ISO storage (and downloads on the node), and read-only access to other ISO storages >w<
    if settings.iso_storage:
        paths.append((f"/storage/{settings.iso_storage}", "ValorISO"))
        paths.append((f"/nodes/{facts.node}", "ValorDownload"))
    for storage_id in settings.iso_storages:
        if storage_id != settings.iso_storage and storage_id != settings.range_storage:
            paths.append((f"/storage/{storage_id}", "ValorISOReader"))
    return paths


# plan(): what ensure() would do, for --dry-run OwO
def plan(settings: Answers, facts: Facts, bridge: str) -> list[str]:
    """What ensure() would do, for --dry-run."""
    out = []
    for name, privs in roles(facts).items():
        have = facts.roles.get(name)
        if have is None:
            out.append(f"create role {name} ({', '.join(privs)})")
        elif have != set(privs):
            out.append(f"update role {name} to ({', '.join(privs)})")
    if settings.user not in facts.users:
        out.append(f"create API user {settings.user} (no password; token only)")
    else:
        out.append(f"reuse API user {settings.user}")
    for pool in (settings.pool_ranges, settings.pool_templates, settings.pool_system):
        if pool not in facts.pools:
            out.append(f"create pool {pool}")
    out.append(f"create privilege-separated token {settings.user}!{TOKEN}")
    for path, role in acls(settings, facts, bridge):
        out.append(f"grant {role} on {path}")
    return out


# ensure_roles(): create missing roles and update changed ones (also on --upgrade); remember which VALOR created ^w^
def ensure_roles(facts: Facts, rec: Record) -> None:
    """Create or update VALOR's roles to this version's privileges (also run by --upgrade)."""
    created_roles = rec.objects.get("roles_created", [])
    for name, privs in roles(facts).items():
        have = facts.roles.get(name)
        if have is None:
            pvesh("create", "/access/roles", roleid=name, privs=",".join(privs))
            created_roles.append(name)
            ui.ok(f"role {name}")
        elif have != set(privs):
            pvesh("set", f"/access/roles/{name}", privs=",".join(privs), append=0)
            ui.ok(f"role {name} updated")
    rec.set("roles_created", sorted(set(created_roles)))


# grant(): give every ACL to the user AND its token (a privilege-separated token needs both) UwU
def grant(settings: Answers, facts: Facts, rec: Record, bridge: str, token_id: str) -> None:
    """Grant every ACL to the user and to its token (a privilege-separated token needs both)."""
    granted = []
    for path, role in acls(settings, facts, bridge):
        pvesh("set", "/access/acl", path=path, roles=role, users=settings.user, propagate=1)
        pvesh("set", "/access/acl", path=path, roles=role, tokens=token_id, propagate=1)
        granted.append([path, role])
    rec.set("acls", granted)
    ui.ok(f"{len(granted)} permissions granted to the user and the token")


# ensure(): create/refresh everything and return the new token (its secret goes ONLY into the VALOR VM) >w<
def ensure(settings: Answers, facts: Facts, rec: Record, bridge: str) -> dict:
    """Create/refresh everything; returns {"token_id", "secret"} (the secret goes only into the VALOR VM)."""
    ensure_roles(facts, rec)

    # The API user: create it (no password at all), or reuse ours; refuse a same-named user VALOR didn't make OwO
    if settings.user not in facts.users:
        pvesh("create", "/access/users", userid=settings.user,
              comment=f"VALOR {settings.id}: engine API user (token only, no password)")
        rec.set("user_created", True)
        ui.ok(f"user {settings.user}")
    elif not facts.users[settings.user].startswith("VALOR"):
        raise SystemExit(f"{settings.user} exists but was not made by VALOR (comment: {facts.users[settings.user]!r}); "
                         "choose another id with --id")
    else:
        rec.objects.setdefault("user_created", False)

    # The three pools: ranges, OS templates, and the VALOR VM ^w^
    pools_created = rec.objects.get("pools_created", [])
    pools = ((settings.pool_ranges, "ranges"), (settings.pool_templates, "OS templates"),
             (settings.pool_system, "the VALOR VM"))
    for pool, what in pools:
        if pool not in facts.pools:
            pvesh("create", "/pools", poolid=pool, comment=f"VALOR {settings.id}: {what}")
            pools_created.append(pool)
            ui.ok(f"pool {pool}")
    rec.set("pools_created", sorted(set(pools_created)))

    # A fresh token every run (Proxmox only shows a secret when it's created) UwU
    # A fresh token every run: Proxmox shows a token's secret only when it is created.
    tokens = {token["tokenid"] for token in (pvesh("get", f"/access/users/{settings.user}/token") or [])}
    if TOKEN in tokens:
        pvesh("delete", f"/access/users/{settings.user}/token/{TOKEN}")
    created = pvesh("create", f"/access/users/{settings.user}/token/{TOKEN}", _quiet_secret=True, privsep=1,
                    comment=f"VALOR {settings.id} engine in the VALOR VM")
    token_id, secret = created["full-tokenid"], created["value"]
    rec.set("token", token_id)
    ui.ok(f"API token {token_id} (privilege-separated; secret goes only into the VALOR VM)")

    grant(settings, facts, rec, bridge, token_id)
    return {"token_id": token_id, "secret": secret}


# token_cannot_touch(): ask the LOCAL API, as the token, for a VM's config: True when the answer is 403 Forbidden (the
# check that VALOR can't touch its own VM) >w<
def token_cannot_touch(node: str, vmid: int, token: dict) -> bool:
    """True when the token gets HTTP 403 for the given VM (checked against the local API)."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE           # loopback only
    request = urllib.request.Request(f"https://127.0.0.1:8006/api2/json/nodes/{node}/qemu/{vmid}/config",
                                     headers={"Authorization": f"PVEAPIToken={token['token_id']}={token['secret']}"})
    try:
        with urllib.request.urlopen(request, context=context, timeout=15):
            return False
    except urllib.error.HTTPError as e:
        return e.code == 403


# remove(): uninstall: revoke the ACLs, delete the token, the user (if VALOR made it) and VALOR's roles no other ACL
# uses. Every step tolerates "already gone" OwO
def remove(rec: Record, purge_user: bool = True) -> None:
    """Uninstall: revoke the ACLs, delete the token, the user (if VALOR made it) and VALOR's roles no other ACL
    uses. Every step tolerates "already gone", so a half-finished uninstall can run again."""
    objects = rec.objects
    user = objects.get("token", "!").split("!")[0]
    token = objects.get("token")
    for path, role in objects.get("acls", []):
        for kind, who in (("tokens", token), ("users", user)):
            if who:
                try:
                    pvesh("set", "/access/acl", path=path, roles=role, delete=1, **{kind: who})
                except CommandError:
                    pass
    if token:
        try:
            pvesh("delete", f"/access/users/{user}/token/{token.split('!')[1]}")
        except CommandError:
            pass
    if objects.get("user_created") and purge_user:
        try:
            pvesh("delete", f"/access/users/{user}")
        except CommandError:
            pass
    acl_roles = {acl["roleid"] for acl in (pvesh("get", "/access/acl") or [])}
    for role in objects.get("roles_created", []):
        if role not in acl_roles:
            try:
                pvesh("delete", f"/access/roles/{role}")
            except CommandError:
                pass


# api_endpoint(): how the VALOR VM reaches and verifies the Proxmox API: by IP with the cluster's CA, or by name with
# a public CA ^w^
def api_endpoint(facts: Facts) -> tuple[str, str | None]:
    """How the VALOR VM reaches and verifies the Proxmox API: (host, CA PEM to trust or None for system CAs)."""
    import socket
    ca_file = "/etc/pve/pve-root-ca.pem"
    # First the cluster's own CA by IP (the usual case), then a public CA by the node's name (custom certificate).
    for host, cafile, name in ((facts.node_ip, ca_file, facts.node_ip), (facts.node_ip, None, socket.getfqdn())):
        context = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
        try:
            with socket.create_connection((host, 8006), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=name):
                    pass
            return name, (open(cafile).read() if cafile else None)
        except (ssl.SSLError, OSError):
            continue
    raise SystemExit("cannot verify the Proxmox API certificate on this node (neither the cluster CA nor a public CA "
                     "matches); see docs/INSTALL.md, 'custom API certificates'")


# json_token(): the token file's contents UwU *locks the key box*
def json_token(token: dict) -> str:
    return json.dumps({"token_id": token["token_id"], "secret": token["secret"]})
