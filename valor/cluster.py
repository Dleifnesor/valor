# *sniffs around the whole cluster* OwO valor/cluster.py FINDS VALOR's VMs and templates on Proxmox! ^w^
# VALOR keeps NO database of its VMs: the cluster itself is the truth. Every VM VALOR makes is put in VALOR's pool,
# tagged `valor` + `valor-range-<range>`, and carries a hidden JSON metadata block in its Proxmox notes (which
# range, which host, fingerprints of its spec, hardware and roles). Templates are found by tag too, never by a
# fixed VM id. This file reads all of that back, plus general cluster facts for the UI and the AI *wags tail*
"""Discovery of range VMs (by pool + tags) and the per-VM metadata the engine stamps on them.

VALOR keeps no database of its VMs: the cluster is the source of truth. Every VM it creates is

  - in VALOR's range pool, tagged `valor` and `valor-range-<range>`, and
  - carries a JSON metadata block in its Proxmox notes (description): range, host, spec hash, hardware and
    converge fingerprints (see plan.py). People see a readable line above it in the Proxmox UI.

Templates are found the same way, by tag (`valor-template` + `os-<name>`), never by a fixed VMID.
"""

# Lazy type hints UwU
from __future__ import annotations

# json reads/writes the metadata block; re finds it and splits tags; tomllib reads the OS catalog; dataclass/field
# describe a VM >w<
import json
import re
import tomllib
from dataclasses import dataclass, field

# PVE = the Proxmox API client OwO
from .pve import PVE

# META_RE: finds the metadata block "<!--valor {...} -->" in a VM's notes (an HTML comment, so the Proxmox UI hides
# it); re.S lets the JSON span lines ^w^
META_RE = re.compile(r"<!--valor (\{.*?\}) -->", re.S)


# tag_list: Proxmox gives tags as ONE string, separated by ";" "," or " " depending on the version; split on any
# of them and drop empties UwU
def tag_list(tags: str | None) -> list[str]:
    """Proxmox returns tags as one string; separators vary between versions (';', ',' or ' ')."""
    return [tag for tag in re.split(r"[;, ]", tags or "") if tag]


# range_tag: the tag every VM of a range carries >w<
def range_tag(name: str) -> str:
    return f"valor-range-{name}"


# VMState: one VALOR VM as found on the cluster: id, name, status (running/stopped), tags, its metadata and (when
# loaded) its Proxmox config OwO
@dataclass
class VMState:
    """A VALOR VM as found on the cluster."""
    vmid: int
    name: str
    status: str
    tags: list[str]
    meta: dict = field(default_factory=dict)        # the metadata block from its notes ({} if none)
    config: dict = field(default_factory=dict)      # its Proxmox config (only loaded when asked for)

    # host: which host of the spec this VM is (from its metadata; None for a VM without metadata) ^w^
    @property
    def host(self) -> str | None:
        return self.meta.get("host")


# parse_meta: pull the metadata dict out of a VM's notes ({} when there is none) UwU
def parse_meta(description: str | None) -> dict:
    match = META_RE.search(description or "")
    if not match:
        return {}
    # Broken JSON (someone hand-edited the notes) is treated as "no metadata" rather than crashing >w<
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:            # someone edited the notes by hand
        return {}


# render_description: write a VM's notes: a friendly warning + summary line for people, then the hidden metadata
# (sorted keys, so the same data always gives the same text) OwO
def render_description(meta: dict, spec_short: str) -> str:
    """The VM's Proxmox notes: a line for people, then the metadata block (an HTML comment, hidden in the UI)."""
    human = (f"VALOR-managed VM - do not edit by hand; change the range spec and re-apply.\n\n"
             f"range: {meta.get('range')} | host: {meta.get('host')} | spec: {spec_short} | "
             f"converged: {'yes' if meta.get('conv') else 'no'} | applied: {meta.get('applied', '-')}\n\n")
    return human + "<!--valor " + json.dumps(meta, sort_keys=True) + " -->"


# range_vms: list VALOR's VMs (all, or one range's), sorted by VM id ^w^
def range_vms(pve: PVE, range_name: str | None = None, with_config: bool = True) -> list[VMState]:
    """VALOR's VMs (all ranges, or one), sorted by VMID. with_config=False skips one API call per VM when only
    names, tags and status are needed."""
    out = []
    # Walk every resource on the cluster... UwU
    for resource in pve.resources():
        # ...skipping templates and anything that isn't a QEMU VM (containers, storage, nodes) >w<
        if resource.get("template") or resource.get("type") != "qemu":
            continue
        # SAFETY: only VMs tagged `valor` AND in VALOR's own pool count. A VM someone else made can't be mistaken for one
        # of ours, even with the right tag *guards with fluffy tail* OwO
        tags = tag_list(resource.get("tags"))
        if "valor" not in tags or resource.get("pool") != pve.cfg.pool:
            continue
        # Filter to one range when asked ^w^
        if range_name and range_tag(range_name) not in tags:
            continue
        # Build the VMState; with_config also loads its config (one API call per VM) and reads its metadata UwU
        vm = VMState(int(resource["vmid"]), resource.get("name", ""), resource.get("status", "unknown"), tags)
        if with_config:
            vm.config = pve.vm_config(vm.vmid)
            vm.meta = parse_meta(vm.config.get("description"))
        out.append(vm)
    return sorted(out, key=lambda vm: vm.vmid)


# ranges_overview: a summary per range for the range list: VM count, how many run, the spec version (short hash)
# and each host's id, status and whether it's converged >w<
def ranges_overview(pve: PVE) -> dict[str, dict]:
    """Per range: VM count, how many run, spec version and the hosts - the range list of the UI and CLI."""
    ranges: dict[str, dict] = {}
    for vm in range_vms(pve):
        # The range's name: from the metadata, or from the range tag as a fallback ("?" if neither) OwO
        tagged = next((tag[len("valor-range-"):] for tag in vm.tags if tag.startswith("valor-range-")), "?")
        name = vm.meta.get("range") or tagged
        entry = ranges.setdefault(name, {"vms": 0, "running": 0, "spec": vm.meta.get("spec", "")[:12], "hosts": []})
        entry["vms"] += 1
        # `True` adds as 1, so this counts running VMs ^w^
        entry["running"] += vm.status == "running"
        entry["hosts"].append({"host": vm.host, "vmid": vm.vmid, "status": vm.status,
                               "converged": bool(vm.meta.get("conv"))})
    return ranges


# vlans_in_use: which VLAN tags VALOR VMs use on the segment bridge, and which range uses each UwU
def vlans_in_use(pve: PVE) -> dict[int, str]:
    """VLAN tags used on the segment bridge by VALOR VMs, mapped to their range."""
    used: dict[int, str] = {}
    for vm in range_vms(pve):
        # Look at every network card (net0, net1, ...) attached to the segment bridge and read its "tag=<vlan>" >w<
        for key, value in vm.config.items():
            if re.fullmatch(r"net\d+", key) and f"bridge={pve.cfg.segment_bridge}" in value:
                match = re.search(r"tag=(\d+)", value)
                if match:
                    used[int(match.group(1))] = vm.meta.get("range", "?")
    return used


# os_tag: the template tag for an OS name ("ubuntu-24.04" -> "os-ubuntu-24-04"; dots aren't allowed in tags) OwO
def os_tag(os_name: str) -> str:
    return "os-" + os_name.replace(".", "-")


# templates: for every OS in the catalog, find its template VM (by tags) and whether it exists ^w^
def templates(pve: PVE, catalog: dict) -> dict[str, dict]:
    """Templates by OS, found by tag in the template pool (never by fixed VMID).

    A template on the range node wins over one elsewhere (linked clones need the same storage); among equals the
    newest (highest VMID) wins.
    """
    # Every template VM the token can see UwU
    visible = [res for res in pve.resources() if res.get("template") and res.get("type") == "qemu"]
    out = {}
    for os_name, entry in catalog.items():
        # Without Pool.Audit the API omits `pool`; the token only sees templates its ACLs allow, so a hidden
        # pool is accepted and a visible one must be VALOR's template pool.
        # Matches: tagged valor-template AND os-<name>, in the template pool (or with the pool hidden from us, see the
        # comment above) >w<
        found = [res for res in visible
                 if "valor-template" in tag_list(res.get("tags")) and os_tag(os_name) in tag_list(res.get("tags"))
                 and res.get("pool") in (None, "", pve.cfg.template_pool)]
        # Best first: on the range node beats elsewhere (linked clones need the same storage), then the highest VM id
        # (the newest build). True sorts after False, hence reverse=True OwO
        found.sort(key=lambda res: (res.get("node") == pve.cfg.node, int(res["vmid"])), reverse=True)
        vmid = int(found[0]["vmid"]) if found else None
        # The OS's entry: its template's id (or None), present yes/no, family and description ^w^
        out[os_name] = {"vmid": vmid, "present": vmid is not None, "family": entry.get("family"),
                        "description": entry.get("description", "")}
    return out


# template_ids: just {os: vmid} for templates that exist UwU
def template_ids(pve: PVE, catalog: dict | None = None) -> dict[str, int]:
    found = templates(pve, catalog if catalog is not None else load_catalog(pve.cfg))
    return {os_name: template["vmid"] for os_name, template in found.items() if template["present"]}


# load_catalog: read templates/catalog.toml (every OS VALOR knows how to build) >w<
def load_catalog(cfg) -> dict:
    """templates/catalog.toml: every OS VALOR knows (family, defaults, installer ISO, ...)."""
    with cfg.catalog_file.open("rb") as fh:
        return tomllib.load(fh)


# cluster_info: everything someone (or the AI) needs to write a spec that FITS this cluster OwO
def cluster_info(pve: PVE) -> dict:
    """What an author (or the AI builder) needs to know about the cluster to write a spec that fits it."""
    cfg = pve.cfg
    # Ask Proxmox about the node, the storage and the network; keep only bridges, by name ^w^
    node = pve.node_status()
    storage = pve.storage_status()
    bridges = {iface["iface"]: iface for iface in pve.network() if iface.get("type") == "bridge"}
    segment_bridge = bridges.get(cfg.segment_bridge, {})
    # The facts: CPU threads, memory (bytes -> MiB by dividing by 2**20), free storage in GiB, the segment bridge
    # (present? VLAN-aware? which VLANs VALOR may use), the uplink bridge, reserved networks, templates, the default
    # OS, VLANs in use, the ranges, the roles (folders with a role.sh) and the baselines UwU *happy tail wags*
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
