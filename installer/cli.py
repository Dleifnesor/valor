"""install.sh entry point: install, status, upgrade, uninstall, extra templates."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import MIN_PVE, VERSION, appliance, identity, network, templates, ui
from .answers import Answers, defaults, edit, load_file, problems, resolve_bridge
from .discover import Facts, collect
from .record import Record
from .sh import CommandError, pvesh, run

EPILOG = """
examples:
  ./install.sh                                  interactive install with detected defaults
  ./install.sh --answers site.toml --yes        unattended install (any missing setting: detected default)
  ./install.sh --dry-run --save-answers a.toml  show what would happen and save the settings, change nothing
  ./install.sh --status                         show the installation and check the web UI
  ./install.sh --upgrade                        push this version into the VALOR VM (data is kept)
  ./install.sh --template debian-13             build another OS template for an existing installation
  ./install.sh --uninstall [--purge-ranges]     remove VALOR (ranges are kept unless --purge-ranges)
"""


def preflight(facts: Facts, need_internet: bool = True) -> None:
    ui.step("Checking this node")
    rows = [("Proxmox VE", facts.pve_version), ("node", f"{facts.node} ({facts.node_ip})"),
            ("cluster", f"{facts.cluster_name} ({len(facts.nodes)} nodes)" if facts.cluster_name else "single node"),
            ("CPU", f"{facts.cpu_threads} threads, AES-NI {'yes' if facts.has_aes else 'no'}"),
            ("memory", f"{facts.mem_free / 2**30:.0f} GiB free of {facts.mem_total / 2**30:.0f} GiB"),
            ("management network", f"{facts.mgmt_network} on {facts.mgmt_bridge}"
             + (f" VLAN {facts.mgmt_vlan}" if facts.mgmt_vlan else "") + f", gateway {facts.mgmt_gateway}"),
            ("internet", facts.internet[1])]
    ui.table(rows)
    if facts.version_tuple < MIN_PVE:
        raise SystemExit(f"VALOR needs Proxmox VE {MIN_PVE[0]}.{MIN_PVE[1]} or newer")
    if need_internet and not facts.internet[0]:
        raise SystemExit("the node needs internet access during the install (cloud images, Ubuntu and Python packages)")
    if facts.mem_free < 3 * 2**30:
        raise SystemExit("less than 3 GiB of free memory on this node")
    if facts.pending_network_changes:
        ui.warn("there are pending network changes on this node (/etc/network/interfaces.new); the installer will "
                "not touch the network until they are applied or reverted")


def show(a: Answers, facts: Facts, bridge: str, create_bridge: bool) -> None:
    lo, hi = a.vmid_ranges
    ui.table([
        ("instance", f"{a.id}  (pools {a.pool_ranges}, {a.pool_templates}, {a.pool_system}; user {a.user})"),
        ("VALOR VM", f"VMID {a.vmid_vm}, {a.vm_cores} vCPU, {a.vm_memory} MiB, {a.vm_disk} GiB on {a.vm_storage}"),
        ("  network", f"{a.vm_bridge}" + (f" VLAN {a.vm_vlan}" if a.vm_vlan else "")
         + f", {'DHCP' if a.vm_ip == 'dhcp' else a.vm_ip + ' via ' + a.vm_gateway}, hostname {a.vm_hostname}"),
        ("  web UI from", ", ".join(a.allowed_networks) + "  (HTTPS; nothing else is reachable)"),
        ("  certificate", {"valor-ca": "VALOR's own CA", "own": f"your certificate {a.tls_cert}"}[a.tls]),
        ("  first admin", a.admin_user + " (password saved root-only on this node; MFA set up at first sign-in)"),
        ("ranges", f"disks on {a.range_storage}; VMIDs {lo}-{hi}; VLANs {a.vlan_min}-{a.vlan_max}"),
        ("  segment bridge", f"{bridge}" + (" (new: VLAN-aware, no physical port, with rollback timer)" if create_bridge else " (existing)")),
        ("  uplink", f"{a.vm_bridge} via the LAN's DHCP, NAT, private networks blocked"),
        ("  reserved", ", ".join(a.reserved_networks)),
        ("templates", ", ".join(a.templates) + f"  (snippets on {a.snippets_storage})"),
    ])


def install(args, facts: Facts) -> None:
    preflight(facts)
    cat = templates.catalog()
    given = load_file(args.answers) if args.answers else {}
    if args.id:
        given["id"] = args.id
    a = defaults(facts, given)
    if facts.record and not args.answers:
        ui.info(f"found an earlier install record for '{a.id}' ({facts.record.get('version')}); resuming with its settings")
    while True:
        bridge, create_bridge = resolve_bridge(a, facts)
        ui.step("Plan")
        show(a, facts, bridge, create_bridge)
        errs = problems(a, facts, cat)
        for e in errs:
            ui.fail(e)
        if args.save_answers:
            Path(args.save_answers).write_text(a.to_toml())
            ui.ok(f"settings saved to {args.save_answers}")
        if args.dry_run:
            ui.step("Would do")
            for line in identity.plan(a, facts, bridge):
                ui.info(line)
            if create_bridge:
                ui.info(f"create bridge {bridge} on {facts.node} (backup + 5-minute rollback timer)")
            for os_name in a.templates:
                t = templates.existing(facts, a, os_name)
                ui.info(f"reuse template {t['vmid']} ({os_name})" if t else f"build template {os_name}")
            ui.info(f"create the VALOR VM {a.vmid_vm} and provision it over the guest agent")
            raise SystemExit(1 if errs else 0)
        if errs and (ui.ASSUME_YES or not sys.stdin.isatty()):
            raise SystemExit("fix the settings above (answers file or flags) and run again")
        choice = "edit" if errs else ui.choose("Install with these settings?", [
            ("install", "go ahead"), ("edit", "change settings"), ("quit", "do nothing")], "install")
        if choice == "quit":
            raise SystemExit(0)
        if choice == "edit":
            a = edit(a, facts, cat)
            continue
        break

    rec = Record(a.id, facts.record)
    rec.save(a)
    ui.step("Proxmox identity")
    token = identity.ensure(a, facts, rec, bridge)

    ui.step("Range network")
    if create_bridge:
        backup = network.create_bridge(facts.node, bridge, f"VALOR {a.id}: range segments (VLAN-aware, no physical port)")
        rec.set("bridge", {"name": bridge, "created": True, "backup": backup})
    else:
        rec.objects.setdefault("bridge", {"name": bridge, "created": False})
        rec.save()
        ui.ok(f"using bridge {bridge}")

    ui.step("OS templates")
    tpl = templates.ensure(a, facts, rec, a.templates)

    ui.step("VALOR VM")
    api_host, pve_ca = identity.api_endpoint(facts)
    vmid = appliance.create(a, facts, rec, tpl["ubuntu-24.04"])
    ip = appliance.boot(facts, vmid)
    rec.set("vm", {"vmid": vmid, "name": a.vm_hostname, "ip": ip})
    if not identity.token_cannot_touch(facts.node, vmid, token):
        raise SystemExit("safety check failed: the engine's token can read the VALOR VM. Not continuing.")
    ui.ok("the engine's token cannot touch the VALOR VM (HTTP 403)")

    password = appliance.new_password()
    files: dict[str, str | bytes] = {
        "config.toml": appliance.config_toml(a, facts, bridge, api_host, pve_ca, ip),
        "appliance.env": appliance.appliance_env(a, facts, "install", api_host),
        "token.json": identity.json_token(token),
        "admin.pw": password + "\n",
    }
    if pve_ca:
        files["pve-ca.pem"] = pve_ca
    if a.tls == "own":
        files["tls.crt"] = Path(a.tls_cert).read_bytes()
        files["tls.key"] = Path(a.tls_key).read_bytes()
    ui.step("Provisioning")
    appliance.provision(a, facts, vmid, "install", files)
    rec.mark("provisioned")

    ui.step("Checking")
    ca = appliance.ca_pem(facts, vmid) if a.tls == "valor-ca" else None
    if not appliance.health(ip, ca):
        raise SystemExit(f"the web UI at https://{ip}/ does not answer; see /var/log/valor-setup.log in VM {vmid}")
    ui.ok(f"https://{ip}/ answers with a valid certificate")
    creds = Path(f"/root/valor-{a.id}-credentials.txt")
    fd = os.open(creds, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"VALOR {VERSION} ({a.id}) - first sign-in\nURL: https://{ip}/\nUser: {a.admin_user}\n"
                 f"Password: {password}\n(You set up two-factor authentication at the first sign-in.)\n")
        if ca:
            fh.write(f"CA certificate SHA-256: {appliance.fingerprint(ca)}\n")
    rec.set("credentials_file", str(creds))
    rec.mark("installed")
    ui.say()
    ui.step(f"VALOR {VERSION} is ready")
    rows = [("open", f"https://{ip}/"), ("sign in as", f"{a.admin_user} (password in {creds}, root-only)")]
    if ca:
        rows.append(("CA certificate", f"https://{ip}/ca.crt - SHA-256 {appliance.fingerprint(ca)[:47]}..."))
    if a.vm_ip == "dhcp":
        rows.append(("tip", f"reserve {ip} for the VALOR VM in your DHCP server so the address never changes"))
    ui.table(rows)


def status(args, facts: Facts) -> None:
    rec = facts.record
    if not rec:
        raise SystemExit(f"no VALOR installation '{args.id or 'valor'}' on this cluster")
    o = rec.get("objects", {})
    vm = o.get("vm", {})
    ui.step(f"VALOR '{rec['instance']}' {rec.get('version')} (installed {rec.get('created', '?')[:10]})")
    ui.table([("VALOR VM", f"{vm.get('vmid')} at {vm.get('ip')}"), ("token", o.get("token", "-")),
              ("bridge", f"{o.get('bridge', {}).get('name')}"), ("templates", ", ".join(f"{t['os']}={t['vmid']}" for t in o.get("templates", [])) or "reused"),
              ("steps", ", ".join(k for k, v in rec.get("steps", {}).items() if v == "done"))])
    if vm.get("ip"):
        ok = appliance.health(vm["ip"], None) or appliance.health(vm["ip"], _ca_or_none(facts, vm))
        (ui.ok if ok else ui.fail)(f"web UI https://{vm['ip']}/ {'answers' if ok else 'does not answer'}")


def _ca_or_none(facts: Facts, vm: dict) -> str | None:
    try:
        return appliance.ca_pem(facts, vm["vmid"])
    except Exception:
        return None


def upgrade(args, facts: Facts) -> None:
    rec = facts.record
    if not rec or rec.get("steps", {}).get("installed") != "done":
        raise SystemExit("no complete installation to upgrade; run ./install.sh")
    preflight(facts)
    a = Answers(**rec["answers"])
    bridge = rec["objects"]["bridge"]["name"]
    vmid = rec["objects"]["vm"]["vmid"]
    ui.step(f"Upgrading VALOR '{a.id}' from {rec.get('version')} to {VERSION}")
    ip = appliance.boot(facts, vmid)
    api_host, pve_ca = identity.api_endpoint(facts)
    files: dict[str, str | bytes] = {"config.toml": appliance.config_toml(a, facts, bridge, api_host, pve_ca, ip),
                                     "appliance.env": appliance.appliance_env(a, facts, "upgrade", api_host)}
    if pve_ca:
        files["pve-ca.pem"] = pve_ca
    appliance.provision(a, facts, vmid, "upgrade", files)
    r = Record(a.id, rec)
    r.set("vm", {**rec["objects"]["vm"], "ip": ip})
    ca = appliance.ca_pem(facts, vmid) if a.tls == "valor-ca" else None
    if not appliance.health(ip, ca):
        raise SystemExit(f"after the upgrade https://{ip}/ does not answer; see /var/log/valor-setup.log in VM {vmid}")
    ui.ok(f"VALOR {VERSION} running at https://{ip}/")


def uninstall(args, facts: Facts) -> None:
    rec_data = facts.record
    if not rec_data:
        raise SystemExit(f"no VALOR installation '{args.id or 'valor'}' on this cluster")
    rec = Record(rec_data["instance"], rec_data)
    a = Answers(**rec_data.get("answers", {}))
    ranges = appliance.pool_vms(a.pool_ranges)
    ui.step(f"Removing VALOR '{a.id}'")
    ui.info(f"VALOR VM {rec.objects.get('vm', {}).get('vmid')}, the API user/token/permissions and what the installer "
            f"created. {len(ranges)} range VMs " + ("will be DELETED." if args.purge_ranges else "are kept."))
    if not ui.confirm("Remove VALOR?", default=False):
        raise SystemExit(0)
    if rec.objects.get("vm"):
        appliance.destroy(rec.objects["vm"]["vmid"])
    if args.purge_ranges:
        for m in ranges:
            try:
                run(["qm", "stop", str(m["vmid"]), "--skiplock", "1"], check=False)
                run(["qm", "destroy", str(m["vmid"]), "--purge", "1"])
                ui.ok(f"range VM {m['vmid']} ({m.get('name')}) removed")
            except CommandError as e:
                ui.warn(f"range VM {m['vmid']}: {e}")
        ranges = []
    if not ranges:
        templates.remove_built(rec)
    identity.remove(rec)
    for pool in rec.objects.get("pools_created", []):
        try:
            pvesh("delete", f"/pools/{pool}")
        except CommandError:
            ui.warn(f"pool {pool} kept (not empty)")
    b = rec.objects.get("bridge", {})
    if b.get("created") and not ranges:
        network.remove_bridge(facts.node, b["name"])
    creds = rec.objects.get("credentials_file")
    if creds:
        Path(creds).unlink(missing_ok=True)
    rec.delete()
    ui.ok("VALOR removed")


def add_template(args, facts: Facts) -> None:
    if not facts.record:
        raise SystemExit("install VALOR first")
    a = Answers(**facts.record["answers"])
    if args.template not in templates.catalog():
        raise SystemExit(f"unknown OS {args.template}; known: {', '.join(templates.catalog())}")
    rec = Record(a.id, facts.record)
    ui.step(f"Template {args.template}")
    templates.ensure(a, facts, rec, [args.template])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="install.sh", description=f"VALOR {VERSION} installer for Proxmox VE",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--upgrade", action="store_true")
    mode.add_argument("--uninstall", action="store_true")
    mode.add_argument("--template", metavar="OS")
    ap.add_argument("--id", help="instance id (default: valor); prefixes every Proxmox object VALOR creates")
    ap.add_argument("--answers", metavar="FILE", help="TOML answers file (see docs/INSTALL.md)")
    ap.add_argument("--save-answers", metavar="FILE", help="write the final settings to FILE")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    ap.add_argument("--yes", action="store_true", help="accept defaults and confirmations (unattended)")
    ap.add_argument("--purge-ranges", action="store_true", help="with --uninstall: also delete all range VMs")
    ap.add_argument("--version", action="version", version=f"VALOR {VERSION}")
    args = ap.parse_args(argv)
    if os.geteuid() != 0:
        raise SystemExit("run the installer as root on a Proxmox VE node")
    ui.ASSUME_YES = args.yes
    instance = args.id or (load_file(args.answers).get("id") if args.answers else None) or "valor"
    ui.step(f"VALOR {VERSION} installer")
    facts = collect(instance, check_net=not (args.status or args.uninstall))
    try:
        if args.status:
            status(args, facts)
        elif args.upgrade:
            upgrade(args, facts)
        elif args.uninstall:
            uninstall(args, facts)
        elif args.template:
            add_template(args, facts)
        else:
            install(args, facts)
    except CommandError as e:
        ui.fail(str(e))
        raise SystemExit(2)
    except KeyboardInterrupt:
        ui.fail("interrupted; run the installer again to resume (it continues where it stopped)")
        raise SystemExit(130)
