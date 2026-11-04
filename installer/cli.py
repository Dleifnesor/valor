"""install.sh entry point: install, status, upgrade, uninstall, extra templates.

The installer runs as root on a Proxmox node. Everything it creates is written to an install record
(record.py) the moment it exists, so a run that stops half way can be resumed, and upgrade / uninstall touch
only what the installer made.
"""

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
  ./install.sh --template rocky-10 --rebuild    rebuild a template (current updates) and remove the old one
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


def show(settings: Answers, facts: Facts, bridge: str, create_bridge: bool) -> None:
    lo, hi = settings.vmid_ranges
    ui.table([
        ("instance", f"{settings.id}  (pools {settings.pool_ranges}, {settings.pool_templates}, {settings.pool_system}; user {settings.user})"),
        ("VALOR VM", f"VMID {settings.vmid_vm}, {settings.vm_cores} vCPU, {settings.vm_memory} MiB, {settings.vm_disk} GiB on {settings.vm_storage}"),
        ("  network", f"{settings.vm_bridge}" + (f" VLAN {settings.vm_vlan}" if settings.vm_vlan else "")
         + f", {'DHCP' if settings.vm_ip == 'dhcp' else settings.vm_ip + ' via ' + settings.vm_gateway}, hostname {settings.vm_hostname}"),
        ("  web UI from", ", ".join(settings.allowed_networks) + "  (HTTPS; nothing else is reachable)"),
        ("  certificate", {"valor-ca": "VALOR's own CA", "own": f"your certificate {settings.tls_cert}"}[settings.tls]),
        ("  first admin", settings.admin_user + " (password saved root-only on this node; MFA set up at first sign-in)"),
        ("ranges", f"disks on {settings.range_storage}; VMIDs {lo}-{hi}; VLANs {settings.vlan_min}-{settings.vlan_max}"),
        ("  segment bridge", f"{bridge}" + (" (new: VLAN-aware, no physical port, with rollback timer)" if create_bridge else " (existing)")),
        ("  uplink", f"{settings.vm_bridge} via the LAN's DHCP, NAT, private networks blocked"),
        ("  reserved", ", ".join(settings.reserved_networks)),
        ("templates", ", ".join(settings.templates) + f"  (snippets on {settings.snippets_storage})"),
        ("ISO library", f"downloads/uploads to {settings.iso_storage or '-'}; reads {', '.join(settings.iso_storages) or '-'}"),
    ])


def install(args, facts: Facts) -> None:
    """A new installation (or the resumption of one that stopped half way: every step checks what exists)."""
    preflight(facts)
    settings, bridge, create_bridge = _agree_on_settings(args, facts)
    record = Record(settings.id, facts.record)
    record.save(settings)

    ui.step("Proxmox identity")
    token = identity.ensure(settings, facts, record, bridge)

    ui.step("Range network")
    _range_bridge(settings, facts, record, bridge, create_bridge)

    ui.step("OS templates")
    built = templates.ensure(settings, facts, record, settings.templates)

    ui.step("VALOR VM")
    api_host, pve_ca = identity.api_endpoint(facts)
    vmid = appliance.create(settings, facts, record, built["ubuntu-24.04"])
    ip = appliance.boot(facts, vmid)
    record.set("vm", {"vmid": vmid, "name": settings.vm_hostname, "ip": ip})
    # Least privilege, checked for real: the engine's token must not be able to read VALOR's own VM.
    if not identity.token_cannot_touch(facts.node, vmid, token):
        raise SystemExit("safety check failed: the engine's token can read the VALOR VM. Not continuing.")
    ui.ok("the engine's token cannot touch the VALOR VM (HTTP 403)")

    password = appliance.new_password()
    files = _appliance_files(settings, facts, bridge, api_host, pve_ca, ip, token, password)
    ui.step("Provisioning")
    appliance.provision(settings, facts, vmid, "install", files)
    record.mark("provisioned")

    ui.step("Checking")
    ca = appliance.ca_pem(facts, vmid) if settings.tls == "valor-ca" else None
    if not appliance.health(ip, ca):
        raise SystemExit(f"the web UI at https://{ip}/ does not answer; see /var/log/valor-setup.log in VM {vmid}")
    ui.ok(f"https://{ip}/ answers with a valid certificate")
    credentials = _write_credentials(settings, ip, password, ca)
    record.set("credentials_file", str(credentials))
    record.mark("installed")
    _say_ready(settings, ip, credentials, ca)


def _agree_on_settings(args, facts: Facts):
    """Detected defaults + answers file + flags, shown as a plan until the person installs (or --dry-run stops
    here). Returns (settings, range bridge, whether to create it)."""
    catalog = templates.catalog()
    given = load_file(args.answers) if args.answers else {}
    if args.id:
        given["id"] = args.id
    settings = defaults(facts, given)
    if facts.record and not args.answers:
        ui.info(f"found an earlier install record for '{settings.id}' ({facts.record.get('version')}); "
                "resuming with its settings")
    while True:
        bridge, create_bridge = resolve_bridge(settings, facts)
        ui.step("Plan")
        show(settings, facts, bridge, create_bridge)
        errors = problems(settings, facts, catalog)
        for error in errors:
            ui.fail(error)
        if args.save_answers:
            Path(args.save_answers).write_text(settings.to_toml())
            ui.ok(f"settings saved to {args.save_answers}")
        if args.dry_run:
            _show_dry_run(settings, facts, bridge, create_bridge)
            raise SystemExit(1 if errors else 0)
        if errors and (ui.ASSUME_YES or not sys.stdin.isatty()):
            raise SystemExit("fix the settings above (answers file or flags) and run again")
        choice = "edit" if errors else ui.choose("Install with these settings?", [
            ("install", "go ahead"), ("edit", "change settings"), ("quit", "do nothing")], "install")
        if choice == "quit":
            raise SystemExit(0)
        if choice == "edit":
            settings = edit(settings, facts, catalog)
            continue
        return settings, bridge, create_bridge


def _show_dry_run(settings, facts: Facts, bridge: str, create_bridge: bool) -> None:
    ui.step("Would do")
    for line in identity.plan(settings, facts, bridge):
        ui.info(line)
    if create_bridge:
        ui.info(f"create bridge {bridge} on {facts.node} (backup + 5-minute rollback timer)")
    for os_name in settings.templates:
        template = templates.existing(facts, settings, os_name)
        ui.info(f"reuse template {template['vmid']} ({os_name})" if template else f"build template {os_name}")
    ui.info(f"create the VALOR VM {settings.vmid_vm} and provision it over the guest agent")


def _range_bridge(settings, facts: Facts, record: Record, bridge: str, create_bridge: bool) -> None:
    if create_bridge:
        backup = network.create_bridge(facts.node, bridge,
                                       f"VALOR {settings.id}: range segments (VLAN-aware, no physical port)")
        record.set("bridge", {"name": bridge, "created": True, "backup": backup})
    else:
        # an existing bridge is recorded as not ours, so uninstall never removes it
        record.objects.setdefault("bridge", {"name": bridge, "created": False})
        record.save()
        ui.ok(f"using bridge {bridge}")


def _appliance_files(settings, facts: Facts, bridge: str, api_host: str, pve_ca: str | None, ip: str, token: dict,
                     password: str) -> dict[str, str | bytes]:
    """What goes into the VALOR VM at the first install: its config, the engine's token, the first admin's
    password, and the TLS certificate when the person brings their own."""
    files: dict[str, str | bytes] = {
        "config.toml": appliance.config_toml(settings, facts, bridge, api_host, pve_ca, ip),
        "appliance.env": appliance.appliance_env(settings, facts, "install", api_host),
        "token.json": identity.json_token(token),
        "admin.pw": password + "\n",
    }
    if pve_ca:
        files["pve-ca.pem"] = pve_ca
    if settings.tls == "own":
        files["tls.crt"] = Path(settings.tls_cert).read_bytes()
        files["tls.key"] = Path(settings.tls_key).read_bytes()
    return files


def _write_credentials(settings, ip: str, password: str, ca: str | None) -> Path:
    """The first sign-in details, readable by root only (created with mode 0600, never world-readable)."""
    path = Path(f"/root/valor-{settings.id}-credentials.txt")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"VALOR {VERSION} ({settings.id}) - first sign-in\nURL: https://{ip}/\nUser: {settings.admin_user}\n"
                 f"Password: {password}\n(You set up two-factor authentication at the first sign-in.)\n")
        if ca:
            fh.write(f"CA certificate SHA-256: {appliance.fingerprint(ca)}\n")
    return path


def _say_ready(settings, ip: str, credentials: Path, ca: str | None) -> None:
    ui.say()
    ui.step(f"VALOR {VERSION} is ready")
    rows = [("open", f"https://{ip}/"), ("sign in as", f"{settings.admin_user} (password in {credentials}, root-only)")]
    if ca:
        rows.append(("CA certificate", f"https://{ip}/ca.crt - SHA-256 {appliance.fingerprint(ca)[:47]}..."))
    if settings.vm_ip == "dhcp":
        rows.append(("tip", f"reserve {ip} for the VALOR VM in your DHCP server so the address never changes"))
    ui.table(rows)


def status(args, facts: Facts) -> None:
    recorded = facts.record
    if not recorded:
        raise SystemExit(f"no VALOR installation '{args.id or 'valor'}' on this cluster")
    objects = recorded.get("objects", {})
    vm = objects.get("vm", {})
    built = ", ".join(f"{template['os']}={template['vmid']}" for template in objects.get("templates", []))
    ui.step(f"VALOR '{recorded['instance']}' {recorded.get('version')} (installed {recorded.get('created', '?')[:10]})")
    ui.table([("VALOR VM", f"{vm.get('vmid')} at {vm.get('ip')}"), ("token", objects.get("token", "-")),
              ("bridge", f"{objects.get('bridge', {}).get('name')}"), ("templates", built or "reused"),
              ("steps", ", ".join(step for step, state in recorded.get("steps", {}).items() if state == "done"))])
    if vm.get("ip"):
        ok = appliance.health(vm["ip"], None) or appliance.health(vm["ip"], _ca_or_none(facts, vm))
        (ui.ok if ok else ui.fail)(f"web UI https://{vm['ip']}/ {'answers' if ok else 'does not answer'}")


def _ca_or_none(facts: Facts, vm: dict) -> str | None:
    try:
        return appliance.ca_pem(facts, vm["vmid"])
    except Exception:
        return None


def upgrade(args, facts: Facts) -> None:
    """Push this version into the existing VALOR VM. Its data stays; running jobs finish first."""
    recorded = facts.record
    if not recorded or recorded.get("steps", {}).get("installed") != "done":
        raise SystemExit("no complete installation to upgrade; run ./install.sh")
    preflight(facts)
    settings = defaults(facts, recorded["answers"])        # settings added by newer versions get their default
    bridge = recorded["objects"]["bridge"]["name"]
    vmid = recorded["objects"]["vm"]["vmid"]
    ui.step(f"Upgrading VALOR '{settings.id}' from {recorded.get('version')} to {VERSION}")
    record = Record(settings.id, recorded)
    record.save(settings)
    identity.ensure_roles(facts, record)                   # new versions may need new privileges
    identity.grant(settings, facts, record, bridge, recorded["objects"]["token"])
    ip = appliance.boot(facts, vmid)
    busy = appliance.busy_jobs(facts, vmid)
    if busy:
        ui.warn(f"VALOR is running or has queued {len(busy)} job(s) ({', '.join(busy)}). The upgrade restarts the "
                "worker, so it waits until they are done.")
        if not ui.confirm("Wait for them, then upgrade?", default=True):
            raise SystemExit(0)
        appliance.wait_jobs(facts, vmid)
    api_host, pve_ca = identity.api_endpoint(facts)
    files: dict[str, str | bytes] = {"config.toml": appliance.config_toml(settings, facts, bridge, api_host, pve_ca, ip),
                                     "appliance.env": appliance.appliance_env(settings, facts, "upgrade", api_host)}
    if pve_ca:
        files["pve-ca.pem"] = pve_ca
    appliance.provision(settings, facts, vmid, "upgrade", files)
    record.set("vm", {**recorded["objects"]["vm"], "ip": ip})
    ca = appliance.ca_pem(facts, vmid) if settings.tls == "valor-ca" else None
    if not appliance.health(ip, ca):
        raise SystemExit(f"after the upgrade https://{ip}/ does not answer; see /var/log/valor-setup.log in VM {vmid}")
    ui.ok(f"VALOR {VERSION} running at https://{ip}/")


def uninstall(args, facts: Facts) -> None:
    """Remove what the installer created. Range VMs stay unless --purge-ranges; templates, the bridge and pools
    that ranges still use stay too."""
    recorded = facts.record
    if not recorded:
        raise SystemExit(f"no VALOR installation '{args.id or 'valor'}' on this cluster")
    record = Record(recorded["instance"], recorded)
    settings = Answers(**recorded.get("answers", {}))
    ranges = appliance.pool_vms(settings.pool_ranges)
    ui.step(f"Removing VALOR '{settings.id}'")
    ui.info(f"VALOR VM {record.objects.get('vm', {}).get('vmid')}, the API user/token/permissions and what the "
            f"installer created. {len(ranges)} range VMs " + ("will be DELETED." if args.purge_ranges else "are kept."))
    if not ui.confirm("Remove VALOR?", default=False):
        raise SystemExit(0)
    if record.objects.get("vm"):
        appliance.destroy(record.objects["vm"]["vmid"])
    if args.purge_ranges:
        _purge_ranges(ranges)
        ranges = []
    if not ranges:
        templates.remove_built(record)
    identity.remove(record)
    for pool in record.objects.get("pools_created", []):
        try:
            pvesh("delete", f"/pools/{pool}")
        except CommandError:
            ui.warn(f"pool {pool} kept (not empty)")
    bridge = record.objects.get("bridge", {})
    if bridge.get("created") and not ranges:
        network.remove_bridge(facts.node, bridge["name"])
    credentials = record.objects.get("credentials_file")
    if credentials:
        Path(credentials).unlink(missing_ok=True)
    record.delete()
    ui.ok("VALOR removed")


def _purge_ranges(ranges: list[dict]) -> None:
    for vm in ranges:
        try:
            run(["qm", "stop", str(vm["vmid"]), "--skiplock", "1"], check=False)
            run(["qm", "destroy", str(vm["vmid"]), "--purge", "1"])
            ui.ok(f"range VM {vm['vmid']} ({vm.get('name')}) removed")
        except CommandError as e:
            ui.warn(f"range VM {vm['vmid']}: {e}")


def add_template(args, facts: Facts) -> None:
    if not facts.record:
        raise SystemExit("install VALOR first")
    settings = Answers(**facts.record["answers"])
    if args.template not in templates.catalog():
        raise SystemExit(f"unknown OS {args.template}; known: {', '.join(templates.catalog())}")
    record = Record(settings.id, facts.record)
    ui.step(f"Template {args.template}")
    templates.ensure(settings, facts, record, [args.template], rebuild=args.rebuild)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="install.sh", description=f"VALOR {VERSION} installer for Proxmox VE",
                                     epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--upgrade", action="store_true")
    mode.add_argument("--uninstall", action="store_true")
    mode.add_argument("--template", metavar="OS")
    parser.add_argument("--id", help="instance id (default: valor); prefixes every Proxmox object VALOR creates")
    parser.add_argument("--answers", metavar="FILE", help="TOML answers file (see docs/INSTALL.md)")
    parser.add_argument("--save-answers", metavar="FILE", help="write the final settings to FILE")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    parser.add_argument("--yes", action="store_true", help="accept defaults and confirmations (unattended)")
    parser.add_argument("--purge-ranges", action="store_true", help="with --uninstall: also delete all range VMs")
    parser.add_argument("--rebuild", action="store_true",
                    help="with --template: build a fresh template (current updates) and remove the old one")
    parser.add_argument("--version", action="version", version=f"VALOR {VERSION}")
    args = parser.parse_args(argv)
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
