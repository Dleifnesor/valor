"""Desired state per VM and the create / update / replace / keep / remove plan."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import credentials
from .baseline import load_baseline
from .cluster import VMState, load_catalog, range_vms, template_ids
from .netpolicy import render
from .pve import PVE
from .roles import load_role
from .spec import ROUTER, RangeSpec, spec_hash

CONVERGE_PROTOCOL = 1     # bump when the engine's in-guest converge logic changes
DISPLAY = "std"           # Proxmox vga type for range VMs (a serial console is always attached as well)


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass
class Desired:
    host: str                 # host name, or 'rtr'
    is_router: bool
    os: str
    template: int
    cores: int
    memory: int
    disk: int
    nics: list[dict]          # [{bridge, vlan, ip, gw}]
    roles: list[dict] = field(default_factory=list)
    segment: str | None = None
    address: str | None = None
    hw: dict = field(default_factory=dict)
    hw_hash: str = ""
    conv_hash: str = ""

    @property
    def vm_name(self) -> str:
        return self.host


def desired_state(cfg, spec: RangeSpec, template_ids: dict[str, int] | None = None) -> list[Desired]:
    """template_ids: OS name -> template VMID (cluster.template_ids); without it, legacy catalog VMIDs are used."""
    if template_ids is None:
        template_ids = {k: int(v["vmid"]) for k, v in load_catalog(cfg).items() if v.get("vmid")}

    def tpl(os_name: str) -> int:
        return template_ids.get(os_name, -1)

    pub = Path(cfg.ssh_public_key).read_text().strip() if Path(cfg.ssh_public_key).exists() else ""
    baseline = load_baseline(cfg.baselines_dir, spec.baseline)
    base_digest = baseline.digest if baseline else None
    # display: a normal screen (Proxmox noVNC console) plus the serial console on every VM
    common = {"nameservers": list(cfg.nameservers), "user": cfg.guest_user, "sshkey": _h(pub), "display": DISPLAY}
    login = credentials.version(cfg, spec.name)        # a new password version re-converges every VM
    out: list[Desired] = []

    # Router: uplink first (eth0), then one NIC per segment in spec order.
    r = spec.router
    nics = [{"bridge": cfg.uplink_bridge, "vlan": cfg.uplink_vlan or None, "ip": "dhcp", "gw": None}]
    for s in spec.segments:
        nics.append({"bridge": cfg.segment_bridge, "vlan": s.vlan, "ip": f"{s.gateway}/{s.cidr.prefixlen}", "gw": None})
    d = Desired(ROUTER, True, r.os, tpl(r.os), r.cores, r.memory, r.disk, nics)
    ifmap = {s.name: f"eth{i + 1}" for i, s in enumerate(spec.segments)}
    final_rules = render(spec, ifmap, "eth0", build_egress=False, spec_id="", reserved=cfg.reserved_networks)
    d.hw = {"os": d.os, "template": d.template, "cores": d.cores, "memory": d.memory, "disk": d.disk, "nics": nics, **common}
    d.hw_hash = _h(d.hw)
    d.conv_hash = _h({"hw": d.hw_hash, "policy": _h(final_rules), "baseline": base_digest, "proto": CONVERGE_PROTOCOL,
                      **({"login": login} if login else {})})
    out.append(d)

    for h in spec.hosts:
        seg = spec.segment(h.segment)
        nics = [{"bridge": cfg.segment_bridge, "vlan": seg.vlan, "ip": f"{h.address}/{seg.cidr.prefixlen}",
                 "gw": str(seg.gateway)}]
        roles = []
        for ref in h.roles:
            try:
                digest = load_role(cfg.roles_dir, ref.name).digest
            except Exception:
                digest = "missing"
            roles.append({"name": ref.name, "params": ref.params, "digest": digest})
        d = Desired(h.name, False, h.os, tpl(h.os), h.cores, h.memory, h.disk, nics, roles, h.segment,
                    str(h.address))
        d.hw = {"os": d.os, "template": d.template, "cores": d.cores, "memory": d.memory, "disk": d.disk, "nics": nics, **common}
        d.hw_hash = _h(d.hw)
        d.conv_hash = _h({"hw": d.hw_hash, "roles": roles, "baseline": base_digest, "proto": CONVERGE_PROTOCOL,
                          **({"login": login} if login else {})})
        out.append(d)
    return out


def _classify(d: Desired, vm: VMState | None, current_spec: str = "") -> tuple[str, list[str]]:
    if vm is None:
        return "create", ["new VM"]
    old = vm.meta.get("hw_spec") or {}
    reasons: list[str] = []
    if not old:
        return "replace", ["VM has no VALOR metadata"]
    for key in ("os", "template", "nics", "nameservers", "user", "sshkey"):
        if old.get(key) != d.hw.get(key):
            reasons.append(f"{key} changed")
    if old.get("disk", 0) > d.disk:
        reasons.append("disk cannot shrink")
    if reasons:
        return "replace", reasons
    for key in ("cores", "memory", "disk", "display"):
        if old.get(key) != d.hw.get(key):
            reasons.append(f"{key} {old.get(key)} -> {d.hw.get(key)}")
    if reasons:
        return "update", reasons + ["reboot required"]
    if vm.meta.get("conv") != d.conv_hash:
        return "converge", ["roles, baseline or policy changed" if vm.meta.get("conv") else "previous apply did not finish"]
    if vm.status != "running":
        return "start", [f"VM is {vm.status}"]
    if current_spec and vm.meta.get("spec") != current_spec:
        return "restamp", ["spec version changed; VM itself unchanged (metadata only)"]
    return "keep", []


def make_plan(pve: PVE, spec: RangeSpec, desired: list[Desired] | None = None) -> dict:
    cfg = pve.cfg
    desired = desired or desired_state(cfg, spec, template_ids(pve))
    existing = {vm.host: vm for vm in range_vms(pve, spec.name) if vm.host}
    unknown = [vm for vm in range_vms(pve, spec.name) if not vm.host]
    actions = []
    for d in desired:
        vm = existing.get(d.host)
        action, reasons = _classify(d, vm, spec_hash(spec))
        actions.append({
            "host": d.host, "action": action, "reasons": reasons, "vmid": vm.vmid if vm else None,
            "role": "router" if d.is_router else "host", "os": d.os, "segment": d.segment,
            "address": d.address if not d.is_router else
            ", ".join(n["ip"] for n in d.nics),
            "cores": d.cores, "memory_mib": d.memory, "disk_gib": d.disk,
            "services": [r["name"] for r in d.roles],
        })
    wanted = {d.host for d in desired}
    for host, vm in existing.items():
        if host not in wanted:
            actions.append({"host": host, "action": "remove", "reasons": ["not in spec"], "vmid": vm.vmid})
    for vm in unknown:
        actions.append({"host": vm.name, "action": "remove", "reasons": ["VM without VALOR metadata"], "vmid": vm.vmid})

    summary: dict[str, int] = {}
    for a in actions:
        summary[a["action"]] = summary.get(a["action"], 0) + 1
    totals = {"vms": len(desired), "cores": sum(d.cores for d in desired),
              "memory_mib": sum(d.memory for d in desired), "disk_gib": sum(d.disk for d in desired)}
    ns = pve.node_status()
    free = ns["memory"]["free"] // 2**20
    total = ns["memory"]["total"] // 2**20
    running_now = sum(existing[d.host].config.get("memory", 0) and int(existing[d.host].config["memory"])
                      for d in desired if d.host in existing and existing[d.host].status == "running")
    extra = totals["memory_mib"] - running_now
    limit_ok = (total - free + max(extra, 0)) <= total * cfg.max_memory_fraction
    changes = any(a["action"] != "keep" for a in actions)
    return {
        "range": spec.name, "spec": spec_hash(spec)[:12], "changes": changes,
        "summary": summary, "actions": actions, "totals": totals,
        "node": {"name": cfg.node, "memory_total_mib": total, "memory_free_mib": free,
                 "additional_memory_mib": max(extra, 0), "within_limit": limit_ok,
                 "limit_fraction": cfg.max_memory_fraction},
    }
