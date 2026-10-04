"""Engine configuration, read from /etc/valor/config.toml (owned by the 'valor' user).

Nothing here describes a particular cluster: the installer discovers the node, storage, bridges and networks
and writes them into the config file. Fields left empty make the engine refuse to run (see `missing()`).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("VALOR_CONFIG", "/etc/valor/config.toml"))
REQUIRED = ("node", "pool", "template_pool", "storage", "segment_bridge", "uplink_bridge")


@dataclass(frozen=True)
class Config:
    # Proxmox API
    api_host: str = "127.0.0.1"
    api_port: int = 8006
    verify_ssl: str | bool = "/etc/valor/pve-root-ca.pem"
    token_file: str = "/etc/valor/token.json"
    node: str = ""                              # node that hosts the ranges
    # Resources the engine may use
    pool: str = ""                              # range VMs (the token's VM rights end at this pool)
    template_pool: str = ""                     # OS templates, found by tag
    storage: str = ""                           # range disks (same storage as the templates: linked clones)
    segment_bridge: str = ""                    # VLAN-aware bridge without physical ports
    uplink_bridge: str = ""                     # range routers' NAT uplink
    uplink_vlan: int = 0                        # VLAN tag on the uplink bridge (0 = untagged)
    vmid_min: int = 4000
    vmid_max: int = 4999
    vlan_min: int = 100
    vlan_max: int = 3999
    max_memory_fraction: float = 0.7
    reserved_networks: tuple[str, ...] = ()     # never used by segments and never reachable from ranges
    # Paths
    project_dir: str = "/srv/valor"
    content_dir: str = ""                       # roles/, baselines/, templates/ (default: project_dir)
    data_dir: str = ""                          # ranges/, journals/ (default: project_dir)
    state_dir: str = "/var/lib/valor"
    ssh_public_key: str = "/etc/valor/ssh/id_ed25519.pub"
    job_runner: str = "spawn"                   # "spawn": detached process; "worker": the valor-worker service
    # Guest defaults
    default_os: str = "ubuntu-24.04"
    nameservers: tuple[str, ...] = ("1.1.1.1", "9.9.9.9")
    internet_probe: str = "1.1.1.1:443"         # public host:port the egress tests try to reach
    guest_user: str = "valor"
    extra: dict = field(default_factory=dict)

    def missing(self) -> list[str]:
        return [k for k in REQUIRED if not getattr(self, k)]

    @property
    def probe(self) -> tuple[str, int]:
        host, _, port = self.internet_probe.rpartition(":")
        return host, int(port)

    @property
    def _content(self) -> Path:
        return Path(self.content_dir or self.project_dir)

    @property
    def _data(self) -> Path:
        return Path(self.data_dir or self.project_dir)

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


TUPLES = ("nameservers", "reserved_networks")


def load_config(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        return Config()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    flat: dict = {}
    for section in ("proxmox", "resources", "paths", "guest"):
        flat.update(raw.get(section, {}))
    for k in TUPLES:
        if k in flat:
            flat[k] = tuple(flat[k])
    known = {f for f in Config.__dataclass_fields__}
    extra = {k: v for k, v in flat.items() if k not in known}
    kwargs = {k: v for k, v in flat.items() if k in known}
    return Config(**kwargs, extra=extra)
