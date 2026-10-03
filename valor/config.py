"""Engine configuration, read from /etc/valor/config.toml (owned by the 'valor' user)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("VALOR_CONFIG", "/etc/valor/config.toml"))


@dataclass(frozen=True)
class Config:
    # Proxmox API
    api_host: str = "127.0.0.1"
    api_port: int = 8006
    verify_ssl: str | bool = "/etc/valor/pve-root-ca.pem"
    token_file: str = "/etc/valor/token.json"
    node: str = "svr-02"
    # Resources the engine may use
    pool: str = "valor"
    template_pool: str = "valor-templates"
    storage: str = "valor-tank"
    segment_bridge: str = "vmbr100"
    uplink_bridge: str = "vmbr0"
    vmid_min: int = 4000
    vmid_max: int = 4999
    vlan_min: int = 100
    vlan_max: int = 3999
    max_memory_fraction: float = 0.7
    # Paths
    project_dir: str = "/srv/valor"
    state_dir: str = "/var/lib/valor"
    ssh_public_key: str = "/etc/valor/ssh/id_ed25519.pub"
    # Guest defaults
    default_os: str = "ubuntu-24.04"
    nameservers: tuple[str, ...] = ("1.1.1.1", "9.9.9.9")
    guest_user: str = "valor"
    extra: dict = field(default_factory=dict)

    @property
    def ranges_dir(self) -> Path:
        return Path(self.project_dir) / "ranges"

    @property
    def roles_dir(self) -> Path:
        return Path(self.project_dir) / "roles"

    @property
    def baselines_dir(self) -> Path:
        return Path(self.project_dir) / "baselines"

    @property
    def catalog_file(self) -> Path:
        return Path(self.project_dir) / "templates" / "catalog.yaml"

    @property
    def journals_dir(self) -> Path:
        return Path(self.project_dir) / "journals"

    @property
    def jobs_dir(self) -> Path:
        return Path(self.state_dir) / "jobs"


def load_config(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        return Config()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    flat: dict = {}
    for section in ("proxmox", "resources", "paths", "guest"):
        flat.update(raw.get(section, {}))
    if "nameservers" in flat:
        flat["nameservers"] = tuple(flat["nameservers"])
    known = {f for f in Config.__dataclass_fields__}
    extra = {k: v for k, v in flat.items() if k not in known}
    kwargs = {k: v for k, v in flat.items() if k in known}
    return Config(**kwargs, extra=extra)
