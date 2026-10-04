"""VALOR installer: runs as root on any Proxmox VE 8.2+/9 node, using only the Python standard library.

It discovers the cluster, asks only what it cannot decide, and creates everything VALOR needs: a least-privilege
API identity, an isolated range network, verified OS templates and the VALOR VM with the web UI.
"""

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent          # repository / release bundle root
VERSION = re.search(r'__version__ = "([^"]+)"', (SOURCE / "valor" / "__init__.py").read_text()).group(1)
RECORD_DIR = Path("/etc/pve/priv/valor")                 # cluster-wide, root-only (pmxcfs)
MIN_PVE = (8, 2)
