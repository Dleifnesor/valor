#!/usr/bin/env bash
# OwO *bounces up to the front door* hewwo! install.sh is the FIRST thing you run to get VALOR ^w^
# It's a tiny bash wrapper: it checks that it's root on a real Proxmox node with the needed tools, then hands over
# to the Python installer (installer/). The usage notes below are the original header *wags tail*
# VALOR installer for Proxmox VE. Run as root on the Proxmox node that should host VALOR:
#
#   ./install.sh                 interactive install (detects everything, asks only what it must)
#   ./install.sh --help          all options (unattended installs, status, upgrade, uninstall)
#
# Needs only what every Proxmox VE 8.2+/9 node ships: bash, Python 3 (standard library), pvesh/qm, curl, gpg.
# Strict mode: stop on any error (-e), on unset variables (-u), and when any part of a pipe fails (pipefail) UwU
set -euo pipefail

# `here` = the folder this script lives in (so it works from anywhere) >w<
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Must be root (EUID 0) OwO
if [[ $EUID -ne 0 ]]; then
  echo "Run this as root on a Proxmox VE node (for example: sudo ./install.sh)." >&2
  exit 1
fi
# Must be a Proxmox VE node (pveversion and pvesh exist) ^w^
if ! command -v pveversion >/dev/null 2>&1 || ! command -v pvesh >/dev/null 2>&1; then
  echo "This is not a Proxmox VE node: VALOR's installer runs in a Proxmox node's shell." >&2
  exit 1
fi
# Python 3.11+ (the installer uses tomllib); the tiny inline program exits non-zero on older versions UwU
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "Python 3.11 or newer is required (Proxmox VE 8.2+ ships it)." >&2
  exit 1
fi
# The other tools the installer calls >w<
for tool in curl gpg qemu-img ssh-keygen systemd-run; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Missing tool: $tool" >&2; exit 1; }
done

# Go to the repo and REPLACE this shell with the Python installer (exec), passing every argument along OwO *waves*
cd "$here"
exec python3 -m installer "$@"
