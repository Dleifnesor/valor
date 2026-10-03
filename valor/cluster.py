"""Discovery of range VMs (by pool + tags) and the per-VM metadata the engine stamps on them."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import yaml

from .pve import PVE

META_RE = re.compile(r"<!--valor (\{.*?\}) -->", re.S)


def tag_list(tags: str | None) -> list[str]:
    return [t for t in re.split(r"[;, ]", tags or "") if t]


def range_tag(name: str) -> str:
    return f"valor-range-{name}"


@dataclass
class VMState:
    vmid: int
    name: str
    status: str
    tags: list[str]
    meta: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)

    @property
    def host(self) -> str | None:
        return self.meta.get("host")


def parse_meta(description: str | None) -> dict:
    m = META_RE.search(description or "")
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def render_description(meta: dict, spec_short: str) -> str:
    human = (f"VALOR-managed VM - do not edit by hand; change the range spec and re-apply.\n\n"
             f"range: {meta.get('range')} | host: {meta.get('host')} | spec: {spec_short} | "
             f"converged: {'yes' if meta.get('conv') else 'no'} | applied: {meta.get('applied', '-')}\n\n")
    return human + "<!--valor " + json.dumps(meta, sort_keys=True) + " -->"


def range_vms(pve: PVE, range_name: str | None = None, with_config: bool = True) -> list[VMState]:
    out = []
    for r in pve.resources():
        if r.get("template") or r.get("type") != "qemu":
            continue
        tags = tag_list(r.get("tags"))
        if "valor" not in tags or r.get("pool") != pve.cfg.pool:
            continue
        if range_name and range_tag(range_name) not in tags:
            continue
        vm = VMState(int(r["vmid"]), r.get("name", ""), r.get("status", "unknown"), tags)
        if with_config:
            vm.config = pve.vm_config(vm.vmid)
            vm.meta = parse_meta(vm.config.get("description"))
        out.append(vm)
    return sorted(out, key=lambda v: v.vmid)


def ranges_overview(pve: PVE) -> dict[str, dict]:
    ranges: dict[str, dict] = {}
    for vm in range_vms(pve):
        rn = vm.meta.get("range") or next((t[len("valor-range-"):] for t in vm.tags if t.startswith("valor-range-")), "?")
        r = ranges.setdefault(rn, {"vms": 0, "running": 0, "spec": vm.meta.get("spec", "")[:12], "hosts": []})
        r["vms"] += 1
        r["running"] += vm.status == "running"
        r["hosts"].append({"host": vm.host, "vmid": vm.vmid, "status": vm.status, "converged": bool(vm.meta.get("conv"))})
    return ranges


def vlans_in_use(pve: PVE) -> dict[int, str]:
    """VLAN tags used on the segment bridge by VALOR VMs, mapped to their range."""
    used: dict[int, str] = {}
    for vm in range_vms(pve):
        for k, v in vm.config.items():
            if re.fullmatch(r"net\d+", k) and f"bridge={pve.cfg.segment_bridge}" in v:
                m = re.search(r"tag=(\d+)", v)
                if m:
                    used[int(m.group(1))] = vm.meta.get("range", "?")
    return used


def templates(pve: PVE, catalog: dict) -> dict[str, dict]:
    visible = {int(r["vmid"]): r for r in pve.resources() if r.get("template")}
    out = {}
    for os_name, entry in catalog.items():
        vmid = int(entry["vmid"])
        out[os_name] = {"vmid": vmid, "present": vmid in visible, "family": entry.get("family"),
                        "description": entry.get("description", "")}
    return out


def load_catalog(cfg) -> dict:
    return yaml.safe_load(cfg.catalog_file.read_text())


def cluster_info(pve: PVE) -> dict:
    cfg = pve.cfg
    ns = pve.node_status()
    st = pve.storage_status()
    bridges = {i["iface"]: i for i in pve.network() if i.get("type") == "bridge"}
    seg = bridges.get(cfg.segment_bridge, {})
    return {
        "node": cfg.node,
        "cpu_threads": ns.get("cpuinfo", {}).get("cpus"),
        "memory_total_mib": ns["memory"]["total"] // 2**20,
        "memory_free_mib": ns["memory"]["free"] // 2**20,
        "storage": {"id": cfg.storage, "available_gib": round(st.get("avail", 0) / 2**30, 1)},
        "segment_bridge": {"name": cfg.segment_bridge, "present": bool(seg),
                           "vlan_aware": bool(seg.get("bridge_vlan_aware")),
                           "usable_vlans": f"{cfg.vlan_min}-{cfg.vlan_max}"},
        "uplink_bridge": {"name": cfg.uplink_bridge, "present": cfg.uplink_bridge in bridges},
        "reserved_networks": cfg.extra.get("reserved_networks", ["192.168.1.0/24", "10.10.10.0/24"]),
        "templates": templates(pve, load_catalog(cfg)),
        "default_os": cfg.default_os,
        "vlans_in_use": {str(k): v for k, v in sorted(vlans_in_use(pve).items())},
        "ranges": ranges_overview(pve),
        "roles": sorted(p.name for p in cfg.roles_dir.iterdir() if (p / "role.sh").is_file()),
        "baselines": sorted(p.stem for p in cfg.baselines_dir.glob("*.yaml")),
    }
