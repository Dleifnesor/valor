"""The least-privilege Proxmox identity: roles, a token-only user, a privilege-separated token, pools and ACLs.

The token may act on VMs in VALOR's range pool, clone from the template pool, allocate disks on the range storage,
attach NICs to the two bridges and read node status - nothing else. The VALOR VM sits in a third pool the token
has no rights on, so the AI-driven engine can never touch VALOR itself.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

from . import ui
from .answers import Answers
from .discover import Facts
from .record import Record
from .sh import CommandError, pvesh

ENGINE_PRIVS = ["Pool.Audit", "VM.Allocate", "VM.Audit", "VM.Clone", "VM.Config.CDROM", "VM.Config.CPU",
                "VM.Config.Cloudinit", "VM.Config.Disk", "VM.Config.HWType", "VM.Config.Memory", "VM.Config.Network",
                "VM.Config.Options", "VM.PowerMgmt"]
AGENT_PRIVS_9 = ["VM.GuestAgent.Audit", "VM.GuestAgent.FileRead", "VM.GuestAgent.FileWrite", "VM.GuestAgent.Unrestricted"]
AGENT_PRIVS_8 = ["VM.Monitor"]          # PVE 8: guest agent access is part of VM.Monitor
TOKEN = "appliance"


def roles(facts: Facts) -> dict[str, list[str]]:
    agent = AGENT_PRIVS_9 if "VM.GuestAgent.Unrestricted" in facts.privileges else AGENT_PRIVS_8
    want = {
        "ValorEngine": ENGINE_PRIVS + agent,
        "ValorTemplateUser": ["VM.Audit", "VM.Clone"],
        "ValorStorage": ["Datastore.AllocateSpace", "Datastore.Audit"],
        "ValorNodeAudit": ["Sys.Audit"],
    }
    if facts.privileges:
        for name, privs in want.items():
            missing = [p for p in privs if p not in facts.privileges]
            if missing:
                ui.warn(f"role {name}: this Proxmox VE does not know {missing}; leaving them out")
            want[name] = [p for p in privs if p in facts.privileges]
    return want


def acls(a: Answers, facts: Facts, bridge: str) -> list[tuple[str, str]]:
    paths = [(f"/pool/{a.pool_ranges}", "ValorEngine"),
             (f"/pool/{a.pool_templates}", "ValorTemplateUser"),
             (f"/storage/{a.range_storage}", "ValorStorage"),
             (f"/sdn/zones/localnetwork/{bridge}", "PVESDNUser"),
             (f"/nodes/{facts.node}", "ValorNodeAudit")]
    uplink = f"/sdn/zones/localnetwork/{a.vm_bridge}"
    if uplink not in [p for p, _ in paths]:
        paths.append((uplink, "PVESDNUser"))
    return paths


def plan(a: Answers, facts: Facts, bridge: str) -> list[str]:
    out = []
    for name, privs in roles(facts).items():
        have = facts.roles.get(name)
        if have is None:
            out.append(f"create role {name} ({', '.join(privs)})")
        elif have != set(privs):
            out.append(f"update role {name} to ({', '.join(privs)})")
    if a.user not in facts.users:
        out.append(f"create API user {a.user} (no password; token only)")
    else:
        out.append(f"reuse API user {a.user}")
    for pool in (a.pool_ranges, a.pool_templates, a.pool_system):
        if pool not in facts.pools:
            out.append(f"create pool {pool}")
    out.append(f"create privilege-separated token {a.user}!{TOKEN}")
    for path, role in acls(a, facts, bridge):
        out.append(f"grant {role} on {path}")
    return out


def ensure(a: Answers, facts: Facts, rec: Record, bridge: str) -> dict:
    """Create/refresh everything; returns {"token_id", "secret"} (the secret goes only into the VALOR VM)."""
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

    if a.user not in facts.users:
        pvesh("create", "/access/users", userid=a.user, comment=f"VALOR {a.id}: engine API user (token only, no password)")
        rec.set("user_created", True)
        ui.ok(f"user {a.user}")
    elif not facts.users[a.user].startswith("VALOR"):
        raise SystemExit(f"{a.user} exists but was not made by VALOR (comment: {facts.users[a.user]!r}); "
                         "choose another id with --id")
    else:
        rec.objects.setdefault("user_created", False)

    pools_created = rec.objects.get("pools_created", [])
    for pool, what in ((a.pool_ranges, "ranges"), (a.pool_templates, "OS templates"), (a.pool_system, "the VALOR VM")):
        if pool not in facts.pools:
            pvesh("create", "/pools", poolid=pool, comment=f"VALOR {a.id}: {what}")
            pools_created.append(pool)
            ui.ok(f"pool {pool}")
    rec.set("pools_created", sorted(set(pools_created)))

    tokens = {t["tokenid"] for t in (pvesh("get", f"/access/users/{a.user}/token") or [])}
    if TOKEN in tokens:
        pvesh("delete", f"/access/users/{a.user}/token/{TOKEN}")
    res = pvesh("create", f"/access/users/{a.user}/token/{TOKEN}", _quiet_secret=True, privsep=1,
                comment=f"VALOR {a.id} engine in the VALOR VM")
    token_id, secret = res["full-tokenid"], res["value"]
    rec.set("token", token_id)
    ui.ok(f"API token {token_id} (privilege-separated; secret goes only into the VALOR VM)")

    granted = []
    for path, role in acls(a, facts, bridge):
        pvesh("set", "/access/acl", path=path, roles=role, users=a.user, propagate=1)
        pvesh("set", "/access/acl", path=path, roles=role, tokens=token_id, propagate=1)
        granted.append([path, role])
    rec.set("acls", granted)
    ui.ok(f"{len(granted)} permissions granted to the user and the token")
    return {"token_id": token_id, "secret": secret}


def token_cannot_touch(node: str, vmid: int, token: dict) -> bool:
    """True when the token gets HTTP 403 for the given VM (checked against the local API)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE           # loopback only
    req = urllib.request.Request(f"https://127.0.0.1:8006/api2/json/nodes/{node}/qemu/{vmid}/config",
                                 headers={"Authorization": f"PVEAPIToken={token['token_id']}={token['secret']}"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15):
            return False
    except urllib.error.HTTPError as e:
        return e.code == 403


def remove(rec: Record, purge_user: bool = True) -> None:
    o = rec.objects
    user = o.get("token", "!").split("!")[0]
    token = o.get("token")
    for path, role in o.get("acls", []):
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
    if o.get("user_created") and purge_user:
        try:
            pvesh("delete", f"/access/users/{user}")
        except CommandError:
            pass
    acl_roles = {a["roleid"] for a in (pvesh("get", "/access/acl") or [])}
    for role in o.get("roles_created", []):
        if role not in acl_roles:
            try:
                pvesh("delete", f"/access/roles/{role}")
            except CommandError:
                pass


def api_endpoint(facts: Facts) -> tuple[str, str | None]:
    """How the VALOR VM reaches and verifies the Proxmox API: (host, CA PEM to trust or None for system CAs)."""
    import socket
    ca_file = "/etc/pve/pve-root-ca.pem"
    for host, cafile, name in ((facts.node_ip, ca_file, facts.node_ip), (facts.node_ip, None, socket.getfqdn())):
        ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
        try:
            with socket.create_connection((host, 8006), timeout=10) as sock:
                with ctx.wrap_socket(sock, server_hostname=name):
                    pass
            return name, (open(cafile).read() if cafile else None)
        except (ssl.SSLError, OSError):
            continue
    raise SystemExit("cannot verify the Proxmox API certificate on this node (neither the cluster CA nor a public CA "
                     "matches); see docs/INSTALL.md, 'custom API certificates'")


def json_token(token: dict) -> str:
    return json.dumps({"token_id": token["token_id"], "secret": token["secret"]})
