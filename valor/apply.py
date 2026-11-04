"""Idempotent build (FR-04 .. FR-07, FR-09, FR-10, FR-14).

`apply(pve, spec)` brings the cluster in line with the spec and can be run any number of times: VMs that already
match are kept, and only what changed is rebuilt, reconfigured or re-converged (see plan.py for how that is
decided). A build goes through these phases:

  1. validate the spec against the cluster and compute the plan (stop here if nothing changes)
  2. delete VMs the plan removes or replaces
  3. create (clone or install) new VMs, resize / restart changed ones
  4. wait until every guest is up: guest agent, Windows setup, cloud-init
  5. map the router's NICs to interface names inside it
  6. open temporary internet egress on the router, then converge the hosts in waves (a host after the hosts its
     roles wait for): login, roles, baseline - and close the egress again, also when a host fails
  7. converge the router itself: login, baseline, final firewall policy, WireGuard
  8. stamp every VM with the spec version it now matches

Each step is recorded (Steps), which gives the progress events in the UI, the build journal, and - when something
fails - an error that says which step on which host failed and what had completed before it.
"""

from __future__ import annotations

import json
import re
import secrets
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path

from . import credentials, isos, kickstart, windows, wireguard
from .baseline import baseline_for, bundle, parse_results
from .cluster import load_catalog, range_tag, range_vms, render_description, template_ids
from .errors import ValorError
from .netpolicy import render, router_script
from .plan import Desired, desired_state, make_plan
from .pve import PVE
from .roles import build_script, load_role, role_env
from .spec import ROUTER, RangeSpec, spec_hash
from .validate import validate_cluster

TEMPLATE_DISK_GIB = 8
WINDOWS_TEMPLATE_DISK_GIB = 64
PARALLEL_HOSTS = 4
AGENT_LOST_RETRIES = 1              # a role or baseline script the guest agent lost runs once more

# Plan actions that need the in-guest setup (login, roles, baseline) to run again.
CONVERGE_ACTIONS = ("create", "replace", "update", "converge")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class Steps:
    """Records every step (for structured errors and the build journal) and emits progress events."""

    def __init__(self, emit):
        self.emit = emit
        self.records: list[dict] = []
        self.lock = threading.Lock()            # hosts converge in parallel threads

    @contextmanager
    def step(self, name: str, host: str | None = None):
        record = {"step": name, "host": host, "status": "running", "started": now()}
        with self.lock:
            self.records.append(record)
        self.emit("step_start", step=name, host=host)
        started = time.time()
        try:
            yield record
        except Exception as e:
            record.update(status="failed", seconds=round(time.time() - started, 1),
                          error=getattr(e, "message", str(e)))
            self.emit("step_failed", step=name, host=host, error=record["error"])
            raise
        record.update(status="done", seconds=round(time.time() - started, 1))
        self.emit("step_done", step=name, host=host, seconds=record["seconds"])

    def completed(self) -> list[str]:
        return [f"{record['step']}{' [' + record['host'] + ']' if record['host'] else ''}"
                for record in self.records if record["status"] == "done"]


# ---- VM metadata ------------------------------------------------------------------------------------------

def vm_tags(spec: RangeSpec, vm: Desired) -> str:
    """Proxmox tags: how VALOR (and people) find a range's VMs - see cluster.py."""
    tags = ["valor", range_tag(spec.name), f"valor-spec-{spec_hash(spec)[:12]}", f"valor-os-{vm.os.replace('.', '-')}"]
    if vm.is_router:
        tags.append("valor-router")
    return ";".join(tags)


def vm_meta(spec: RangeSpec, vm: Desired, conv: str | None) -> dict:
    """The metadata block in a VM's notes. conv is None until the VM's first converge finished."""
    return {"range": spec.name, "host": vm.host, "role": "router" if vm.is_router else "host",
            "spec": spec_hash(spec), "hw": vm.hw_hash, "hw_spec": vm.hw, "conv": conv, "applied": now()}


def stamp(pve: PVE, spec: RangeSpec, vm: Desired, vmid: int, conv: str | None) -> None:
    pve.update_config(vmid, description=render_description(vm_meta(spec, vm, conv), spec_hash(spec)[:12]),
                      tags=vm_tags(spec, vm))


def _net(model: str, nic: dict) -> str:
    """A Proxmox netN value: model[=MAC],bridge=...[,tag=VLAN]."""
    return f"{model},bridge={nic['bridge']}" + (f",tag={nic['vlan']}" if nic["vlan"] else "")


def _mac(net: str) -> str | None:
    """The MAC address in a Proxmox netN value ('virtio=BC:24:11:..,bridge=..')."""
    first = net.split(",", 1)[0]
    return first.split("=", 1)[1] if "=" in first else None


def _disk_gib(disk: str) -> float:
    """The size in a Proxmox disk value ('store:vm-1-disk-1,discard=on,size=64G,ssd=1'); 0 when it has none."""
    match = re.search(r"(?:^|,)size=(\d+(?:\.\d+)?)([KMGT]?)(?:,|$)", disk or "")
    if not match:
        return 0
    return float(match.group(1)) * {"": 2**-30, "K": 2**-20, "M": 2**-10, "G": 1, "T": 2**10}[match.group(2)]


# ---- Creating VMs -----------------------------------------------------------------------------------------

def create_vm(pve: PVE, spec: RangeSpec, vm: Desired, taken: set[int], uplink_mac: str | None = None) -> int:
    """Create one VM (clone of its OS template, or an empty VM that installs from ISO) and start it.
    taken: VMIDs already handed out in this build. uplink_mac: keep the router's old LAN MAC (see _Build)."""
    vmid = pve.allocate_vmid(taken)
    taken.add(vmid)
    if vm.hw.get("install") == "iso":
        return create_vm_from_iso(pve, spec, vm, vmid)
    pve.clone(vm.template, vmid, f"{spec.name}-{vm.host}",
              render_description(vm_meta(spec, vm, None), spec_hash(spec)[:12]))
    if vm.family == "windows":
        _configure_windows_clone(pve, spec, vm, vmid)
    else:
        _configure_linux_clone(pve, spec, vm, vmid, uplink_mac)
    if vm.hw.get("iso"):
        attach_iso(pve, vmid, vm.hw["iso"])
    pve.power(vmid, "start")
    return vmid


def _configure_linux_clone(pve: PVE, spec: RangeSpec, vm: Desired, vmid: int, uplink_mac: str | None) -> None:
    """Size, NICs and cloud-init settings (user, SSH key, addresses, DNS) of a Linux clone."""
    cfg = pve.cfg
    params: dict = {
        "cores": vm.cores, "memory": vm.memory, "onboot": 0, "tags": vm_tags(spec, vm),
        "nameserver": " ".join(cfg.nameservers), "ciuser": cfg.guest_user, "ciupgrade": 0,
        "vga": vm.hw.get("display", "std"), "serial0": "socket",
        **({"cpu": "host"} if vm.hw.get("nested") else {}),
    }
    key_file = Path(cfg.ssh_public_key)
    if key_file.exists():
        params["sshkeys"] = key_file.read_text().strip() + "\n"
    for i, nic in enumerate(vm.nics):
        model = f"virtio={uplink_mac}" if i == 0 and uplink_mac else "virtio"
        params[f"net{i}"] = _net(model, nic)
        if nic["ip"] == "dhcp":
            params[f"ipconfig{i}"] = "ip=dhcp"
        else:
            params[f"ipconfig{i}"] = f"ip={nic['ip']}" + (f",gw={nic['gw']}" if nic["gw"] else "")
    pve.update_config(vmid, **params)
    if vm.disk > TEMPLATE_DISK_GIB:
        pve.resize(vmid, "scsi0", vm.disk)


def _configure_windows_clone(pve: PVE, spec: RangeSpec, vm: Desired, vmid: int) -> None:
    """Size and NIC of a Windows clone. No cloud-init: address, name and password come later (windows_prep)."""
    nic = vm.nics[0]
    # localtime=0: the hardware clock runs in UTC like Linux VMs (Proxmox defaults Windows to the host's local
    # time, which with Windows' UTC zone puts the clock hours off)
    pve.update_config(vmid, cores=vm.cores, memory=vm.memory, onboot=0, tags=vm_tags(spec, vm), vga="std",
                      localtime=0, net0=_net(nic.get("model", "e1000e"), nic))
    if vm.hw.get("nested"):
        pve.update_config(vmid, cpu="host")
    if vm.disk > WINDOWS_TEMPLATE_DISK_GIB:
        pve.resize(vmid, "sata0", vm.disk)


def ks_iso_name(spec: RangeSpec, vm: Desired) -> str:
    return f"valor-ks-{spec.name}-{vm.host}.iso"


def create_vm_from_iso(pve: PVE, spec: RangeSpec, vm: Desired, vmid: int) -> int:
    """Empty VM + installer ISO + OEMDRV kickstart ISO (see kickstart.py); the install runs on first boot."""
    cfg = pve.cfg
    entry = load_catalog(cfg)[vm.os]
    installer = isos.installer_iso(pve, entry["install_iso"])
    if installer is None:
        raise ValorError("iso_missing", f"the installer ISO for {vm.os} is not in the ISO library", host=vm.host)

    # The kickstart answers the installer: disk layout, static address, the guest user with VALOR's SSH key.
    segment = spec.segment(vm.segment)
    key_file = Path(cfg.ssh_public_key)
    ssh_key = key_file.read_text().strip() if key_file.exists() else ""
    mac = "BC:24:11:" + ":".join(f"{b:02X}" for b in secrets.token_bytes(3))     # Proxmox's prefix
    kickstart_text = kickstart.kickstart(vm.host, vm.address, segment.cidr.prefixlen, str(segment.gateway),
                                         list(cfg.nameservers), cfg.guest_user, ssh_key, device=mac,
                                         repos=isos.load_catalog(cfg).get(entry["install_iso"], {}).get("kickstart_repos"))
    name = ks_iso_name(spec, vm)
    try:                                    # a CD left by an interrupted install of this host
        pve.delete_volume(cfg.iso_storage, f"{cfg.iso_storage}:iso/{name}")
    except ValorError:
        pass
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        kickstart.build_iso(kickstart_text, path)
        pve.wait_task(pve.upload_iso(cfg.iso_storage, str(path), name), f"upload {name}")

    nic = vm.nics[0]
    pve.create_vm(vmid, name=f"{spec.name}-{vm.host}", ostype="l26", cores=vm.cores, memory=vm.memory,
                  cpu="host" if vm.hw.get("nested") else pve.cpu_type(entry.get("cpu_min")),
                  scsihw="virtio-scsi-single",
                  scsi0=f"{cfg.storage}:{vm.disk},discard=on,ssd=1,iothread=1",
                  ide2=f"{installer['volid']},media=cdrom",
                  ide3=f"{cfg.iso_storage}:iso/{name},media=cdrom",
                  net0=_net(f"virtio={mac}", nic),
                  boot="order=scsi0;ide2", agent="enabled=1", serial0="socket", vga=vm.hw.get("display", "std"),
                  onboot=0)
    # tags and notes only once the VM is in the pool: on create Proxmox checks tag rights on /vms/<id> alone
    pve.update_config(vmid, tags=vm_tags(spec, vm),
                      description=render_description(vm_meta(spec, vm, None), spec_hash(spec)[:12]))
    return vmid                         # started once the router's build egress is open (the install downloads)


def wait_iso_install(pve: PVE, spec: RangeSpec, vm: Desired, vmid: int, timeout: int = 3600) -> None:
    """Until the installed system (not the installer) answers, then remove the install media."""
    end = time.time() + timeout
    pve.power(vmid, "start")
    while time.time() < end:
        if pve.vm_status(vmid).get("status") != "running":      # e.g. the installer stopped on an error
            raise ValorError("iso_install_failed", f"the {vm.os} installer on {vm.host} stopped before finishing",
                             host=vm.host, hint="Open the VM's console to see the installer's message.")
        if pve.agent_ping(vmid):
            try:
                # The kickstart's last step writes MARKER; the installer's own environment never has it.
                if pve.exec(vmid, ["test", "-f", kickstart.MARKER], timeout=30).ok:
                    break
            except ValorError:
                pass
        time.sleep(15)
    else:
        raise ValorError("iso_install_timeout", f"installing {vm.os} on {vm.host} did not finish within "
                         f"{timeout // 60} minutes", host=vm.host, hint="Open the VM's console to see where it stopped.")
    pve.update_config(vmid, ide2="none,media=cdrom", ide3="none,media=cdrom")
    try:
        pve.delete_volume(pve.cfg.iso_storage, f"{pve.cfg.iso_storage}:iso/{ks_iso_name(spec, vm)}")
    except ValorError:
        pass                                    # a leftover kickstart ISO holds no secrets


ISO_SLOT = "ide3"                   # ide2 is the cloud-init drive on Linux clones


def attach_iso(pve: PVE, vmid: int, name: str | None) -> None:
    """Put a library ISO in the VM's CD-ROM drive, or eject it (name None)."""
    if not name:
        if ISO_SLOT in pve.vm_config(vmid):                   # eject (removing the drive would need a reboot)
            pve.update_config(vmid, **{ISO_SLOT: "none,media=cdrom"})
        return
    volid = isos.find(pve, name)
    if volid is None:
        raise ValorError("iso_missing", f"ISO {name} is not in the ISO library",
                         hint="Download or upload it in the ISO library first.")
    pve.update_config(vmid, **{ISO_SLOT: f"{volid},media=cdrom"})


# ---- The router -------------------------------------------------------------------------------------------

def router_interfaces(pve: PVE, vmid: int, n_segments: int, spec: RangeSpec) -> tuple[dict[str, str], str]:
    """Map router NICs to interface names inside the guest by MAC address.

    Returns ({segment name: interface}, uplink interface). Names inside the guest (eth1, ens19, ...) depend on the
    OS, so VALOR matches the MAC of each Proxmox NIC (net0 = uplink, net1.. = segments in spec order)."""
    config = pve.vm_config(vmid)
    macs = {}
    for i in range(n_segments + 1):
        net = config.get(f"net{i}", "")
        mac = next((part.split("=")[1].lower() for part in net.split(",") if part.lower().startswith("virtio=")), None)
        if not mac:
            raise ValorError("router_nic_missing", f"router NIC net{i} has no MAC", host=ROUTER)
        macs[i] = mac
    links = pve.exec(vmid, ["ip", "-j", "link"], timeout=30)
    if not links.ok:
        raise ValorError("router_inspect_failed", "could not list router interfaces", host=ROUTER,
                         details=links.tail())
    name_by_mac = {link.get("address", "").lower(): link["ifname"] for link in json.loads(links.out)}
    names = {}
    for i, mac in macs.items():
        if mac not in name_by_mac:
            raise ValorError("router_nic_missing", f"interface with MAC {mac} (net{i}) not found in the router",
                             host=ROUTER)
        names[i] = name_by_mac[mac]
    ifmap = {segment.name: names[i + 1] for i, segment in enumerate(spec.segments)}
    return ifmap, names[0]


def load_router_policy(pve: PVE, spec: RangeSpec, vmid: int, ifmap, uplink, build_egress: bool) -> None:
    """Load the router's firewall: the final policy, plus (build_egress) the temporary build ruleset on top
    (see netpolicy.router_script for how the build egress is guaranteed to end)."""
    def rules(build: bool) -> str:
        return render(spec, ifmap, uplink, build_egress=build, spec_id=spec_hash(spec)[:12],
                      reserved=pve.cfg.reserved_networks)
    result = pve.script(vmid, router_script(rules(False), rules(True) if build_egress else None), timeout=600)
    if not result.ok:
        raise ValorError("router_policy_failed", "loading the router's nftables policy failed", host=ROUTER,
                         details=result.tail(), hint="Check the generated ruleset (valor plan --show-policy).")


def configure_wireguard(pve: PVE, spec: RangeSpec, vmid: int, uplink: str, steps: Steps) -> dict | None:
    """Set up (or remove) the range's WireGuard access on the router; keys stay encrypted in VALOR's state."""
    cfg = pve.cfg
    if not (spec.access and spec.access.wireguard):
        if wireguard.load(cfg, spec.name) is None:
            return None
        with steps.step("router: remove WireGuard access", ROUTER):
            result = pve.script(vmid, wireguard.router_script(None), timeout=120)
            if not result.ok or "VALOR-WG-REMOVED" not in result.out:
                raise ValorError("wireguard_failed", "removing WireGuard from the router failed", host=ROUTER,
                                 details=result.tail())
            wireguard.forget(cfg, spec.name)
        return None
    with steps.step("router: WireGuard access", ROUTER):
        data = wireguard.ensure(cfg, spec)
        result = pve.script(vmid, wireguard.router_script(wireguard.server_conf(data, spec)), timeout=900)
        if not result.ok or "VALOR-WG-PORT" not in result.out:
            raise ValorError("wireguard_failed", "setting up WireGuard on the router failed", host=ROUTER,
                             details=result.tail())
        # The router's uplink address is the default endpoint in the peers' configs.
        address = pve.exec(vmid, ["ip", "-j", "-4", "addr", "show", "dev", uplink], timeout=30)
        try:
            local = json.loads(address.out)[0]["addr_info"][0]["local"]
            wireguard.set_router_address(cfg, spec.name, local)
        except (ValueError, LookupError):
            local = None
    return {"port": spec.access.wireguard.port, "peers": len(data["peers"]),
            "endpoint": wireguard.endpoint(spec, {**data, "router_address": local})}


# ---- Converging a host (login, roles, baseline) ----------------------------------------------------------

def set_login(pve: PVE, vm: Desired, vmid: int, login: dict | None, steps: Steps) -> None:
    """Console password for the guest user, passed on stdin (never on a command line or in the VM config)."""
    if not login:
        return
    with steps.step("console login", vm.host):
        result = pve.exec(vmid, ["/usr/sbin/chpasswd"], input_data=f"{login['username']}:{login['password']}\n",
                          timeout=60)
        if not result.ok:
            raise ValorError("login_failed", f"could not set the console password on {vm.host}", host=vm.host,
                             details=result.err[-300:])


WINDOWS_READY = ("$s = (Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Setup\\State' "
                 "-ErrorAction SilentlyContinue).ImageState; if ($s -eq 'IMAGE_STATE_COMPLETE') { 'READY' } else { $s }")


def windows_wait_ready(pve: PVE, vmid: int, timeout: int = 1500) -> None:
    """After sysprep a clone runs specialize and OOBE (with a reboot) before it is usable; the agent answers
    earlier than that, so wait for the setup state, not only the agent."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            pve.wait_agent(vmid, 600)
            if "READY" in pve.ps(vmid, WINDOWS_READY, timeout=60).out:
                return
        except ValorError:
            pass
        time.sleep(10)
    raise ValorError("windows_not_ready", f"Windows in VM {vmid} did not finish its first-boot setup",
                     hint="Open the VM console in VALOR or Proxmox to see where setup stopped.")


def windows_reboot(pve: PVE, vmid: int) -> None:
    pve.power(vmid, "shutdown", wait=660, timeout=600, forceStop=1)
    pve.power(vmid, "start")
    windows_wait_ready(pve, vmid)


def _hide(text: str, login: dict | None) -> str:
    """Script output with the range password masked (it goes into errors and the journal)."""
    return text.replace(login["password"], "***") if login else text


def windows_prep(pve: PVE, spec: RangeSpec, vm: Desired, vmid: int, login: dict | None, steps: Steps) -> None:
    """What cloud-init does for Linux: Administrator password (the range login), static IP, DNS, hostname."""
    segment = spec.segment(vm.segment)
    script = windows.prep_script(vm.host, vm.address, segment.cidr.prefixlen, str(segment.gateway),
                                 list(pve.cfg.nameservers), login["password"] if login else None)
    with steps.step("windows: name, address, login", vm.host):
        for _ in range(3):              # a rename asks for a reboot; then the script runs again to confirm
            result = pve.ps(vmid, script, timeout=600)
            if not result.ok or windows.PREP_OK not in result.out:
                raise ValorError("windows_prep_failed", f"preparing Windows on {vm.host} failed", host=vm.host,
                                 details=_hide(result.tail(), login))
            if windows.REBOOT not in result.out:
                return
            windows_reboot(pve, vmid)
        raise ValorError("windows_prep_failed", f"{vm.host} still asks for a reboot after renaming", host=vm.host)


def run_again_if_lost(pve: PVE, vmid: int, run):
    """Role and baseline scripts are idempotent: when the guest agent lost one (it restarted while the script ran, or
    never started it), wait for the agent and run the script again instead of failing the build."""
    for attempt in range(AGENT_LOST_RETRIES + 1):
        try:
            return run()
        except ValorError as e:
            if e.code != "agent_exec_lost" or attempt == AGENT_LOST_RETRIES:
                raise
            pve.wait_agent(vmid, 300)


def run_roles(pve: PVE, spec: RangeSpec, vm: Desired, vmid: int, steps: Steps, login: dict | None = None) -> list[dict]:
    """Run each of the host's roles in spec order; returns [{role, changed}]."""
    cfg = pve.cfg
    host = spec.host(vm.host)
    out = []
    for ref in host.roles:
        with steps.step(f"role {ref.name}", vm.host):
            role = load_role(cfg.roles_dir, ref.name)
            env = role_env(spec, host, role, ref.params)
            if vm.family == "windows":
                result, changed = _run_windows_role(pve, vmid, ref.name, role, env, login, cfg)
            else:
                env["VALOR_NAMESERVERS"] = " ".join(cfg.nameservers)
                body = build_script(role, env)
                result = run_again_if_lost(pve, vmid, lambda: pve.script(vmid, body, timeout=1800))
                changed = "VALOR-ROLE-CHANGED=1" in result.out
            if not result.ok:
                raise ValorError("role_failed", f"role '{ref.name}' failed on {vm.host} (exit {result.exitcode})",
                                 host=vm.host, step=f"role {ref.name}", details=_hide(result.tail(), login),
                                 hint=f"Fix roles/{ref.name}/ or the role params in the spec, then re-apply.")
            out.append({"role": ref.name, "changed": changed})
    return out


def _run_windows_role(pve: PVE, vmid: int, name: str, role, env: dict, login: dict | None, cfg):
    """A Windows role may ask for a reboot (e.g. AD promotion): reboot and run it again, up to 4 times.
    Returns (last result, whether any run changed something)."""
    env.update(VALOR_LOGIN_PASSWORD=login["password"] if login else "",
               VALOR_NAMESERVERS=" ".join(cfg.nameservers))
    script = windows.role_script(name, role.ps_script, env)
    changed = False
    for _ in range(4):
        result = run_again_if_lost(pve, vmid, lambda: pve.ps(vmid, script, timeout=3600))
        if not result.ok:
            break
        changed |= "VALOR-ROLE-CHANGED=1" in result.out
        if windows.REBOOT not in result.out:
            break
        windows_reboot(pve, vmid)
    return result, changed


def run_baseline(pve: PVE, spec: RangeSpec, vm: Desired, vmid: int, steps: Steps) -> list[dict]:
    """Check and fix every control of the host's baseline; any control still failing fails the build."""
    baseline = baseline_for(pve.cfg.baselines_dir, spec.baseline, vm.family)
    if baseline is None:
        return []
    with steps.step(f"baseline {baseline.id}", vm.host):
        if vm.family == "windows":
            body = windows.baseline_bundle(baseline, vm.is_router, fix=True)
            result = run_again_if_lost(pve, vmid, lambda: pve.ps(vmid, body, timeout=1800))
        else:
            body = bundle(baseline, vm.is_router, fix=True)
            result = run_again_if_lost(pve, vmid, lambda: pve.script(vmid, body, timeout=1800))
        results = parse_results(baseline, result.out)
        failed = [control["control"] for control in results if control["after"] != "pass"]
        if not result.ok or failed:
            raise ValorError("baseline_failed",
                             f"baseline controls failed on {vm.host}: {', '.join(failed) or 'script error'}",
                             host=vm.host, step=f"baseline {baseline.id}", details=result.tail(),
                             hint=f"Fix the control in baselines/{baseline.id}.yaml (check and fix must agree), "
                                  "then re-apply.")
        return results


def converge_waves(spec: RangeSpec, hosts: list[Desired], cfg) -> list[list[Desired]]:
    """Order hosts so a host converges after the hosts its roles wait for (role.yaml `wait_for: [param]`)."""
    names = {vm.host for vm in hosts}
    waits_for: dict[str, set[str]] = {vm.host: set() for vm in hosts}
    for vm in hosts:
        for ref in spec.host(vm.host).roles:
            for param in load_role(cfg.roles_dir, ref.name).meta.get("wait_for") or []:
                value = ref.params.get(param)
                for target in value if isinstance(value, list) else [value]:
                    if target in names and target != vm.host:
                        waits_for[vm.host].add(target)
    waves, done = [], set()
    while len(done) < len(hosts):
        wave = [vm for vm in hosts if vm.host not in done and waits_for[vm.host] <= done]
        if not wave:
            raise ValorError("dependency_cycle", "hosts wait for each other: " +
                             ", ".join(f"{host} -> {sorted(targets)}" for host, targets in waits_for.items()
                                       if targets - done))
        waves.append(wave)
        done |= {vm.host for vm in wave}
    return waves


# ---- The build --------------------------------------------------------------------------------------------

def apply(pve: PVE, spec: RangeSpec, emit=lambda *a, **k: None) -> dict:
    """Build or converge the range (see the module docstring). Returns the build result for the journal; raises
    ValorError with the failed step and the completed steps."""
    steps = Steps(emit)
    try:
        return _Build(pve, spec, steps).run()
    except ValorError as e:
        e.completed_steps = steps.completed()
        if e.step is None:
            failed = next((record for record in reversed(steps.records) if record["status"] == "failed"), None)
            if failed:
                e.step = failed["step"]
                e.host = e.host or failed["host"]
        raise
    except Exception as e:
        raise ValorError("internal_error", f"{e.__class__.__name__}: {e}", completed_steps=steps.completed())


class _Build:
    """One run of apply(). The phases are methods, called in order by run()."""

    def __init__(self, pve: PVE, spec: RangeSpec, steps: Steps):
        self.pve, self.spec, self.steps = pve, spec, steps
        self.cfg = pve.cfg
        self.started = time.time()
        self.result: dict = {"range": spec.name, "spec": spec_hash(spec)[:12], "started": now()}
        self.login: dict | None = None
        self.desired: list[Desired] = []
        self.actions: dict[str, dict] = {}         # host -> plan action
        self.vmids: dict[str, int] = {}            # host -> VMID

    def run(self) -> dict:
        self.validate()
        # The login exists before desired_state: its version is part of the converge fingerprints.
        self.login = credentials.ensure(self.cfg, self.spec.name)
        self.desired = desired_state(self.cfg, self.spec, template_ids(self.pve))
        plan = self.compute_plan()
        self.result["plan_summary"] = plan["summary"]
        if not plan["changes"]:
            self.result.update(changed=False, seconds=self.seconds(), steps=self.steps.records)
            return self.result

        self.actions = {action["host"]: action for action in plan["actions"]}
        uplink_mac = self.delete_old_vms(plan["actions"])
        self.create_and_update_vms(uplink_mac)
        self.result["vmids"] = self.vmids
        from_iso = [vm for vm in self.desired
                    if vm.hw.get("install") == "iso" and self.action(vm) in ("create", "replace")]
        self.wait_for_guests(from_iso)

        router = self.desired[0]
        with self.steps.step("map router interfaces", ROUTER):
            ifmap, uplink = router_interfaces(self.pve, self.vmids[ROUTER], len(self.spec.segments), self.spec)
        self.result["router_interfaces"] = {"uplink": uplink, **ifmap}

        hosts = [vm for vm in self.desired[1:] if self.action(vm) in CONVERGE_ACTIONS]
        report: dict = {}
        if hosts:
            report.update(self.converge_hosts(hosts, from_iso, ifmap, uplink))
        router_changed = self.action(router) in CONVERGE_ACTIONS
        if router_changed or hosts:
            report.update(self.converge_router(router, router_changed, ifmap, uplink))
        self.restamp_unchanged_vms()

        self.result.update(changed=True, converged=report, seconds=self.seconds(), steps=self.steps.records)
        return self.result

    # ---- helpers ----

    def seconds(self) -> float:
        return round(time.time() - self.started, 1)

    def action(self, vm: Desired) -> str:
        return self.actions[vm.host]["action"]

    def policy(self, ifmap, uplink, build_egress: bool) -> None:
        load_router_policy(self.pve, self.spec, self.vmids[ROUTER], ifmap, uplink, build_egress=build_egress)

    # ---- phase 1: validate and plan ----

    def validate(self) -> None:
        with self.steps.step("validate"):
            checks = validate_cluster(self.pve, self.spec)
            if not checks["ok"]:
                raise ValorError("validation_failed", f"{len(checks['errors'])} cluster check(s) failed",
                                 details=checks["errors"],
                                 hint="Fix the spec (or ask an administrator for missing templates/bridges) and "
                                      "re-apply.")

    def compute_plan(self) -> dict:
        with self.steps.step("plan"):
            plan = make_plan(self.pve, self.spec, self.desired)
            if not plan["node"]["within_limit"]:
                raise ValorError("insufficient_resources", "the range would exceed the node's memory limit",
                                 details=plan["node"])
        return plan

    # ---- phase 2-3: VMs ----

    def delete_old_vms(self, actions: list[dict]) -> str | None:
        """Delete removed and replaced VMs. Returns the old router's LAN MAC when the router is replaced: the new
        router keeps it, so its DHCP address (the WireGuard endpoint people configured) stays the same."""
        uplink_mac = None
        for action in actions:
            if action["action"] in ("remove", "replace") and action.get("vmid"):
                with self.steps.step(f"{action['action']}: delete old VM {action['vmid']}", action["host"]):
                    if action["host"] == ROUTER and action["action"] == "replace":
                        uplink_mac = _mac(self.pve.vm_config(action["vmid"]).get("net0", ""))
                    self.pve.destroy(action["vmid"])
        return uplink_mac

    def create_and_update_vms(self, uplink_mac: str | None) -> None:
        taken: set[int] = set()
        for vm in self.desired:
            action = self.actions[vm.host]
            if action["action"] in ("create", "replace"):
                with self.steps.step("create VM", vm.host):
                    self.vmids[vm.host] = create_vm(self.pve, self.spec, vm, taken,
                                                    uplink_mac if vm.is_router else None)
                continue
            vmid = self.vmids[vm.host] = action["vmid"]
            if action["action"] == "update" and "reboot required" not in action.get("reasons", []):
                with self.steps.step("change ISO media", vm.host):         # only the CD-ROM changed
                    attach_iso(self.pve, vmid, vm.hw.get("iso"))
                    if self.pve.vm_status(vmid).get("status") != "running":
                        self.pve.power(vmid, "start")
            elif action["action"] == "update":
                with self.steps.step("resize CPU/memory/disk and reboot", vm.host):
                    self.resize_and_reboot(vm, vmid)
            elif action["action"] == "start" or self.pve.vm_status(vmid).get("status") != "running":
                with self.steps.step("start VM", vm.host):
                    self.pve.power(vmid, "start")

    def resize_and_reboot(self, vm: Desired, vmid: int) -> None:
        pve = self.pve
        cpu = "host" if vm.hw.get("nested") else pve.cpu_type(load_catalog(self.cfg).get(vm.os, {}).get("cpu_min"))
        pve.update_config(vmid, cores=vm.cores, memory=vm.memory, vga=vm.hw.get("display", "std"),
                          serial0="socket", cpu=cpu)
        disk = "sata0" if vm.family == "windows" else "scsi0"
        if vm.disk > _disk_gib(str(pve.vm_config(vmid).get(disk, ""))):     # disks only grow
            pve.resize(vmid, disk, vm.disk)
        attach_iso(pve, vmid, vm.hw.get("iso"))
        if pve.vm_status(vmid).get("status") == "running":
            pve.power(vmid, "reboot")
        else:
            pve.power(vmid, "start")

    # ---- phase 4: wait until every guest is usable ----

    def wait_for_guests(self, from_iso: list[Desired]) -> None:
        """Hosts installing from ISO are left out: they only come up once the build egress is open."""
        pve = self.pve
        with self.steps.step("wait for guest agents"):
            for vm in self.desired:
                if vm.family != "windows" and vm not in from_iso:
                    pve.wait_agent(self.vmids[vm.host], 300)
        windows_vms = [vm for vm in self.desired if vm.family == "windows"]
        if windows_vms:
            with self.steps.step("wait for Windows setup"):
                self.in_parallel(lambda vm: windows_wait_ready(pve, self.vmids[vm.host]), windows_vms)
        linux_vms = [vm for vm in self.desired
                     if self.action(vm) != "keep" and vm.family != "windows" and vm.hw.get("install") != "iso"]
        with self.steps.step("wait for cloud-init"):
            self.in_parallel(self.wait_cloud_init, linux_vms)

    def wait_cloud_init(self, vm: Desired) -> None:
        result = self.pve.exec(self.vmids[vm.host], ["cloud-init", "status", "--wait"], timeout=900)
        if result.exitcode not in (0, 2):            # 2: finished with (recoverable) warnings
            raise ValorError("cloud_init_failed", f"cloud-init failed on {vm.host}", host=vm.host,
                             details=result.tail())

    @staticmethod
    def in_parallel(work, vms: list[Desired]) -> None:
        """Run work(vm) for every VM, PARALLEL_HOSTS at a time; the first error is raised."""
        with ThreadPoolExecutor(PARALLEL_HOSTS) as pool:
            for future in as_completed([pool.submit(work, vm) for vm in vms]):
                future.result()

    # ---- phase 6: converge the hosts ----

    def converge_hosts(self, hosts: list[Desired], from_iso: list[Desired], ifmap, uplink) -> dict:
        """Open temporary egress, install ISO hosts, converge everything in waves. A failure anywhere closes the
        egress again before the error is reported, so a failed build never leaves the range open."""
        with self.steps.step("router: temporary build egress", ROUTER):
            self.policy(ifmap, uplink, build_egress=True)
        if from_iso:                                        # installs download through the build egress
            with self.steps.step("install from ISO (kickstart)"):
                try:
                    self.in_parallel(lambda vm: wait_iso_install(self.pve, self.spec, vm, self.vmids[vm.host]),
                                     from_iso)
                except Exception:
                    self.policy(ifmap, uplink, build_egress=False)
                    raise

        report: dict = {}
        errors = []
        for wave in converge_waves(self.spec, hosts, self.cfg):      # e.g. domain controllers before their members
            with ThreadPoolExecutor(PARALLEL_HOSTS) as pool:
                futures = [pool.submit(self.converge_host, vm) for vm in wave]
                for future in as_completed(futures):
                    try:
                        host, host_report = future.result()
                        report[host] = host_report
                    except Exception as e:
                        errors.append(e)
            if errors:
                break
        if errors:
            # restore the final policy before reporting, so a failed build never leaves egress open
            try:
                self.policy(ifmap, uplink, build_egress=False)
            except Exception:
                pass
            raise errors[0]
        return report

    def converge_host(self, vm: Desired) -> tuple[str, dict]:
        vmid = self.vmids[vm.host]
        if vm.family == "windows":
            windows_prep(self.pve, self.spec, vm, vmid, self.login, self.steps)
        else:
            set_login(self.pve, vm, vmid, self.login, self.steps)
        roles = run_roles(self.pve, self.spec, vm, vmid, self.steps, self.login)
        baseline = run_baseline(self.pve, self.spec, vm, vmid, self.steps)
        stamp(self.pve, self.spec, vm, vmid, vm.conv_hash)
        return vm.host, {"roles": roles, "baseline": baseline}

    # ---- phase 7: converge the router ----

    def converge_router(self, router: Desired, router_changed: bool, ifmap, uplink) -> dict:
        """The final policy is loaded whenever anything was built (it also ends the build egress); the router's own
        setup only runs when the router itself changed."""
        report = {}
        vmid = self.vmids[ROUTER]
        if router_changed:
            set_login(self.pve, router, vmid, self.login, self.steps)
            report[ROUTER] = {"baseline": run_baseline(self.pve, self.spec, router, vmid, self.steps)}
        with self.steps.step("router: final policy", ROUTER):
            self.policy(ifmap, uplink, build_egress=False)
        if router_changed:
            access = configure_wireguard(self.pve, self.spec, vmid, uplink, self.steps)
            if access:
                self.result["wireguard"] = access
        stamp(self.pve, self.spec, router, vmid, router.conv_hash)
        return report

    # ---- phase 8: record the spec version ----

    def restamp_unchanged_vms(self) -> None:
        """VMs the build didn't touch still get the new spec version in their metadata (keeping their converge
        fingerprint), so the next plan shows them as up to date."""
        current = spec_hash(self.spec)
        with self.steps.step("record spec version on all VMs"):
            for vm in self.desired:
                if self.action(vm) in ("keep", "start", "restamp"):
                    existing = next((v for v in range_vms(self.pve, self.spec.name) if v.host == vm.host), None)
                    if existing and existing.meta.get("spec") != current:
                        stamp(self.pve, self.spec, vm, self.vmids[vm.host], existing.meta.get("conv"))
