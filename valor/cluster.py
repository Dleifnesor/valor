"""Discovery of range VMs (by pool + tags) and the per-VM metadata the engine stamps on them.

VALOR keeps no database of its VMs: the cluster is the source of truth. Every VM it creates is

  - in VALOR's range pool, tagged `valor` and `valor-range-<range>`, and
  - carries a JSON metadata block in its Proxmox notes (description): range, host, spec hash, hardware and
    converge fingerprints (see plan.py). People see a readable line above it in the Proxmox UI.

Templates are found the same way, by tag (`valor-template` + `os-<name>`), never by a fixed VMID.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field

from .pve import PVE

META_RE = re.compile(r"<!--valor (\{.*?\}) -->", re.S)


def tag_list(tags: str | None) -> list[str]:
    """Proxmox returns tags as one string; separators vary between versions (';', ',' or ' ')."""
    return [tag for tag in re.split(r"[;, ]", tags or "") if tag]


def range_tag(name: str) -> str:
    return f"valor-range-{name}"


@dataclass
class VMState:
    """A VALOR VM as found on the cluster."""
    vmid: int
    name: str
    status: str
    tags: list[str]
    meta: dict = field(default_factory=dict)        # the metadata block from its notes ({} if none)
    config: dict = field(default_factory=dict)      # its Proxmox config (only loaded when asked for)

    @property
    def host(self) -> str | None:
        return self.meta.get("host")


def parse_meta(description: str | None) -> dict:
    match = META_RE.search(description or "")
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:            # someone edited the notes by hand
        return {}


def render_description(meta: dict, spec_short: str) -> str:
    """The VM's Proxmox notes: a line for people, then the metadata block (an HTML comment, hidden in the UI)."""
    human = (f"VALOR-managed VM - do not edit by hand; change the range spec and re-apply.\n\n"
             f"range: {meta.get('range')} | host: {meta.get('host')} | spec: {spec_short} | "
             f"converged: {'yes' if meta.get('conv') else 'no'} | applied: {meta.get('applied', '-')}\n\n")
    return human + "<!--valor " + json.dumps(meta, sort_keys=True) + " -->"


def range_vms(pve: PVE, range_name: str | None = None, with_config: bool = True) -> list[VMState]:
    """VALOR's VMs (all ranges, or one), sorted by VMID. with_config=False skips one API call per VM when only
    names, tags and status are needed."""
    out = []
    for resource in pve.resources():
        if resource.get("template") or resource.get("type") != "qemu":
            continue
        tags = tag_list(resource.get("tags"))
        if "valor" not in tags or resource.get("pool") != pve.cfg.pool:
            continue
        if range_name and range_tag(range_name) not in tags:
            continue
        vm = VMState(int(resource["vmid"]), resource.get("name", ""), resource.get("status", "unknown"), tags)
        if with_config:
            vm.config = pve.vm_config(vm.vmid)
            vm.meta = parse_meta(vm.config.get("description"))
        out.append(vm)
    return sorted(out, key=lambda vm: vm.vmid)


def ranges_overview(pve: PVE) -> dict[str, dict]:
    """Per range: VM count, how many run, spec version and the hosts - the range list of the UI and CLI."""
    ranges: dict[str, dict] = {}
    for vm in range_vms(pve):
        tagged = next((tag[len("valor-range-"):] for tag in vm.tags if tag.startswith("valor-range-")), "?")
        name = vm.meta.get("range") or tagged
        entry = ranges.setdefault(name, {"vms": 0, "running": 0, "spec": vm.meta.get("spec", "")[:12], "hosts": []})
        entry["vms"] += 1
        entry["running"] += vm.status == "running"
        entry["hosts"].append({"host": vm.host, "vmid": vm.vmid, "status": vm.status,
                               "converged": bool(vm.meta.get("conv"))})
    return ranges


def vlans_in_use(pve: PVE) -> dict[int, str]:
    """VLAN tags used on the segment bridge by VALOR VMs, mapped to their range."""
    used: dict[int, str] = {}
    for vm in range_vms(pve):
        for key, value in vm.config.items():
            if re.fullmatch(r"net\d+", key) and f"bridge={pve.cfg.segment_bridge}" in value:
                match = re.search(r"tag=(\d+)", value)
                if match:
                    used[int(match.group(1))] = vm.meta.get("range", "?")
    return used


def os_tag(os_name: str) -> str:
    return "os-" + os_name.replace(".", "-")


def templates(pve: PVE, catalog: dict) -> dict[str, dict]:
    """Templates by OS, found by tag in the template pool (never by fixed VMID).

    A template on the range node wins over one elsewhere (linked clones need the same storage); among equals the
    newest (highest VMID) wins.
    """
    visible = [res for res in pve.resources() if res.get("template") and res.get("type") == "qemu"]
    out = {}
    for os_name, entry in catalog.items():
        # Without Pool.Audit the API omits `pool`; the token only sees templates its ACLs allow, so a hidden
        # pool is accepted and a visible one must be VALOR's template pool.
        found = [res for res in visible
                 if "valor-template" in tag_list(res.get("tags")) and os_tag(os_name) in tag_list(res.get("tags"))
                 and res.get("pool") in (None, "", pve.cfg.template_pool)]
        found.sort(key=lambda res: (res.get("node") == pve.cfg.node, int(res["vmid"])), reverse=True)
        vmid = int(found[0]["vmid"]) if found else None
        out[os_name] = {"vmid": vmid, "present": vmid is not None, "family": entry.get("family"),
                        "description": entry.get("description", "")}
    return out


def template_ids(pve: PVE, catalog: dict | None = None) -> dict[str, int]:
    found = templates(pve, catalog if catalog is not None else load_catalog(pve.cfg))
    return {os_name: template["vmid"] for os_name, template in found.items() if template["present"]}


def load_catalog(cfg) -> dict:
    """templates/catalog.toml: every OS VALOR knows (family, defaults, installer ISO, ...)."""
    with cfg.catalog_file.open("rb") as fh:
        return tomllib.load(fh)


def cluster_info(pve: PVE) -> dict:
    """What an author (or the AI builder) needs to know about the cluster to write a spec that fits it."""
    cfg = pve.cfg
    node = pve.node_status()
    storage = pve.storage_status()
    bridges = {iface["iface"]: iface for iface in pve.network() if iface.get("type") == "bridge"}
    segment_bridge = bridges.get(cfg.segment_bridge, {})
    return {
        "node": cfg.node,
        "cpu_threads": node.get("cpuinfo", {}).get("cpus"),
        "memory_total_mib": node["memory"]["total"] // 2**20,
        "memory_free_mib": node["memory"]["free"] // 2**20,
        "storage": {"id": cfg.storage, "available_gib": round(storage.get("avail", 0) / 2**30, 1)},
        "segment_bridge": {"name": cfg.segment_bridge, "present": bool(segment_bridge),
                           "vlan_aware": bool(segment_bridge.get("bridge_vlan_aware")),
                           "usable_vlans": f"{cfg.vlan_min}-{cfg.vlan_max}"},
        "uplink_bridge": {"name": cfg.uplink_bridge, "present": cfg.uplink_bridge in bridges},
        "reserved_networks": list(cfg.reserved_networks),
        "templates": templates(pve, load_catalog(cfg)),
        "default_os": cfg.default_os,
        "vlans_in_use": {str(vlan): owner for vlan, owner in sorted(vlans_in_use(pve).items())},
        "ranges": ranges_overview(pve),
        "roles": sorted(path.name for path in cfg.roles_dir.iterdir() if (path / "role.sh").is_file()),
        "baselines": sorted(path.stem for path in cfg.baselines_dir.glob("*.yaml")),
    }
