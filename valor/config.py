# OwO *opens the settings drawer* valor/config.py holds the ENGINE's settings! ^w^
# The installer looks around the cluster (which node, which storage, which bridges and networks) and writes what
# it found to /etc/valor/config.toml. This file reads that file into one frozen Config object that every engine
# module gets passed. Required settings left empty make the engine refuse to run (see missing()) *wags tail*
"""Engine configuration, read from /etc/valor/config.toml (owned by the 'valor' user).

Nothing here describes a particular cluster: the installer discovers the node, storage, bridges and networks
and writes them into the config file. Fields left empty make the engine refuse to run (see `missing()`).
"""

# Lazy type hints UwU
from __future__ import annotations

# os reads the VALOR_CONFIG environment variable; tomllib reads TOML; dataclass/field build Config; Path for
# file paths >w<
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Where the config file is: /etc/valor/config.toml, unless the VALOR_CONFIG environment variable points elsewhere
# (tests and development use that) OwO
# REQUIRED: settings that have no sensible default; without them the engine can't safely do anything ^w^
CONFIG_PATH = Path(os.environ.get("VALOR_CONFIG", "/etc/valor/config.toml"))
REQUIRED = ("node", "pool", "template_pool", "storage", "segment_bridge", "uplink_bridge")


# Config: every engine setting with its default. frozen=True = read-only after loading, so no module can change a
# setting under another module's paws *guards it with tail* UwU
@dataclass(frozen=True)
class Config:
    # How to reach Proxmox: API host and port (the VALOR VM talks to its own node), the CA file used to check the
    # API's TLS certificate (or True/False), and the file with the API token >w<
    # Proxmox API
    api_host: str = "127.0.0.1"
    api_port: int = 8006
    verify_ssl: str | bool = "/etc/valor/pve-root-ca.pem"
    token_file: str = "/etc/valor/token.json"
    node: str = ""                              # node that hosts the ranges
    # What VALOR may use. The token's rights are limited to these pools, so it can't touch anything else ^w^
    # Resources the engine may use
    pool: str = ""                              # range VMs (the token's VM rights end at this pool)
    template_pool: str = ""                     # OS templates, found by tag
    storage: str = ""                           # range disks (same storage as the templates: linked clones)
    segment_bridge: str = ""                    # VLAN-aware bridge without physical ports
    uplink_bridge: str = ""                     # range routers' NAT uplink
    uplink_vlan: int = 0                        # VLAN tag on the uplink bridge (0 = untagged)
    # VM ids (4000-4999) and VLAN tags (100-3999) VALOR picks from, so ranges never collide with other VMs/VLANs OwO
    vmid_min: int = 4000
    vmid_max: int = 4999
    vlan_min: int = 100
    vlan_max: int = 3999
    # VALOR won't start builds that would push the node's memory use past 70% (leaves room for everything else) UwU
    max_memory_fraction: float = 0.7
    reserved_networks: tuple[str, ...] = ()     # never used by segments and never reachable from ranges
    iso_storage: str = ""                       # ISO library: downloads and uploads go here
    iso_storages: tuple[str, ...] = ()          # ISO storages VALOR may list (includes iso_storage)
    # Where things live on disk. project_dir defaults to the checkout this file is in (two folders up), which is what
    # development and tests use; the VALOR VM sets content_dir/data_dir explicitly >w<
    # Paths
    project_dir: str = str(Path(__file__).resolve().parent.parent)     # the checkout (development and tests)
    content_dir: str = ""                       # roles/, baselines/, templates/ (default: project_dir)
    data_dir: str = ""                          # ranges/, journals/ (default: project_dir)
    state_dir: str = "/var/lib/valor"
    ssh_public_key: str = "/etc/valor/ssh/id_ed25519.pub"
    secret_key_file: str = "/etc/valor/secret.key"   # encrypts range login passwords at rest
    # Defaults for range VMs: the default OS, DNS servers, the internet address the egress tests try, and the admin
    # user VALOR creates in every VM ^w^
    # Guest defaults
    default_os: str = "ubuntu-24.04"
    nameservers: tuple[str, ...] = ("1.1.1.1", "9.9.9.9")
    internet_probe: str = "1.1.1.1:443"         # public host:port the egress tests try to reach
    guest_user: str = "valor"
    # `extra` holds settings this version doesn't know (kept so nothing is lost); default_factory gives each Config
    # its own empty dict OwO
    extra: dict = field(default_factory=dict)

    # missing: the names of REQUIRED settings that are empty (an empty list = all good) UwU
    def missing(self) -> list[str]:
        return [k for k in REQUIRED if not getattr(self, k)]

    # probe: internet_probe split into (host, port). rpartition splits at the LAST colon, so "1.1.1.1:443" gives
    # ("1.1.1.1", "443") >w<
    @property
    def probe(self) -> tuple[str, int]:
        host, _, port = self.internet_probe.rpartition(":")
        return host, int(port)

    # _content / _data: the content and data folders, falling back to project_dir when not set ^w^
    @property
    def _content(self) -> Path:
        return Path(self.content_dir or self.project_dir)

    @property
    def _data(self) -> Path:
        return Path(self.data_dir or self.project_dir)

    # Handy paths built from those folders, so no module ever glues paths together by itself *nuzzles*:
    #   ranges_dir    saved range specs (<data>/ranges)
    #   journals_dir  what each range build did (<data>/journals)
    #   roles_dir     the roles = install scripts (<content>/roles)
    #   baselines_dir security baselines (<content>/baselines)
    #   catalog_file  the OS template catalog (<content>/templates/catalog.toml)
    #   jobs_dir      queued and finished jobs (<state>/jobs) OwO
    @property
    def ranges_dir(self) -> Path:
        return self._data / "ranges"

    @property
    def journals_dir(self) -> Path:
        return self._data / "journals"

    @property
    def roles_dir(self) -> Path:
        return self._content / "roles"

    @property
    def baselines_dir(self) -> Path:
        return self._content / "baselines"

    @property
    def catalog_file(self) -> Path:
        return self._content / "templates" / "catalog.toml"

    @property
    def jobs_dir(self) -> Path:
        return Path(self.state_dir) / "jobs"


# TOML gives lists; the frozen Config keeps these as tuples so it stays hashable and read-only.
TUPLES = ("nameservers", "reserved_networks", "iso_storages")


# load_config: read the file into a Config ^w^
def load_config(path: Path | None = None) -> Config:
    """Read the config file. Its sections ([proxmox], [resources], ...) are only there for people: they are
    flattened into one Config. Keys VALOR doesn't know end up in `extra`."""
    path = path or CONFIG_PATH
    # No file (tests, a fresh checkout) = all defaults UwU
    if not path.exists():
        return Config()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    # Merge the sections into one flat dict (the sections only exist to make the file easier for people to read) >w<
    flat: dict = {}
    for section in ("proxmox", "resources", "paths", "guest"):
        flat.update(raw.get(section, {}))
    # Lists from TOML become tuples, as Config expects OwO
    for key in TUPLES:
        if key in flat:
            flat[key] = tuple(flat[key])
    # Split known settings from unknown ones (unknown ones go to `extra` instead of crashing) and build the Config ^w^
    known = set(Config.__dataclass_fields__)
    extra = {key: value for key, value in flat.items() if key not in known}
    settings = {key: value for key, value in flat.items() if key in known}
    return Config(**settings, extra=extra)
