from pathlib import Path

import pytest

from valor.config import Config
from valor.spec import load_spec

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path):
    key = tmp_path / "id.pub"
    key.write_text("ssh-ed25519 AAAATEST valor-engine\n")
    return Config(project_dir=str(ROOT), state_dir=str(tmp_path), ssh_public_key=str(key), node="pve1",
                  pool="valor-ranges", template_pool="valor-templates", storage="local-lvm",
                  segment_bridge="vmbr100", uplink_bridge="vmbr0", reserved_networks=("192.168.50.0/24",))


@pytest.fixture
def ref_spec():
    spec, _ = load_spec("web2tier.yaml", ROOT / "ranges", "ubuntu-24.04")
    return spec
