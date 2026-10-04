# *rolls out the welcome mat* OwO installer/__init__.py: the VALOR installer package! ^w^
# The installer runs as root on a Proxmox node and uses ONLY Python's standard library (nothing to install first).
# It discovers the cluster, asks only what it can't work out, and creates everything VALOR needs (see the docstring).
# This file holds a few constants every installer module shares *wags tail*
"""VALOR installer: runs as root on any Proxmox VE 8.2+/9 node, using only the Python standard library.

It discovers the cluster, asks only what it cannot decide, and creates everything VALOR needs: a least-privilege
API identity, an isolated range network, verified OS templates and the VALOR VM with the web UI.
"""

# re finds the version string; Path builds paths UwU
import re
from pathlib import Path

# SOURCE: the folder that contains installer/ (the repo or the release bundle) >w<
# VERSION: read from valor/__init__.py so there's ONE version number for everything OwO
# RECORD_DIR: where the installer keeps its record of what it made: /etc/pve/priv/valor, which Proxmox's cluster file
# system shares with every node and only root can read ^w^
# MIN_PVE: the oldest Proxmox VE version supported (8.2) UwU
SOURCE = Path(__file__).resolve().parent.parent          # repository / release bundle root
VERSION = re.search(r'__version__ = "([^"]+)"', (SOURCE / "valor" / "__init__.py").read_text()).group(1)
RECORD_DIR = Path("/etc/pve/priv/valor")                 # cluster-wide, root-only (pmxcfs)
MIN_PVE = (8, 2)
