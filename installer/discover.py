# *sniffs around the whole Proxmox node* OwO installer/discover.py DISCOVERS the node and cluster! ^w^
# It only READS: Proxmox version, nodes, storages, bridges, networks, DNS, VMs, pools, users, roles... Every fact VALOR
# needs comes from here, so nothing about a particular cluster is ever hard-coded. Parsing is kept apart from running
# commands, so tests can feed it captured output *wags tail*
"""Read-only discovery of the Proxmox VE node and cluster the installer runs on.

Every fact VALOR needs comes from here, so nothing about a particular cluster is ever hard-coded.
Parsing is split from the commands so it can be tested with captured output.
"""

# Lazy type hints UwU
from __future__ import annotations

# ipaddress, json, re, socket (the host name), dataclass/field, Path >w<
import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path

# RECORD_DIR (to find an existing record) and the command helpers OwO
from . import RECORD_DIR
from .sh import pvesh, run

# Storage types whose disks support linked clones (qcow2 on file storages, thin/snapshot-capable block storages).
# FILE_TYPES: file-based storage types (they keep disks as files, e.g. qcow2) ^w^
LINKED_CLONE_TYPES = {"zfspool", "lvmthin", "rbd", "dir", "nfs", "cifs", "glusterfs", "btrfs"}
FILE_TYPES = {"dir", "nfs", "cifs", "glusterfs", "btrfs", "cephfs"}


# Storage: one storage: id, type, content kinds, free/total bytes, shared, active, its path; plus handy questions:
# does it hold VM disks? support linked clones? is it file-based? how many GiB are free? UwU
@dataclass
class Storage:
    id: str
    type: str
    content: set[str]
    avail: int
    total: int
    shared: bool
    active: bool
    path: str | None = None

    @property
    def images(self) -> bool:
        return "images" in self.content

    @property
    def linked_clones(self) -> bool:
        return self.type in LINKED_CLONE_TYPES

    @property
    def file_based(self) -> bool:
        return self.type in FILE_TYPES

    @property
    def avail_gib(self) -> float:
        return self.avail / 2**30


# Bridge: one network bridge: name, VLAN-aware, its ports, address, gateway, active, comment >w<
@dataclass
class Bridge:
    name: str
    vlan_aware: bool
    ports: str
    cidr: str | None
    gateway: str | None
    active: bool
    comments: str = ""


# Facts: everything discovered, in one object OwO
#   version, node, its IP, cluster and nodes; CPU threads and flags; memory; storages and bridges
#   the management bridge/VLAN/network/gateway (where the node's default route goes)
#   networks the node is on (VALOR reserves them), DNS servers and search domain
#   used VM ids and their pools; pools, users, roles and every known privilege
#   existing VALOR templates, pending network changes, internet reachability, an existing install record, and every
#   interface name
@dataclass
class Facts:
    pve_version: str
    node: str
    node_ip: str
    cluster_name: str | None
    nodes: list[dict]
    cpu_threads: int
    cpu_flags: set[str]
    mem_total: int
    mem_free: int
    storages: list[Storage]
    bridges: list[Bridge]
    mgmt_bridge: str
    mgmt_vlan: int
    mgmt_network: str
    mgmt_gateway: str
    networks: list[str]
    nameservers: list[str]
    search_domain: str
    used_vmids: set[int]
    vm_pools: dict[int, str]
    pools: set[str]
    users: dict[str, str]
    roles: dict[str, set[str]]
    privileges: set[str]
    valor_templates: list[dict]
    pending_network_changes: bool
    internet: tuple[bool, str] = (False, "not checked")
    record: dict | None = None
    interfaces: set[str] = field(default_factory=set)      # every network interface name on the node

    # version_tuple: "9.0.3" -> (9, 0). has_aes: does the CPU have AES-NI? ^w^
    @property
    def version_tuple(self) -> tuple[int, int]:
        m = re.match(r"(\d+)\.(\d+)", self.pve_version)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    @property
    def has_aes(self) -> bool:
        return "aes" in self.cpu_flags

    # storage() / bridge(): find one by name UwU
    def storage(self, sid: str) -> Storage | None:
        return next((s for s in self.storages if s.id == sid), None)

    def bridge(self, name: str) -> Bridge | None:
        return next((b for b in self.bridges if b.name == name), None)


# ---- Parsing (pure functions, testable) >w<
# ---------------------------------------------------------------------- parsing (pure)
# parse_storages(): combine the node's storage list with the storage configs (enabled ones only), sorted by id OwO
def parse_storages(node_list: list[dict], configs: dict[str, dict]) -> list[Storage]:
    out = []
    for s in node_list:
        if not s.get("enabled", 1):
            continue
        cfg = configs.get(s["storage"], {})
        out.append(Storage(id=s["storage"], type=s.get("type", cfg.get("type", "")),
                           content=set(filter(None, str(s.get("content", cfg.get("content", ""))).split(","))),
                           avail=int(s.get("avail") or 0), total=int(s.get("total") or 0),
                           shared=bool(s.get("shared")), active=bool(s.get("active")), path=cfg.get("path")))
    return sorted(out, key=lambda s: s.id)


# parse_bridges(): Linux and OVS bridges, short names first ^w^
def parse_bridges(net: list[dict]) -> list[Bridge]:
    out = []
    for i in net:
        if i.get("type") not in ("bridge", "OVSBridge"):
            continue
        out.append(Bridge(name=i["iface"], vlan_aware=bool(i.get("bridge_vlan_aware")), ports=i.get("bridge_ports") or "",
                          cidr=i.get("cidr"), gateway=i.get("gateway"), active=bool(i.get("active")),
                          comments=(i.get("comments") or "").strip()))
    return sorted(out, key=lambda b: (len(b.name), b.name))


# parse_mgmt(): where the node's default route goes: the bridge (a VLAN interface like vmbr0.20 means bridge vmbr0,
# VLAN 20), the node's IP, its network and the gateway UwU
def parse_mgmt(default_routes: list[dict], links: list[dict], addrs: list[dict]) -> tuple[str, int, str, str, str]:
    """(bridge, vlan, node_ip, network, gateway) from `ip -j route show default`, `ip -j -d link`, `ip -j -4 addr`."""
    if not default_routes:
        raise RuntimeError("this node has no default route; VALOR needs one to reach the internet")
    route = default_routes[0]
    dev, gateway = route["dev"], route.get("gateway", "")
    bridge, vlan = dev, 0
    link = next((link for link in links if link.get("ifname") == dev), {})
    info = link.get("linkinfo", {})
    if info.get("info_kind") == "vlan":                       # e.g. vmbr0.20 -> bridge vmbr0, VLAN 20
        vlan = int(info.get("info_data", {}).get("id", 0))
        bridge = link.get("link") or dev.split(".")[0]
    addr = next((a for a in addrs if a.get("ifname") == dev), None)
    ai = next((x for x in (addr or {}).get("addr_info", []) if x.get("family") == "inet"), None)
    if not ai:
        raise RuntimeError(f"no IPv4 address on {dev}")
    iface = ipaddress.IPv4Interface(f"{ai['local']}/{ai['prefixlen']}")
    return bridge, vlan, str(iface.ip), str(iface.network), gateway


# parse_networks(): every network the node is on or routes to, merged (VALOR keeps ranges away from them) >w<
def parse_networks(addrs: list[dict], routes: list[dict]) -> list[str]:
    """IPv4 networks this node is attached to or routes to (except default/loopback/link-local): VALOR reserves
    them, so no range segment overlaps them and no range can reach them."""
    nets: set[ipaddress.IPv4Network] = set()
    for a in addrs:
        if a.get("ifname") == "lo":
            continue
        for x in a.get("addr_info", []):
            if x.get("family") == "inet":
                nets.add(ipaddress.IPv4Interface(f"{x['local']}/{x['prefixlen']}").network)
    for r in routes:
        dst = r.get("dst")
        if dst and dst != "default":
            try:
                nets.add(ipaddress.IPv4Network(dst if "/" in dst else f"{dst}/32", strict=False))
            except ValueError:
                pass
    keep = [n for n in nets if not (n.is_loopback or n.is_link_local)]
    collapsed = ipaddress.collapse_addresses(sorted(keep))
    return [str(n) for n in collapsed]


# parse_resolv(): DNS servers (not 127.x local resolvers) and the search domain from resolv.conf OwO
def parse_resolv(text: str) -> tuple[list[str], str]:
    ns, search = [], ""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver" and not parts[1].startswith("127."):
            ns.append(parts[1])
        elif len(parts) >= 2 and parts[0] in ("search", "domain") and not search:
            search = parts[1]
    return ns, search


# parse_cpu_flags(): the CPU feature flags from /proc/cpuinfo ^w^
def parse_cpu_flags(cpuinfo: str) -> set[str]:
    for line in cpuinfo.splitlines():
        if line.startswith("flags"):
            return set(line.split(":", 1)[1].split())
    return set()


# ---- Collecting (runs commands) UwU
# ---------------------------------------------------------------------- collection
# _ipj(): run `ip -j ...` and parse its JSON >w<
def _ipj(*args: str) -> list[dict]:
    return json.loads(run(["ip", "-j", *args]).stdout or "[]")


# check_internet(): can the node reach Ubuntu's cloud images AND PyPI? (The VALOR VM installs Python packages from
# PyPI) OwO
def check_internet() -> tuple[bool, str]:
    res = run(["curl", "-fsSI", "--max-time", "20", "-o", "/dev/null", "-w", "%{http_code}",
               "https://cloud-images.ubuntu.com/"], check=False)
    if res.returncode == 0:
        res2 = run(["curl", "-fsSI", "--max-time", "20", "-o", "/dev/null", "https://pypi.org/simple/"], check=False)
        if res2.returncode == 0:
            return True, "cloud-images.ubuntu.com and pypi.org reachable"
        return False, "pypi.org not reachable (the VALOR VM installs its Python packages from there)"
    return False, f"cloud-images.ubuntu.com not reachable (curl exit {res.returncode})"


# collect(): ask the node everything ^w^
def collect(instance: str, check_net: bool = True) -> Facts:
    """Ask this node (pvesh, ip, /etc and /proc) everything the installer needs to know."""
    # Version and cluster status: this node, the cluster's name, every node UwU
    version = pvesh("get", "/version")
    status = pvesh("get", "/cluster/status")
    this_node = next((entry for entry in status if entry.get("type") == "node" and entry.get("local")), None)
    node = this_node["name"] if this_node else socket.gethostname().split(".")[0]
    cluster = next((entry.get("name") for entry in status if entry.get("type") == "cluster"), None)
    nodes = [{"name": entry["name"], "ip": entry.get("ip"), "online": bool(entry.get("online"))}
             for entry in status if entry.get("type") == "node"]
    # Node status, storages, bridges, the management network, other networks, DNS >w<
    node_status = pvesh("get", f"/nodes/{node}/status")
    configs = {config["storage"]: config for config in pvesh("get", "/storage")}
    storages = parse_storages(pvesh("get", f"/nodes/{node}/storage"), configs)
    bridges = parse_bridges(pvesh("get", f"/nodes/{node}/network"))
    addrs = _ipj("-4", "addr")
    mgmt_bridge, mgmt_vlan, node_ip, mgmt_net, gateway = parse_mgmt(_ipj("route", "show", "default"),
                                                                    _ipj("-d", "link"), addrs)
    networks = parse_networks(addrs, _ipj("-4", "route"))
    resolv = Path("/etc/resolv.conf")
    nameservers, search = parse_resolv(resolv.read_text() if resolv.exists() else "")
    # VMs, roles, and every privilege this Proxmox knows (the Administrator role has them all) OwO
    resources = pvesh("get", "/cluster/resources", type="vm")
    roles = {role["roleid"]: set(filter(None, (role.get("privs") or "").split(",")))
             for role in pvesh("get", "/access/roles")}
    # Administrator holds every privilege this Proxmox version knows: the list VALOR's roles are checked against.
    admin = pvesh("get", "/access/roles/Administrator") or {}
    privileges = set(admin.keys()) if isinstance(admin, dict) else set()
    record_file = RECORD_DIR / f"{instance}.json"
    # Build the Facts ^w^
    facts = Facts(
        pve_version=version.get("version", "0.0"), node=node, node_ip=node_ip, cluster_name=cluster, nodes=nodes,
        cpu_threads=int(node_status.get("cpuinfo", {}).get("cpus", 0)),
        cpu_flags=parse_cpu_flags(Path("/proc/cpuinfo").read_text()),
        mem_total=int(node_status["memory"]["total"]),
        mem_free=int(node_status["memory"].get("available", node_status["memory"]["free"])),
        storages=storages, bridges=bridges, mgmt_bridge=mgmt_bridge, mgmt_vlan=mgmt_vlan, mgmt_network=mgmt_net,
        mgmt_gateway=gateway, networks=networks, nameservers=nameservers, search_domain=search,
        used_vmids={int(vm["vmid"]) for vm in resources},
        vm_pools={int(vm["vmid"]): vm.get("pool", "") for vm in resources},
        pools={pool["poolid"] for pool in pvesh("get", "/pools")},
        users={user["userid"]: user.get("comment", "") for user in pvesh("get", "/access/users")},
        roles=roles, privileges=privileges,
        valor_templates=[vm for vm in resources
                         if vm.get("template") and "valor-template" in re.split(r"[;, ]", vm.get("tags") or "")],
        pending_network_changes=Path("/etc/network/interfaces.new").exists(),
        record=json.loads(record_file.read_text()) if record_file.exists() else None,
    )
    # Every interface name, and (unless skipped) the internet check UwU *finishes the sniff tour*
    facts.interfaces = {link["ifname"] for link in _ipj("link")}
    if check_net:
        facts.internet = check_internet()
    return facts
