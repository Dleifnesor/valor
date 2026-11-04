# *sets up the test playground* OwO tests/conftest.py: shared pytest FIXTURES! ^w^
# pytest loads this file automatically; any test that asks for an argument named `cfg` or `ref_spec` gets the values
# made here *wags tail*
from pathlib import Path

# pytest itself; the engine Config and the spec loader UwU
import pytest

from valor.config import Config
from valor.spec import load_spec

# ROOT = the repository >w<
ROOT = Path(__file__).resolve().parent.parent


# cfg: an engine Config for tests: the repo's content, a temporary state folder, a fake SSH key, and made-up names
# for the node, pools, storage and bridges (tmp_path is a fresh temp folder per test, from pytest) OwO
@pytest.fixture
def cfg(tmp_path):
    key = tmp_path / "id.pub"
    key.write_text("ssh-ed25519 AAAATEST valor-engine\n")
    return Config(project_dir=str(ROOT), state_dir=str(tmp_path), ssh_public_key=str(key), node="pve1",
                  pool="valor-ranges", template_pool="valor-templates", storage="local-lvm",
                  segment_bridge="vmbr100", uplink_bridge="vmbr0", reserved_networks=("192.168.50.0/24",))


# ref_spec: the example web2tier range, loaded and normalized ^w^
@pytest.fixture
def ref_spec():
    spec, _ = load_spec("web2tier.yaml", ROOT / "examples", "ubuntu-24.04")
    return spec
