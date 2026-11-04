# *draws a tidy plan with crayons* OwO valor/plan.py decides WHAT to do with every VM before a build! ^w^
# For each VM the spec wants, it works out the "desired state" with two fingerprints (hw_hash for the VM's
# hardware, conv_hash for everything set up inside it), then compares them with what's stamped on the real VMs and
# picks one action: create, replace, update, converge, start, restamp or keep (plus remove for VMs no longer in the
# spec). That plan is what a person approves. The full story is in the docstring below *wags tail*
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

# Lazy type hints UwU
from __future__ import annotations

# hashlib/json (fingerprints), dataclass/field (Desired), Path (the SSH key file) >w<
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

# Everything that feeds a fingerprint: credentials (login version), wireguard (VPN digest), baselines, the cluster
# (VMs, catalog, templates), the firewall render, PVE, roles, spec bits, and the Windows prep version OwO
from . import credentials, wireguard
from .baseline import baseline_for, load_baseline
from .cluster import VMState, load_catalog, range_vms, template_ids
from .netpolicy import render
from .pve import PVE
from .roles import load_role
from .spec import ROUTER, Host, RangeSpec, spec_hash
from .windows import PREP_VERSION as WINDOWS_PREP

# CONVERGE_PROTOCOL: part of every conv_hash; bump it on purpose when the in-guest setup logic changes, so every VM
# re-converges. DISPLAY: the virtual graphics card for range VMs ^w^
CONVERGE_PROTOCOL = 1     # bump when the engine's in-guest converge logic changes
DISPLAY = "std"           # Proxmox vga type for range VMs (a serial console is always attached as well)


# _h: a short, stable fingerprint of anything JSON-able (sorted keys, so the same data always gives the same hash) UwU
def _h(obj) -> str:
    """Short, stable fingerprint of any JSON-able value."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


# Desired: what one VM should look like >w<
#   host/is_router  which VM; os/template = what it's cloned from (-1: template missing, 0: installed from ISO)
#   cores/memory/disk  its size; nics = its network cards; roles = its services (with each role's digest)
#   segment/address/family  where it sits and its OS family
#   hw / hw_hash / conv_hash  the hardware description and both fingerprints
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

    # vm_name: the host name (the "<range>-" prefix is added when the VM is created) OwO
    @property
    def vm_name(self) -> str:
        return self.host


# ---- Desired state ----------------------------------------------------------------------------------------

# desired_state: one Desired per VM: the router first, then the hosts in spec order ^w^
def desired_state(cfg, spec: RangeSpec, template_ids: dict[str, int] | None = None) -> list[Desired]:
    """template_ids: OS name -> template VMID (cluster.template_ids); an OS without one plans template -1."""
    inputs = _SharedInputs(cfg, spec, template_ids or {})
    return [_router(inputs)] + [_host(inputs, host) for host in spec.hosts]


# _SharedInputs: things every VM's fingerprint needs, looked up ONCE UwU
class _SharedInputs:
    """Everything the router and all hosts have in common, looked up once."""

    def __init__(self, cfg, spec: RangeSpec, template_ids: dict[str, int]):
        self.cfg, self.spec, self.template_ids = cfg, spec, template_ids
        # VALOR's SSH public key (only its hash goes into fingerprints) >w<
        key_file = Path(cfg.ssh_public_key)
        ssh_key = key_file.read_text().strip() if key_file.exists() else ""
        # The baseline's digest (Linux), and the Windows twin's digest if the range has Windows hosts OwO
        baseline = load_baseline(cfg.baselines_dir, spec.baseline)
        self.baseline_digest = baseline.digest if baseline else None
        self.catalog = load_catalog(cfg)
        has_windows = any(self.catalog.get(host.os or "", {}).get("family") == "windows" for host in spec.hosts)
        windows_baseline = baseline_for(cfg.baselines_dir, spec.baseline, "windows") if has_windows else None
        self.windows_baseline_digest = windows_baseline.digest if windows_baseline else None
        # Part of every VM's hardware: changing the login user, its key or DNS servers means a new VM.
        # display: a normal screen (Proxmox noVNC console) plus the serial console on every VM.
        # common_hw: part of every VM's hardware: DNS servers, admin user, SSH key hash, display ^w^
        self.common_hw = {"nameservers": list(cfg.nameservers), "user": cfg.guest_user, "sshkey": _h(ssh_key),
                          "display": DISPLAY}
        # login_version: the range password's version (a rotation re-converges every VM) UwU
        self.login_version = credentials.version(cfg, spec.name)     # a new password version re-converges every VM

    # template: an OS's template id, or -1 when it isn't built >w<
    def template(self, os_name: str) -> int:
        return self.template_ids.get(os_name, -1)


# _router: the router's desired state OwO
def _router(inputs: _SharedInputs) -> Desired:
    """The router: uplink first (eth0), then one NIC per segment in spec order, each holding the segment's
    gateway address."""
    cfg, spec = inputs.cfg, inputs.spec
    router = spec.router
    # NICs: eth0 = the uplink (DHCP, on the uplink bridge/VLAN), then one per segment holding the gateway address ^w^
    nics = [{"bridge": cfg.uplink_bridge, "vlan": cfg.uplink_vlan or None, "ip": "dhcp", "gw": None}]
    for segment in spec.segments:
        nics.append({"bridge": cfg.segment_bridge, "vlan": segment.vlan,
                     "ip": f"{segment.gateway}/{segment.cidr.prefixlen}", "gw": None})
    # Its hardware description and hw_hash UwU
    vm = Desired(ROUTER, True, router.os, inputs.template(router.os), router.cores, router.memory, router.disk, nics)
    vm.hw = {"os": vm.os, "template": vm.template, "cores": vm.cores, "memory": vm.memory, "disk": vm.disk,
             "nics": nics, **inputs.common_hw}
    vm.hw_hash = _h(vm.hw)

    # The router's converge state includes its final firewall rules, so a policy change re-converges it.
    # Render the FINAL firewall rules exactly as the router will get them, and hash them >w<
    interfaces = {segment.name: f"eth{i + 1}" for i, segment in enumerate(spec.segments)}
    final_rules = render(spec, interfaces, "eth0", build_egress=False, spec_id="", reserved=cfg.reserved_networks)
    wireguard_digest = wireguard.digest(cfg, spec)
    # conv_hash: hardware + policy + baseline + protocol (+ login version, + WireGuard) OwO
    vm.conv_hash = _h({"hw": vm.hw_hash, "policy": _h(final_rules), "baseline": inputs.baseline_digest,
                       "proto": CONVERGE_PROTOCOL,
                       **({"login": inputs.login_version} if inputs.login_version else {}),
                       **({"wireguard": wireguard_digest} if wireguard_digest else {})})
    return vm


# _host: one host's desired state ^w^
def _host(inputs: _SharedInputs, host: Host) -> Desired:
    cfg, spec = inputs.cfg, inputs.spec
    # Its segment, catalog entry and OS family UwU
    segment = spec.segment(host.segment)
    entry = inputs.catalog.get(host.os or "", {})
    family = entry.get("family", "debian")
    windows = family == "windows"
    # One NIC on its segment's VLAN with its address and the gateway; Windows gets the e1000e model (its driver is
    # built in) >w<
    nics = [{"bridge": cfg.segment_bridge, "vlan": segment.vlan, "ip": f"{host.address}/{segment.cidr.prefixlen}",
             "gw": str(segment.gateway)}]
    if windows:
        nics[0]["model"] = "e1000e"               # Windows ships this driver; Linux NICs stay virtio
    # Its roles, each with the role's digest (editing a role's script re-converges the hosts using it) OwO
    roles = [{"name": ref.name, "params": ref.params, "digest": _role_digest(cfg, ref.name)} for ref in host.roles]

    # Windows needs more than the Linux defaults; the catalog sets the floor (a template's disk can't shrink).
    # Windows: at least the catalog's minimum size ^w^
    if windows:
        cores = max(host.cores, int(entry.get("default_cores", 0)))
        memory = max(host.memory, int(entry.get("default_memory", 0)))
        disk = max(host.disk, int(entry.get("default_disk", 0)))
    else:
        cores, memory, disk = host.cores, host.memory, host.disk

    # install: iso = no template (template 0) UwU
    from_iso = host.install == "iso"
    vm = Desired(host.name, False, host.os, 0 if from_iso else inputs.template(host.os), cores, memory, disk, nics,
                 roles, host.segment, str(host.address), family=family)
    # Hardware: everything that can't change in place, plus extras only when they apply (family for Windows, an
    # attached ISO, ISO install, nested virtualization), so older VMs' hashes stay the same >w<
    vm.hw = {"os": vm.os, "template": "iso" if from_iso else vm.template, "cores": vm.cores, "memory": vm.memory,
             "disk": vm.disk, "nics": nics, **inputs.common_hw,
             **({"family": family} if windows else {}),
             **({"iso": host.iso} if host.iso else {}),
             **({"install": "iso"} if from_iso else {}),
             **({"nested": True} if host.nested else {})}
    vm.hw_hash = _h(vm.hw)
    # conv_hash: hardware + roles + the right baseline + protocol (+ login, + Windows prep version) OwO
    baseline_digest = inputs.windows_baseline_digest if windows else inputs.baseline_digest
    vm.conv_hash = _h({"hw": vm.hw_hash, "roles": roles, "baseline": baseline_digest, "proto": CONVERGE_PROTOCOL,
                       **({"login": inputs.login_version} if inputs.login_version else {}),
                       **({"prep": WINDOWS_PREP} if windows else {})})
    return vm


# _role_digest: a role's digest, or "missing" (validation reports missing roles) ^w^
def _role_digest(cfg, name: str) -> str:
    """A role's content digest, so editing a role's script re-converges the hosts that use it. A missing role
    still plans (validation reports it)."""
    try:
        return load_role(cfg.roles_dir, name).digest
    except Exception:
        return "missing"


# ---- The plan ---------------------------------------------------------------------------------------------

# _classify: pick ONE action for a VM (the list in the docstring goes from most to least disruptive) UwU
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
    # No VM: create. No metadata: replace (we can't know what it is) >w<
    if vm is None:
        return "create", ["new VM"]
    old = vm.meta.get("hw_spec") or {}
    if not old:
        return "replace", ["VM has no VALOR metadata"]

    # Things a VM can't change in place -> replace OwO
    reasons: list[str] = []
    for key in ("os", "template", "nics", "nameservers", "user", "sshkey"):
        if old.get(key) != want.hw.get(key):
            reasons.append(f"{key} changed")
    if old.get("disk", 0) > want.disk:
        reasons.append("disk cannot shrink")
    if reasons:
        return "replace", reasons

    # Size/display/nesting changes -> update (needs a reboot); a different attached ISO -> update (works live) ^w^
    for key in ("cores", "memory", "disk", "display", "nested"):
        if old.get(key) != want.hw.get(key):
            reasons.append(f"{key} {old.get(key)} -> {want.hw.get(key)}")
    if reasons:
        reasons.append("reboot required")
    if old.get("iso") != want.hw.get("iso"):                   # CD-ROM media can change while the VM runs
        reasons.insert(0, f"ISO {old.get('iso') or 'none'} -> {want.hw.get('iso') or 'none'}")
    if reasons:
        return "update", reasons

    # Inside-the-guest setup differs (or the last apply didn't finish stamping) -> converge UwU
    if vm.meta.get("conv") != want.conv_hash:
        reason = "roles, baseline or policy changed" if vm.meta.get("conv") else "previous apply did not finish"
        return "converge", [reason]
    # Stopped -> start; only the spec version differs -> restamp; otherwise keep >w<
    if vm.status != "running":
        return "start", [f"VM is {vm.status}"]
    if current_spec and vm.meta.get("spec") != current_spec:
        return "restamp", ["spec version changed; VM itself unchanged (metadata only)"]
    return "keep", []


# make_plan: the whole plan for a range OwO
def make_plan(pve: PVE, spec: RangeSpec, desired: list[Desired] | None = None) -> dict:
    """The plan a person approves before a build: one action per VM, totals, and whether the node has the memory."""
    cfg = pve.cfg
    # Desired state, the range's VMs, those with and without metadata, and the spec's hash ^w^
    desired = desired or desired_state(cfg, spec, template_ids(pve))
    vms = range_vms(pve, spec.name)
    existing = {vm.host: vm for vm in vms if vm.host}
    without_metadata = [vm for vm in vms if not vm.host]
    current_spec = spec_hash(spec)

    # One action per desired VM, then "remove" for VMs not in the spec and for VMs without metadata UwU
    actions = [_action(want, existing.get(want.host), current_spec) for want in desired]
    wanted = {want.host for want in desired}
    for host, vm in existing.items():
        if host not in wanted:
            actions.append({"host": host, "action": "remove", "reasons": ["not in spec"], "vmid": vm.vmid})
    for vm in without_metadata:
        actions.append({"host": vm.name, "action": "remove", "reasons": ["VM without VALOR metadata"],
                        "vmid": vm.vmid})

    # Count actions per kind, and the totals the range needs >w<
    summary: dict[str, int] = {}
    for action in actions:
        summary[action["action"]] = summary.get(action["action"], 0) + 1
    totals = {"vms": len(desired), "cores": sum(want.cores for want in desired),
              "memory_mib": sum(want.memory for want in desired), "disk_gib": sum(want.disk for want in desired)}
    # The plan: range, spec version, whether anything changes, summary, actions, totals and the memory check OwO
    return {
        "range": spec.name, "spec": current_spec[:12],
        "changes": any(action["action"] != "keep" for action in actions),
        "summary": summary, "actions": actions, "totals": totals,
        "node": _node_memory(pve, desired, existing, totals["memory_mib"]),
    }


# _action: one plan row: action, reasons, vmid, role, OS, segment, address(es), size and services ^w^
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


# _node_memory: would the node stay under max_memory_fraction with this range running? UwU
def _node_memory(pve: PVE, desired: list[Desired], existing: dict[str, VMState], range_memory: int) -> dict:
    """Would the node stay under max_memory_fraction once the range runs? Memory of the range's VMs that already
    run is in use today, so only the difference counts."""
    cfg = pve.cfg
    # Free and total memory in MiB (Proxmox gives bytes) >w<
    status = pve.node_status()
    free = status["memory"]["free"] // 2**20
    total = status["memory"]["total"] // 2**20
    # Memory of this range's VMs that ALREADY run is in use today, so only the extra counts OwO
    running_now = 0
    for want in desired:
        vm = existing.get(want.host)
        if vm and vm.status == "running":
            running_now += int(vm.config.get("memory") or 0)
    extra = range_memory - running_now
    # Within limit = (used now + extra) <= total x fraction ^w^ *checks the math twice*
    within_limit = (total - free + max(extra, 0)) <= total * cfg.max_memory_fraction
    return {"name": cfg.node, "memory_total_mib": total, "memory_free_mib": free,
            "additional_memory_mib": max(extra, 0), "within_limit": within_limit,
            "limit_fraction": cfg.max_memory_fraction}
