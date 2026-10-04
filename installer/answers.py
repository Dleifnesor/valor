"""Install settings: defaults derived from the discovered facts, interactive editing, validation, answers files.

An answers file (TOML) holds any subset of the settings; everything missing gets the detected default. The
installer writes the final settings into its install record, and `--save-answers FILE` exports them so the same
install can be repeated exactly (on this cluster or, with the node-specific values edited, on another).
"""

from __future__ import annotations

import ipaddress
import re
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import ui
from .discover import Facts

ID_RE = re.compile(r"^[a-z][a-z0-9]{1,11}$")
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{1,63}$")
BLOCK = 1000           # VMIDs per installation: ranges, templates and the VALOR VM itself

SECTIONS = {
    "valor": ["id", "instance_name"],
    "vm": ["vm_storage", "vm_cores", "vm_memory", "vm_disk", "vm_bridge", "vm_vlan", "vm_ip", "vm_gateway", "vm_dns",
           "vm_hostname", "vm_domain"],
    "ranges": ["range_storage", "range_bridge", "uplink", "vmid_start", "vlan_min", "vlan_max", "reserved_networks",
               "nameservers", "internet_probe"],
    "templates": ["templates", "snippets_storage"],
    "isos": ["iso_storage", "iso_storages"],
    "web": ["allowed_networks", "tls", "tls_cert", "tls_key", "admin_user"],
}


@dataclass
class Answers:
    id: str = "valor"
    instance_name: str = "VALOR"
    vm_storage: str = ""
    vm_cores: int = 2
    vm_memory: int = 4096
    vm_disk: int = 32
    vm_bridge: str = ""
    vm_vlan: int = 0
    vm_ip: str = "dhcp"                 # "dhcp" or a CIDR such as 192.168.1.50/24
    vm_gateway: str = ""
    vm_dns: list[str] = field(default_factory=list)
    vm_hostname: str = "valor"
    vm_domain: str = ""
    range_storage: str = ""
    range_bridge: str = "new"           # "new" creates the first free vmbrN (N >= 100); or an existing bridge
    uplink: str = "lan-dhcp"
    vmid_start: int = 0
    vlan_min: int = 100
    vlan_max: int = 3999
    reserved_networks: list[str] = field(default_factory=list)
    nameservers: list[str] = field(default_factory=lambda: ["1.1.1.1", "9.9.9.9"])
    internet_probe: str = "1.1.1.1:443"
    templates: list[str] = field(default_factory=lambda: ["ubuntu-24.04"])
    snippets_storage: str = ""
    iso_storage: str = ""               # ISO library: downloads and uploads go here
    iso_storages: list[str] = field(default_factory=list)   # ISO storages VALOR may list
    allowed_networks: list[str] = field(default_factory=list)
    tls: str = "valor-ca"               # valor-ca | own
    tls_cert: str = ""
    tls_key: str = ""
    admin_user: str = "admin"

    # ------------------------------------------------------------------ derived
    @property
    def pool_ranges(self) -> str:
        return f"{self.id}-ranges"

    @property
    def pool_templates(self) -> str:
        return f"{self.id}-templates"

    @property
    def pool_system(self) -> str:
        return f"{self.id}-system"

    @property
    def user(self) -> str:
        return f"{self.id}@pve"

    @property
    def vmid_vm(self) -> int:
        return self.vmid_start + BLOCK - 1

    @property
    def vmid_ranges(self) -> tuple[int, int]:
        return self.vmid_start, self.vmid_start + 899

    @property
    def vmid_templates(self) -> tuple[int, int]:
        return self.vmid_start + 900, self.vmid_start + 989

    def to_toml(self) -> str:
        lines = ["# VALOR answers file. Use it with: ./install.sh --answers <this file>",
                 "# Every key is optional; missing keys get the default the installer detects.", ""]
        data = asdict(self)
        for section, keys in SECTIONS.items():
            lines.append(f"[{section}]")
            for k in keys:
                lines.append(f"{k} = {_toml_value(data[k])}")
            lines.append("")
        return "\n".join(lines)


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def load_file(path: str | Path) -> dict:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    flat = {}
    known = {f.name for f in fields(Answers)}
    for section, values in raw.items():
        if not isinstance(values, dict):
            raise ValueError(f"answers file: [{section}] must be a table")
        for k, v in values.items():
            if k not in known:
                raise ValueError(f"answers file: unknown setting '{k}' in [{section}]")
            flat[k] = v
    return flat


# ---------------------------------------------------------------------- defaults
def first_free_bridge(facts: Facts) -> str:
    names = {b.name for b in facts.bridges} | facts.interfaces
    n = 100
    while f"vmbr{n}" in names:
        n += 1
    return f"vmbr{n}"


def free_vmid_block(facts: Facts, start: int = 4000) -> int:
    s = start
    while any(s <= v < s + BLOCK for v in facts.used_vmids):
        s += BLOCK
    return s


def default_storage(facts: Facts) -> str:
    cands = [s for s in facts.storages if s.images and s.active and s.linked_clones]
    if not cands:
        cands = [s for s in facts.storages if s.images and s.active]
    if not cands:
        return ""
    named = [s for s in cands if "valor" in s.id]         # a storage made for VALOR wins
    return max(named or cands, key=lambda s: s.avail).id


def default_iso_storages(facts: Facts) -> tuple[str, list[str]]:
    isos = [s for s in facts.storages if "iso" in s.content and s.active]
    if not isos:
        return "", []
    target = max(isos, key=lambda s: (s.shared, s.avail))       # shared storage first: every node sees the media
    return target.id, [s.id for s in isos]


def default_snippets(facts: Facts) -> str:
    have = [s for s in facts.storages if "snippets" in s.content and s.active and s.file_based and s.path]
    if have:
        return sorted(have, key=lambda s: (s.shared, s.id != "local"))[0].id
    if facts.storage("local"):
        return "local"
    dirs = [s for s in facts.storages if s.type == "dir" and s.active]
    return dirs[0].id if dirs else ""


def defaults(facts: Facts, given: dict | None = None) -> Answers:
    rec = (facts.record or {}).get("answers", {})
    a = Answers(**{**rec, **(given or {})})
    if not a.vm_storage or not a.range_storage:
        st = default_storage(facts)
        a.vm_storage = a.vm_storage or st
        a.range_storage = a.range_storage or st
    a.vm_bridge = a.vm_bridge or facts.mgmt_bridge
    if not given or "vm_vlan" not in given:
        a.vm_vlan = a.vm_vlan or facts.mgmt_vlan
    if not a.vmid_start:
        a.vmid_start = free_vmid_block(facts)
    if not a.reserved_networks:
        a.reserved_networks = list(facts.networks)
    if not a.allowed_networks:
        a.allowed_networks = [facts.mgmt_network]
    if not a.snippets_storage:
        a.snippets_storage = default_snippets(facts)
    if not a.iso_storage or not a.iso_storages:
        target, readable = default_iso_storages(facts)
        a.iso_storage = a.iso_storage or target
        a.iso_storages = a.iso_storages or readable
    if a.iso_storage and a.iso_storage not in a.iso_storages:
        a.iso_storages = [a.iso_storage, *a.iso_storages]
    if not a.vm_dns:
        a.vm_dns = list(facts.nameservers)
    if not a.vm_domain:
        a.vm_domain = facts.search_domain
    if a.instance_name == "VALOR" and facts.cluster_name and facts.cluster_name.lower() != "valor":
        a.instance_name = f"VALOR {facts.cluster_name}"
    return a


def resolve_bridge(a: Answers, facts: Facts) -> tuple[str, bool]:
    """(bridge name, must be created)."""
    rec = (facts.record or {}).get("objects", {}).get("bridge")
    if a.range_bridge == "new":
        if rec and facts.bridge(rec["name"]):
            return rec["name"], False                     # created by an earlier run of this installation
        return first_free_bridge(facts), True
    return a.range_bridge, False


# ---------------------------------------------------------------------- validation
def _cidr(v: str) -> bool:
    try:
        ipaddress.ip_network(v, strict=False)
        return True
    except ValueError:
        return False


def problems(a: Answers, facts: Facts, catalog: dict) -> list[str]:
    out: list[str] = []
    if not ID_RE.match(a.id):
        out.append("id: 2-12 lowercase letters/digits, starting with a letter")
    for key in ("vm_storage", "range_storage"):
        s = facts.storage(getattr(a, key))
        if not s:
            out.append(f"{key}: storage '{getattr(a, key)}' does not exist on {facts.node}")
        elif not s.images:
            out.append(f"{key}: storage '{s.id}' cannot hold VM disks (content 'images' missing)")
        elif not s.active:
            out.append(f"{key}: storage '{s.id}' is not active")
    rs = facts.storage(a.range_storage)
    if rs and rs.images and not rs.linked_clones:
        out.append(f"range_storage: '{rs.id}' ({rs.type}) does not support linked clones; pick ZFS, LVM-thin, Ceph "
                   "RBD or a directory/NFS storage")
    if not facts.bridge(a.vm_bridge):
        out.append(f"vm_bridge: bridge '{a.vm_bridge}' does not exist on {facts.node}")
    if a.range_bridge != "new":
        b = facts.bridge(a.range_bridge)
        if not b:
            out.append(f"range_bridge: bridge '{a.range_bridge}' does not exist")
        else:
            if not b.vlan_aware:
                out.append(f"range_bridge: '{b.name}' is not VLAN-aware")
            if b.ports and b.ports != "none":
                out.append(f"range_bridge: '{b.name}' has physical ports ({b.ports}); range traffic must not leave the node")
            if b.name == a.vm_bridge:
                out.append("range_bridge: must differ from the management bridge")
    if not 1 <= a.vm_cores <= 64:
        out.append("vm_cores: 1-64")
    if not 2048 <= a.vm_memory <= 65536:
        out.append("vm_memory: 2048-65536 MiB")
    if not 16 <= a.vm_disk <= 2048:
        out.append("vm_disk: 16-2048 GiB")
    if not 0 <= a.vm_vlan <= 4094:
        out.append("vm_vlan: 0 (untagged) or 1-4094")
    if a.vm_ip != "dhcp":
        try:
            iface = ipaddress.IPv4Interface(a.vm_ip)
            if iface.network.prefixlen == 32:
                raise ValueError
            if not a.vm_gateway or ipaddress.IPv4Address(a.vm_gateway) not in iface.network:
                out.append("vm_gateway: required with a static IP, inside the same network")
        except ValueError:
            out.append("vm_ip: 'dhcp' or an address with prefix length, e.g. 192.168.1.50/24")
    if not HOST_RE.match(a.vm_hostname):
        out.append("vm_hostname: a DNS label (lowercase letters, digits, dashes)")
    if a.uplink not in ("lan-dhcp",):
        out.append("uplink: only 'lan-dhcp' is available in this version (NAT through the VALOR VM: issue #15)")
    lo, hi = a.vmid_start, a.vmid_start + BLOCK - 1
    if a.vmid_start < 100 or a.vmid_start % 100:
        out.append("vmid_start: a multiple of 100, at least 100")
    else:
        ours = (a.pool_ranges, a.pool_templates, a.pool_system)      # a re-run finds its own VMs in the block
        taken = sorted(v for v in facts.used_vmids if lo <= v <= hi and facts.vm_pools.get(v) not in ours)
        if taken:
            out.append(f"vmid_start: VMIDs {lo}-{hi} must be free, but {taken[:5]} exist")
    if not (2 <= a.vlan_min <= a.vlan_max <= 4094):
        out.append("vlan_min/vlan_max: 2 <= min <= max <= 4094")
    for key in ("reserved_networks", "allowed_networks"):
        bad = [n for n in getattr(a, key) if not _cidr(n)]
        if bad:
            out.append(f"{key}: not networks: {bad}")
    if not a.allowed_networks:
        out.append("allowed_networks: at least one network must be allowed to reach the web UI")
    for ns in a.nameservers + a.vm_dns:
        try:
            ipaddress.ip_address(ns)
        except ValueError:
            out.append(f"nameserver '{ns}' is not an IP address")
    if not re.fullmatch(r"[0-9.]+:\d{1,5}", a.internet_probe):
        out.append("internet_probe: public-ip:port, e.g. 1.1.1.1:443")
    unknown = [t for t in a.templates if t not in catalog]
    if unknown:
        out.append(f"templates: unknown {unknown}; choose from {sorted(catalog)}")
    if "ubuntu-24.04" not in a.templates and not any(_template_on_node(facts, a, "ubuntu-24.04")):
        out.append("templates: ubuntu-24.04 is required (the VALOR VM and the default OS use it)")
    sn = facts.storage(a.snippets_storage)
    if not sn or not sn.file_based or not sn.path:
        out.append(f"snippets_storage: '{a.snippets_storage}' must be a directory-type storage on {facts.node}")
    if a.iso_storage:
        st = facts.storage(a.iso_storage)
        if not st or "iso" not in st.content:
            out.append(f"iso_storage: '{a.iso_storage}' must be a storage with 'iso' content on {facts.node}")
    for sid in a.iso_storages:
        if not facts.storage(sid):
            out.append(f"iso_storages: '{sid}' does not exist on {facts.node}")
    if a.tls not in ("valor-ca", "own"):
        out.append("tls: 'valor-ca' or 'own' (ACME DNS-01 is planned: issue #7)")
    if a.tls == "own":
        for k in ("tls_cert", "tls_key"):
            if not getattr(a, k) or not Path(getattr(a, k)).is_file():
                out.append(f"{k}: PEM file required with tls = own")
    if not USER_RE.match(a.admin_user):
        out.append("admin_user: 2-64 letters, digits, dots, dashes, underscores or @")
    return out


def _template_on_node(facts: Facts, a: Answers, os_name: str):
    tag = "os-" + os_name.replace(".", "-")
    for t in facts.valor_templates:
        if t.get("node") == facts.node and t.get("pool") == a.pool_templates and tag in (t.get("tags") or ""):
            yield t


# ---------------------------------------------------------------------- interactive editing
def edit(a: Answers, facts: Facts, catalog: dict) -> Answers:
    images = [s for s in facts.storages if s.images and s.active]
    st_opts = [(s.id, f"{s.type}, {s.avail_gib:.0f} GiB free{'' if s.linked_clones else ', no linked clones'}") for s in images]
    br_opts = [(b.name, (b.cidr or "no address") + (f" - {b.comments}" if b.comments else "")) for b in facts.bridges]

    ui.say()
    ui.say(ui.bold("  VALOR VM"))
    a.vm_storage = ui.choose("Storage for the VALOR VM's disk", st_opts, a.vm_storage)
    a.vm_cores = int(ui.ask("vCPUs", str(a.vm_cores), lambda v: None if v.isdigit() else "a number"))
    a.vm_memory = int(ui.ask("Memory (MiB)", str(a.vm_memory), lambda v: None if v.isdigit() else "a number"))
    a.vm_disk = int(ui.ask("Disk (GiB)", str(a.vm_disk), lambda v: None if v.isdigit() else "a number"))
    a.vm_bridge = ui.choose("Network bridge (LAN users reach the web UI here)", br_opts, a.vm_bridge)
    a.vm_vlan = int(ui.ask("VLAN tag on that bridge (0 = none)", str(a.vm_vlan), lambda v: None if v.isdigit() else "a number"))
    a.vm_ip = ui.ask("IP address: 'dhcp' or address/prefix (reserve DHCP addresses in your router)", a.vm_ip)
    if a.vm_ip != "dhcp":
        a.vm_gateway = ui.ask("Gateway", a.vm_gateway or facts.mgmt_gateway)
        a.vm_dns = ui.ask("DNS servers (comma-separated)", ",".join(a.vm_dns)).replace(" ", "").split(",")
    a.vm_hostname = ui.ask("Hostname", a.vm_hostname)

    ui.say()
    ui.say(ui.bold("  Ranges"))
    a.range_storage = ui.choose("Storage for range VMs and templates", st_opts, a.range_storage)
    portless = [(b.name, "existing, VLAN-aware, no physical port") for b in facts.bridges if b.vlan_aware and not b.ports]
    a.range_bridge = ui.choose("Isolated network for range segments",
                               [("new", f"create {first_free_bridge(facts)} (VLAN-aware, no physical port)"), *portless],
                               a.range_bridge)
    a.vmid_start = int(ui.ask(f"First VMID of VALOR's block of {BLOCK}", str(a.vmid_start),
                              lambda v: None if v.isdigit() else "a number"))
    a.reserved_networks = ui.ask("Networks ranges must never use or reach (comma-separated)",
                                 ",".join(a.reserved_networks)).replace(" ", "").split(",")
    ui.say()
    ui.say(ui.bold("  Web UI"))
    a.allowed_networks = ui.ask("Networks allowed to open the web UI: your LAN and VPN subnets (comma-separated)",
                                ",".join(a.allowed_networks)).replace(" ", "").split(",")
    a.tls = ui.choose("Web certificate", [("valor-ca", "VALOR creates its own CA (import its certificate once)"),
                                          ("own", "use your certificate and key (PEM files on this node)")], a.tls)
    if a.tls == "own":
        a.tls_cert = ui.ask("Certificate file (PEM, with chain)", a.tls_cert)
        a.tls_key = ui.ask("Private key file (PEM)", a.tls_key)
    a.admin_user = ui.ask("First admin username", a.admin_user)
    names = sorted(catalog)
    a.templates = ui.ask(f"Templates to build now ({', '.join(names)})", ",".join(a.templates)).replace(" ", "").split(",")
    return a
