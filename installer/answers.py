# *fills in the installer's questionnaire* OwO installer/answers.py: the INSTALL SETTINGS! ^w^
# Defaults worked out from the discovered facts, interactive editing, validation, and answers files (TOML with any
# subset of the settings; anything missing gets the detected default). `--save-answers` exports the final settings so
# the same install can be repeated exactly *wags tail*
"""Install settings: defaults derived from the discovered facts, interactive editing, validation, answers files.

An answers file (TOML) holds any subset of the settings; everything missing gets the detected default. The
installer writes the final settings into its install record, and `--save-answers FILE` exports them so the same
install can be repeated exactly (on this cluster or, with the node-specific values edited, on another).
"""

# Lazy type hints UwU
from __future__ import annotations

# ipaddress, re, tomllib, dataclass helpers, Path >w<
import ipaddress
import re
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

# ui (prompts) and the discovered Facts OwO
from . import ui
from .discover import Facts

# Patterns: instance ids (2-12 lowercase letters/digits), host names (DNS labels), admin usernames. BLOCK: each
# installation owns 1000 VM ids (ranges, templates and the VALOR VM) ^w^
ID_RE = re.compile(r"^[a-z][a-z0-9]{1,11}$")
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{1,63}$")
BLOCK = 1000           # VMIDs per installation: ranges, templates and the VALOR VM itself

# SECTIONS: how settings are grouped in an answers file UwU
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


# Answers: every install setting with its default >w<
#   id / instance_name                          the instance id (prefixes everything) and the UI's name
#   vm_*                                        the VALOR VM: storage, size, bridge, VLAN, IP (dhcp or CIDR), gateway, DNS,
#                                               host name, domain
#   range_storage / range_bridge / uplink       where range disks go, the isolated bridge ("new" = create one), uplink
#   vmid_start / vlan_min / vlan_max            the id block and the VLAN range for ranges
#   reserved_networks / nameservers / internet_probe   networks ranges must avoid, range DNS, the egress test target
#   templates / snippets_storage                OSes to build now, where the cloud-init snippet goes
#   iso_storage / iso_storages                  the ISO library
#   allowed_networks / tls / tls_cert / tls_key / admin_user   who may reach the UI, the certificate, the first admin
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

    # Names derived from the id: the three pools, the API user, and the id sub-blocks (ranges x000-x899, templates
    # x900-x989, the VALOR VM x999) OwO
    # ---- Derived names ----

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

    # to_toml(): the settings as an answers file (Python can read TOML but not write it, so it's written by hand) ^w^
    def to_toml(self) -> str:
        """The settings as an answers file (the standard library reads TOML but cannot write it)."""
        lines = ["# VALOR answers file. Use it with: ./install.sh --answers <this file>",
                 "# Every key is optional; missing keys get the default the installer detects.", ""]
        data = asdict(self)
        for section, keys in SECTIONS.items():
            lines.append(f"[{section}]")
            for key in keys:
                lines.append(f"{key} = {_toml_value(data[key])}")
            lines.append("")
        return "\n".join(lines)


# _toml_value(): one value in TOML syntax (strings escaped) UwU
def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


# load_file(): read an answers file into a flat dict; sections must be tables, and an UNKNOWN setting is an error (a
# typo would otherwise be silently replaced by a default) >w<
def load_file(path: str | Path) -> dict:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    flat = {}
    known = {f.name for f in fields(Answers)}
    for section, values in raw.items():
        if not isinstance(values, dict):
            raise ValueError(f"answers file: [{section}] must be a table")
        for key, value in values.items():
            if key not in known:            # a typo would otherwise be silently replaced by a default
                raise ValueError(f"answers file: unknown setting '{key}' in [{section}]")
            flat[key] = value
    return flat


# ---- Defaults OwO
# ---- Defaults --------------------------------------------------------------------------------------------------

# first_free_bridge(): vmbr100, vmbr101... the first name no bridge or interface uses ^w^
def first_free_bridge(facts: Facts) -> str:
    """vmbr100, vmbr101, ...: the first name no bridge or interface uses yet."""
    names = {bridge.name for bridge in facts.bridges} | facts.interfaces
    number = 100
    while f"vmbr{number}" in names:
        number += 1
    return f"vmbr{number}"


# free_vmid_block(): the first block of 1000 ids (from 4000 up) that no VM uses UwU
def free_vmid_block(facts: Facts, start: int = 4000) -> int:
    """The first block of BLOCK VMIDs (from `start`, in steps of BLOCK) that no VM uses."""
    block = start
    while any(block <= vmid < block + BLOCK for vmid in facts.used_vmids):
        block += BLOCK
    return block


# default_storage(): the active VM storage with the most free space that supports linked clones (one with "valor" in
# its name wins) >w<
def default_storage(facts: Facts) -> str:
    """The storage with the most free space that can hold linked clones (any VM storage if none can)."""
    candidates = [s for s in facts.storages if s.images and s.active and s.linked_clones]
    if not candidates:
        candidates = [s for s in facts.storages if s.images and s.active]
    if not candidates:
        return ""
    named = [s for s in candidates if "valor" in s.id]         # a storage made for VALOR wins
    return max(named or candidates, key=lambda s: s.avail).id


# default_iso_storages(): the ISO storage to download to (shared first, then the most free space) and every ISO
# storage OwO
def default_iso_storages(facts: Facts) -> tuple[str, list[str]]:
    isos = [s for s in facts.storages if "iso" in s.content and s.active]
    if not isos:
        return "", []
    target = max(isos, key=lambda s: (s.shared, s.avail))       # shared storage first: every node sees the media
    return target.id, [s.id for s in isos]


# default_snippets(): a file-based storage that already holds snippets, else "local", else any directory storage ^w^
def default_snippets(facts: Facts) -> str:
    have = [s for s in facts.storages if "snippets" in s.content and s.active and s.file_based and s.path]
    if have:
        return sorted(have, key=lambda s: (s.shared, s.id != "local"))[0].id
    if facts.storage("local"):
        return "local"
    dirs = [s for s in facts.storages if s.type == "dir" and s.active]
    return dirs[0].id if dirs else ""


# defaults(): the settings: an earlier run's recorded answers, overridden by the given ones, and every empty setting
# filled from this node (storage, bridge, VLAN, id block, reserved and allowed networks, snippets, ISO storages, DNS,
# domain, and a name with the cluster's name) UwU
def defaults(facts: Facts, given: dict | None = None) -> Answers:
    """The settings: what an earlier run of this installation recorded, overridden by `given` (answers file /
    flags), and every setting still empty filled with what this node suggests."""
    recorded = (facts.record or {}).get("answers", {})
    settings = Answers(**{**recorded, **(given or {})})
    if not settings.vm_storage or not settings.range_storage:
        storage = default_storage(facts)
        settings.vm_storage = settings.vm_storage or storage
        settings.range_storage = settings.range_storage or storage
    settings.vm_bridge = settings.vm_bridge or facts.mgmt_bridge
    if not given or "vm_vlan" not in given:
        settings.vm_vlan = settings.vm_vlan or facts.mgmt_vlan
    if not settings.vmid_start:
        settings.vmid_start = free_vmid_block(facts)
    if not settings.reserved_networks:
        settings.reserved_networks = list(facts.networks)
    if not settings.allowed_networks:
        settings.allowed_networks = [facts.mgmt_network]
    if not settings.snippets_storage:
        settings.snippets_storage = default_snippets(facts)
    if not settings.iso_storage or not settings.iso_storages:
        target, readable = default_iso_storages(facts)
        settings.iso_storage = settings.iso_storage or target
        settings.iso_storages = settings.iso_storages or readable
    if settings.iso_storage and settings.iso_storage not in settings.iso_storages:
        settings.iso_storages = [settings.iso_storage, *settings.iso_storages]
    if not settings.vm_dns:
        settings.vm_dns = list(facts.nameservers)
    if not settings.vm_domain:
        settings.vm_domain = facts.search_domain
    if settings.instance_name == "VALOR" and facts.cluster_name and facts.cluster_name.lower() != "valor":
        settings.instance_name = f"VALOR {facts.cluster_name}"
    return settings


# resolve_bridge(): (bridge name, must it be created?): a bridge an earlier run created, a new free one, or the one
# given >w<
def resolve_bridge(settings: Answers, facts: Facts) -> tuple[str, bool]:
    """(bridge name, must be created)."""
    recorded = (facts.record or {}).get("objects", {}).get("bridge")
    if settings.range_bridge == "new":
        if recorded and facts.bridge(recorded["name"]):
            return recorded["name"], False                # created by an earlier run of this installation
        return first_free_bridge(facts), True
    return settings.range_bridge, False


# ---- Validation OwO
# ---- Validation ------------------------------------------------------------------------------------------------

# _cidr(): is this a network? ^w^
def _cidr(value: str) -> bool:
    try:
        ipaddress.ip_network(value, strict=False)
        return True
    except ValueError:
        return False


# problems(): EVERYTHING wrong with the settings on this node, as "setting: what to fix" lines (empty = good to go) UwU
def problems(settings: Answers, facts: Facts, catalog: dict) -> list[str]:
    """Everything wrong with the settings on this node, as "setting: what to fix" lines (empty: good to go)."""
    return (_id_problems(settings) + _storage_problems(settings, facts) + _bridge_problems(settings, facts)
            + _vm_problems(settings) + _vmid_problems(settings, facts) + _network_problems(settings)
            + _template_problems(settings, facts, catalog) + _web_problems(settings))


# _id_problems(): the id's format >w<
def _id_problems(settings: Answers) -> list[str]:
    if not ID_RE.match(settings.id):
        return ["id: 2-12 lowercase letters/digits, starting with a letter"]
    return []


# _storage_problems(): the storages must exist, hold VM disks and be active; range storage must support linked
# clones OwO
def _storage_problems(settings: Answers, facts: Facts) -> list[str]:
    out = []
    for key in ("vm_storage", "range_storage"):
        storage = facts.storage(getattr(settings, key))
        if not storage:
            out.append(f"{key}: storage '{getattr(settings, key)}' does not exist on {facts.node}")
        elif not storage.images:
            out.append(f"{key}: storage '{storage.id}' cannot hold VM disks (content 'images' missing)")
        elif not storage.active:
            out.append(f"{key}: storage '{storage.id}' is not active")
    range_storage = facts.storage(settings.range_storage)
    if range_storage and range_storage.images and not range_storage.linked_clones:
        out.append(f"range_storage: '{range_storage.id}' ({range_storage.type}) does not support linked clones; "
                   "pick ZFS, LVM-thin, Ceph RBD or a directory/NFS storage")
    return out


# _bridge_problems(): the management bridge must exist; an existing range bridge must be VLAN-aware, have NO physical
# ports (range traffic must never leave the node) and differ from the management bridge ^w^
def _bridge_problems(settings: Answers, facts: Facts) -> list[str]:
    """The range bridge must be VLAN-aware and have no physical port: range traffic never leaves the node."""
    out = []
    if not facts.bridge(settings.vm_bridge):
        out.append(f"vm_bridge: bridge '{settings.vm_bridge}' does not exist on {facts.node}")
    if settings.range_bridge != "new":
        bridge = facts.bridge(settings.range_bridge)
        if not bridge:
            out.append(f"range_bridge: bridge '{settings.range_bridge}' does not exist")
        else:
            if not bridge.vlan_aware:
                out.append(f"range_bridge: '{bridge.name}' is not VLAN-aware")
            if bridge.ports and bridge.ports != "none":
                out.append(f"range_bridge: '{bridge.name}' has physical ports ({bridge.ports}); range traffic must "
                           "not leave the node")
            if bridge.name == settings.vm_bridge:
                out.append("range_bridge: must differ from the management bridge")
    return out


# _vm_problems(): sizes in range, a valid VLAN, a static IP with a gateway in its network, a valid host name, and the
# one supported uplink UwU
def _vm_problems(settings: Answers) -> list[str]:
    out = []
    if not 1 <= settings.vm_cores <= 64:
        out.append("vm_cores: 1-64")
    if not 2048 <= settings.vm_memory <= 65536:
        out.append("vm_memory: 2048-65536 MiB")
    if not 16 <= settings.vm_disk <= 2048:
        out.append("vm_disk: 16-2048 GiB")
    if not 0 <= settings.vm_vlan <= 4094:
        out.append("vm_vlan: 0 (untagged) or 1-4094")
    if settings.vm_ip != "dhcp":
        try:
            iface = ipaddress.IPv4Interface(settings.vm_ip)
            if iface.network.prefixlen == 32:
                raise ValueError
            if not settings.vm_gateway or ipaddress.IPv4Address(settings.vm_gateway) not in iface.network:
                out.append("vm_gateway: required with a static IP, inside the same network")
        except ValueError:
            out.append("vm_ip: 'dhcp' or an address with prefix length, e.g. 192.168.1.50/24")
    if not HOST_RE.match(settings.vm_hostname):
        out.append("vm_hostname: a DNS label (lowercase letters, digits, dashes)")
    if settings.uplink not in ("lan-dhcp",):
        out.append("uplink: only 'lan-dhcp' is available in this version (NAT through the VALOR VM: issue #15)")
    return out


# _vmid_problems(): the id block must start at a multiple of 100 and be free (this installation's own VMs, found by
# pool, don't count) >w<
def _vmid_problems(settings: Answers, facts: Facts) -> list[str]:
    """VALOR's block of VMIDs must be free (VMs of this same installation, found by pool, don't count)."""
    first, last = settings.vmid_start, settings.vmid_start + BLOCK - 1
    if settings.vmid_start < 100 or settings.vmid_start % 100:
        return ["vmid_start: a multiple of 100, at least 100"]
    ours = (settings.pool_ranges, settings.pool_templates, settings.pool_system)    # a re-run finds its own VMs
    taken = sorted(vmid for vmid in facts.used_vmids if first <= vmid <= last and facts.vm_pools.get(vmid) not in ours)
    if taken:
        return [f"vmid_start: VMIDs {first}-{last} must be free, but {taken[:5]} exist"]
    return []


# _network_problems(): valid VLAN range, networks, DNS servers, at least one allowed network, and the probe's format OwO
def _network_problems(settings: Answers) -> list[str]:
    out = []
    if not (2 <= settings.vlan_min <= settings.vlan_max <= 4094):
        out.append("vlan_min/vlan_max: 2 <= min <= max <= 4094")
    for key in ("reserved_networks", "allowed_networks"):
        bad = [net for net in getattr(settings, key) if not _cidr(net)]
        if bad:
            out.append(f"{key}: not networks: {bad}")
    if not settings.allowed_networks:
        out.append("allowed_networks: at least one network must be allowed to reach the web UI")
    for server in settings.nameservers + settings.vm_dns:
        try:
            ipaddress.ip_address(server)
        except ValueError:
            out.append(f"nameserver '{server}' is not an IP address")
    if not re.fullmatch(r"[0-9.]+:\d{1,5}", settings.internet_probe):
        out.append("internet_probe: public-ip:port, e.g. 1.1.1.1:443")
    return out


# _template_problems(): known OSes; ubuntu-24.04 must exist or be built; a directory-type snippet storage; valid ISO
# storages ^w^
def _template_problems(settings: Answers, facts: Facts, catalog: dict) -> list[str]:
    out = []
    unknown = [name for name in settings.templates if name not in catalog]
    if unknown:
        out.append(f"templates: unknown {unknown}; choose from {sorted(catalog)}")
    if "ubuntu-24.04" not in settings.templates and not any(_template_on_node(facts, settings, "ubuntu-24.04")):
        out.append("templates: ubuntu-24.04 is required (the VALOR VM and the default OS use it)")
    snippets = facts.storage(settings.snippets_storage)
    if not snippets or not snippets.file_based or not snippets.path:
        out.append(f"snippets_storage: '{settings.snippets_storage}' must be a directory-type storage on {facts.node}")
    if settings.iso_storage:
        iso_storage = facts.storage(settings.iso_storage)
        if not iso_storage or "iso" not in iso_storage.content:
            out.append(f"iso_storage: '{settings.iso_storage}' must be a storage with 'iso' content on {facts.node}")
    for storage_id in settings.iso_storages:
        if not facts.storage(storage_id):
            out.append(f"iso_storages: '{storage_id}' does not exist on {facts.node}")
    return out


# _web_problems(): a known TLS mode (with certificate files for "own"), and a valid admin username UwU
def _web_problems(settings: Answers) -> list[str]:
    out = []
    if settings.tls not in ("valor-ca", "own"):
        out.append("tls: 'valor-ca' or 'own' (ACME DNS-01 is planned: issue #7)")
    if settings.tls == "own":
        for key in ("tls_cert", "tls_key"):
            if not getattr(settings, key) or not Path(getattr(settings, key)).is_file():
                out.append(f"{key}: PEM file required with tls = own")
    if not USER_RE.match(settings.admin_user):
        out.append("admin_user: 2-64 letters, digits, dots, dashes, underscores or @")
    return out


# _template_on_node(): this installation's templates of an OS on this node >w<
def _template_on_node(facts: Facts, settings: Answers, os_name: str):
    tag = "os-" + os_name.replace(".", "-")
    for template in facts.valor_templates:
        if (template.get("node") == facts.node and template.get("pool") == settings.pool_templates
                and tag in (template.get("tags") or "")):
            yield template


# ---- Interactive editing OwO
# ---- Interactive editing ---------------------------------------------------------------------------------------

# _number() / _list(): ask for a number, or a comma-separated list ^w^
def _number(question: str, current: int) -> int:
    return int(ui.ask(question, str(current), lambda value: None if value.isdigit() else "a number"))


def _list(question: str, current: list[str]) -> list[str]:
    """A comma-separated answer as a list."""
    return ui.ask(question, ",".join(current)).replace(" ", "").split(",")


# edit(): walk through every setting (Enter keeps the current value): the VALOR VM, the ranges, the web UI, and the
# templates UwU
def edit(settings: Answers, facts: Facts, catalog: dict) -> Answers:
    """Walk through every setting; Enter keeps the current value."""
    # Choices for storages (with free space and linked-clone support) and bridges (with address and comment) >w<
    images = [s for s in facts.storages if s.images and s.active]
    storage_options = [(s.id, f"{s.type}, {s.avail_gib:.0f} GiB free{'' if s.linked_clones else ', no linked clones'}")
                       for s in images]
    bridge_options = [(b.name, (b.cidr or "no address") + (f" - {b.comments}" if b.comments else ""))
                      for b in facts.bridges]

    # The VALOR VM's settings OwO
    ui.say()
    ui.say(ui.bold("  VALOR VM"))
    settings.vm_storage = ui.choose("Storage for the VALOR VM's disk", storage_options, settings.vm_storage)
    settings.vm_cores = _number("vCPUs", settings.vm_cores)
    settings.vm_memory = _number("Memory (MiB)", settings.vm_memory)
    settings.vm_disk = _number("Disk (GiB)", settings.vm_disk)
    settings.vm_bridge = ui.choose("Network bridge (LAN users reach the web UI here)", bridge_options,
                                   settings.vm_bridge)
    settings.vm_vlan = _number("VLAN tag on that bridge (0 = none)", settings.vm_vlan)
    settings.vm_ip = ui.ask("IP address: 'dhcp' or address/prefix (reserve DHCP addresses in your router)",
                            settings.vm_ip)
    if settings.vm_ip != "dhcp":
        settings.vm_gateway = ui.ask("Gateway", settings.vm_gateway or facts.mgmt_gateway)
        settings.vm_dns = _list("DNS servers (comma-separated)", settings.vm_dns)
    settings.vm_hostname = ui.ask("Hostname", settings.vm_hostname)

    # The ranges' settings (a new isolated bridge, or an existing port-less VLAN-aware one) ^w^
    ui.say()
    ui.say(ui.bold("  Ranges"))
    settings.range_storage = ui.choose("Storage for range VMs and templates", storage_options, settings.range_storage)
    portless = [(b.name, "existing, VLAN-aware, no physical port") for b in facts.bridges if b.vlan_aware and not b.ports]
    settings.range_bridge = ui.choose(
        "Isolated network for range segments",
        [("new", f"create {first_free_bridge(facts)} (VLAN-aware, no physical port)"), *portless],
        settings.range_bridge)
    settings.vmid_start = _number(f"First VMID of VALOR's block of {BLOCK}", settings.vmid_start)
    settings.reserved_networks = _list("Networks ranges must never use or reach (comma-separated)",
                                       settings.reserved_networks)
    # The web UI's settings, then the templates UwU *submits the questionnaire*
    ui.say()
    ui.say(ui.bold("  Web UI"))
    settings.allowed_networks = _list("Networks allowed to open the web UI: your LAN and VPN subnets (comma-separated)",
                                      settings.allowed_networks)
    settings.tls = ui.choose("Web certificate",
                             [("valor-ca", "VALOR creates its own CA (import its certificate once)"),
                              ("own", "use your certificate and key (PEM files on this node)")], settings.tls)
    if settings.tls == "own":
        settings.tls_cert = ui.ask("Certificate file (PEM, with chain)", settings.tls_cert)
        settings.tls_key = ui.ask("Private key file (PEM)", settings.tls_key)
    settings.admin_user = ui.ask("First admin username", settings.admin_user)
    settings.templates = _list(f"Templates to build now ({', '.join(sorted(catalog))})", settings.templates)
    return settings
