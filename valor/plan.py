"""Desired state per VM and the create / update / replace / keep / remove plan.

How VALOR decides what to do with a VM:

1. `desired_state` turns the spec into one `Desired` per VM (the router first, then the hosts). Each carries two
   fingerprints:
     hw_hash    the VM's "hardware": OS/template, size, NICs, login user/key. Stamped on the VM when it is created.
     conv_hash  everything configured inside the guest: hw_hash plus roles, baseline, firewall policy, login
                password version, WireGuard. Stamped on the VM after a successful converge.
2. `make_plan` compares that with the metadata stamped on the existing VMs (cluster.parse_meta) and picks one
   action per VM (see `_classify`), plus "remove" for VMs that are no longer in the spec.

Changing what goes into a fingerprint changes it for every existing VM, so every range would want a rebuild or
re-converge: CONVERGE_PROTOCOL is bumped on purpose when the in-guest logic changes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import credentials, wireguard
from .baseline import baseline_for, load_baseline
from .cluster import VMState, load_catalog, range_vms, template_ids
from .netpolicy import render
from .pve import PVE
from .roles import load_role
from .spec import ROUTER, Host, RangeSpec, spec_hash
from .windows import PREP_VERSION as WINDOWS_PREP

CONVERGE_PROTOCOL = 1     # bump when the engine's in-guest converge logic changes
DISPLAY = "std"           # Proxmox vga type for range VMs (a serial console is always attached as well)


def _h(obj) -> str:
    """Short, stable fingerprint of any JSON-able value."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass
class Desired:
    """What one VM of the range should look like."""
    host: str                 # host name, or 'rtr'
    is_router: bool
    os: str
    template: int             # template VMID to clone (-1: no template built, 0: installed from ISO)
    cores: int
    memory: int
    disk: int
    nics: list[dict]          # [{bridge, vlan, ip, gw}]
    roles: list[dict] = field(default_factory=list)
    segment: str | None = None
    address: str | None = None
    family: str = "debian"    # debian | rhel | windows (from the OS catalog)
    hw: dict = field(default_factory=dict)
    hw_hash: str = ""
    conv_hash: str = ""

    @property
    def vm_name(self) -> str:
        return self.host


# ---- Desired state ----------------------------------------------------------------------------------------

def desired_state(cfg, spec: RangeSpec, template_ids: dict[str, int] | None = None) -> list[Desired]:
    """template_ids: OS name -> template VMID (cluster.template_ids); an OS without one plans template -1."""
    inputs = _SharedInputs(cfg, spec, template_ids or {})
    return [_router(inputs)] + [_host(inputs, host) for host in spec.hosts]


class _SharedInputs:
    """Everything the router and all hosts have in common, looked up once."""

    def __init__(self, cfg, spec: RangeSpec, template_ids: dict[str, int]):
        self.cfg, self.spec, self.template_ids = cfg, spec, template_ids
        key_file = Path(cfg.ssh_public_key)
        ssh_key = key_file.read_text().strip() if key_file.exists() else ""
        baseline = load_baseline(cfg.baselines_dir, spec.baseline)
        self.baseline_digest = baseline.digest if baseline else None
        self.catalog = load_catalog(cfg)
        has_windows = any(self.catalog.get(host.os or "", {}).get("family") == "windows" for host in spec.hosts)
        windows_baseline = baseline_for(cfg.baselines_dir, spec.baseline, "windows") if has_windows else None
        self.windows_baseline_digest = windows_baseline.digest if windows_baseline else None
        # Part of every VM's hardware: changing the login user, its key or DNS servers means a new VM.
        # display: a normal screen (Proxmox noVNC console) plus the serial console on every VM.
        self.common_hw = {"nameservers": list(cfg.nameservers), "user": cfg.guest_user, "sshkey": _h(ssh_key),
                          "display": DISPLAY}
        self.login_version = credentials.version(cfg, spec.name)     # a new password version re-converges every VM

    def template(self, os_name: str) -> int:
        return self.template_ids.get(os_name, -1)


def _router(inputs: _SharedInputs) -> Desired:
    """The router: uplink first (eth0), then one NIC per segment in spec order, each holding the segment's
    gateway address."""
    cfg, spec = inputs.cfg, inputs.spec
    router = spec.router
    nics = [{"bridge": cfg.uplink_bridge, "vlan": cfg.uplink_vlan or None, "ip": "dhcp", "gw": None}]
    for segment in spec.segments:
        nics.append({"bridge": cfg.segment_bridge, "vlan": segment.vlan,
                     "ip": f"{segment.gateway}/{segment.cidr.prefixlen}", "gw": None})
    vm = Desired(ROUTER, True, router.os, inputs.template(router.os), router.cores, router.memory, router.disk, nics)
    vm.hw = {"os": vm.os, "template": vm.template, "cores": vm.cores, "memory": vm.memory, "disk": vm.disk,
             "nics": nics, **inputs.common_hw}
    vm.hw_hash = _h(vm.hw)

    # The router's converge state includes its final firewall rules, so a policy change re-converges it.
    interfaces = {segment.name: f"eth{i + 1}" for i, segment in enumerate(spec.segments)}
    final_rules = render(spec, interfaces, "eth0", build_egress=False, spec_id="", reserved=cfg.reserved_networks)
    wireguard_digest = wireguard.digest(cfg, spec)
    vm.conv_hash = _h({"hw": vm.hw_hash, "policy": _h(final_rules), "baseline": inputs.baseline_digest,
                       "proto": CONVERGE_PROTOCOL,
                       **({"login": inputs.login_version} if inputs.login_version else {}),
                       **({"wireguard": wireguard_digest} if wireguard_digest else {})})
    return vm


def _host(inputs: _SharedInputs, host: Host) -> Desired:
    cfg, spec = inputs.cfg, inputs.spec
    segment = spec.segment(host.segment)
    entry = inputs.catalog.get(host.os or "", {})
    family = entry.get("family", "debian")
    windows = family == "windows"
    nics = [{"bridge": cfg.segment_bridge, "vlan": segment.vlan, "ip": f"{host.address}/{segment.cidr.prefixlen}",
             "gw": str(segment.gateway)}]
    if windows:
        nics[0]["model"] = "e1000e"               # Windows ships this driver; Linux NICs stay virtio
    roles = [{"name": ref.name, "params": ref.params, "digest": _role_digest(cfg, ref.name)} for ref in host.roles]

    # Windows needs more than the Linux defaults; the catalog sets the floor (a template's disk can't shrink).
    if windows:
        cores = max(host.cores, int(entry.get("default_cores", 0)))
        memory = max(host.memory, int(entry.get("default_memory", 0)))
        disk = max(host.disk, int(entry.get("default_disk", 0)))
    else:
        cores, memory, disk = host.cores, host.memory, host.disk

    from_iso = host.install == "iso"
    vm = Desired(host.name, False, host.os, 0 if from_iso else inputs.template(host.os), cores, memory, disk, nics,
                 roles, host.segment, str(host.address), family=family)
    vm.hw = {"os": vm.os, "template": "iso" if from_iso else vm.template, "cores": vm.cores, "memory": vm.memory,
             "disk": vm.disk, "nics": nics, **inputs.common_hw,
             **({"family": family} if windows else {}),
             **({"iso": host.iso} if host.iso else {}),
             **({"install": "iso"} if from_iso else {}),
             **({"nested": True} if host.nested else {})}
    vm.hw_hash = _h(vm.hw)
    baseline_digest = inputs.windows_baseline_digest if windows else inputs.baseline_digest
    vm.conv_hash = _h({"hw": vm.hw_hash, "roles": roles, "baseline": baseline_digest, "proto": CONVERGE_PROTOCOL,
                       **({"login": inputs.login_version} if inputs.login_version else {}),
                       **({"prep": WINDOWS_PREP} if windows else {})})
    return vm


def _role_digest(cfg, name: str) -> str:
    """A role's content digest, so editing a role's script re-converges the hosts that use it. A missing role
    still plans (validation reports it)."""
    try:
        return load_role(cfg.roles_dir, name).digest
    except Exception:
        return "missing"


# ---- The plan ---------------------------------------------------------------------------------------------

def _classify(want: Desired, vm: VMState | None, current_spec: str = "") -> tuple[str, list[str]]:
    """The action for one VM, with the reasons shown in the plan. From most to least disruptive:

    create    no VM yet
    replace   something a running VM can't change (OS, template, NICs, login, a smaller disk): rebuild it
    update    size, display, nesting or the attached ISO changed: reconfigure in place (some need a reboot)
    converge  roles, baseline or policy changed, or the last apply didn't finish: re-run in-guest setup
    start     everything matches but the VM is not running
    restamp   only the spec version differs (e.g. a description changed): update the VM's metadata
    keep      nothing to do
    """
    if vm is None:
        return "create", ["new VM"]
    old = vm.meta.get("hw_spec") or {}
    if not old:
        return "replace", ["VM has no VALOR metadata"]

    reasons: list[str] = []
    for key in ("os", "template", "nics", "nameservers", "user", "sshkey"):
        if old.get(key) != want.hw.get(key):
            reasons.append(f"{key} changed")
    if old.get("disk", 0) > want.disk:
        reasons.append("disk cannot shrink")
    if reasons:
        return "replace", reasons

    for key in ("cores", "memory", "disk", "display", "nested"):
        if old.get(key) != want.hw.get(key):
            reasons.append(f"{key} {old.get(key)} -> {want.hw.get(key)}")
    if reasons:
        reasons.append("reboot required")
    if old.get("iso") != want.hw.get("iso"):                   # CD-ROM media can change while the VM runs
        reasons.insert(0, f"ISO {old.get('iso') or 'none'} -> {want.hw.get('iso') or 'none'}")
    if reasons:
        return "update", reasons

    if vm.meta.get("conv") != want.conv_hash:
        reason = "roles, baseline or policy changed" if vm.meta.get("conv") else "previous apply did not finish"
        return "converge", [reason]
    if vm.status != "running":
        return "start", [f"VM is {vm.status}"]
    if current_spec and vm.meta.get("spec") != current_spec:
        return "restamp", ["spec version changed; VM itself unchanged (metadata only)"]
    return "keep", []


def make_plan(pve: PVE, spec: RangeSpec, desired: list[Desired] | None = None) -> dict:
    """The plan a person approves before a build: one action per VM, totals, and whether the node has the memory."""
    cfg = pve.cfg
    desired = desired or desired_state(cfg, spec, template_ids(pve))
    vms = range_vms(pve, spec.name)
    existing = {vm.host: vm for vm in vms if vm.host}
    without_metadata = [vm for vm in vms if not vm.host]
    current_spec = spec_hash(spec)

    actions = [_action(want, existing.get(want.host), current_spec) for want in desired]
    wanted = {want.host for want in desired}
    for host, vm in existing.items():
        if host not in wanted:
            actions.append({"host": host, "action": "remove", "reasons": ["not in spec"], "vmid": vm.vmid})
    for vm in without_metadata:
        actions.append({"host": vm.name, "action": "remove", "reasons": ["VM without VALOR metadata"],
                        "vmid": vm.vmid})

    summary: dict[str, int] = {}
    for action in actions:
        summary[action["action"]] = summary.get(action["action"], 0) + 1
    totals = {"vms": len(desired), "cores": sum(want.cores for want in desired),
              "memory_mib": sum(want.memory for want in desired), "disk_gib": sum(want.disk for want in desired)}
    return {
        "range": spec.name, "spec": current_spec[:12],
        "changes": any(action["action"] != "keep" for action in actions),
        "summary": summary, "actions": actions, "totals": totals,
        "node": _node_memory(pve, desired, existing, totals["memory_mib"]),
    }


def _action(want: Desired, vm: VMState | None, current_spec: str) -> dict:
    action, reasons = _classify(want, vm, current_spec)
    # The router has one address per segment; show them all.
    address = ", ".join(nic["ip"] for nic in want.nics) if want.is_router else want.address
    return {
        "host": want.host, "action": action, "reasons": reasons, "vmid": vm.vmid if vm else None,
        "role": "router" if want.is_router else "host", "os": want.os, "segment": want.segment,
        "address": address,
        "cores": want.cores, "memory_mib": want.memory, "disk_gib": want.disk,
        "services": [role["name"] for role in want.roles],
    }


def _node_memory(pve: PVE, desired: list[Desired], existing: dict[str, VMState], range_memory: int) -> dict:
    """Would the node stay under max_memory_fraction once the range runs? Memory of the range's VMs that already
    run is in use today, so only the difference counts."""
    cfg = pve.cfg
    status = pve.node_status()
    free = status["memory"]["free"] // 2**20
    total = status["memory"]["total"] // 2**20
    running_now = 0
    for want in desired:
        vm = existing.get(want.host)
        if vm and vm.status == "running":
            running_now += int(vm.config.get("memory") or 0)
    extra = range_memory - running_now
    within_limit = (total - free + max(extra, 0)) <= total * cfg.max_memory_fraction
    return {"name": cfg.node, "memory_total_mib": total, "memory_free_mib": free,
            "additional_memory_mib": max(extra, 0), "within_limit": within_limit,
            "limit_fraction": cfg.max_memory_fraction}
