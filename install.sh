#!/usr/bin/env bash
# VALOR installer for Proxmox VE. Run as root on the Proxmox node that should host VALOR:
#
#   ./install.sh                 interactive install (detects everything, asks only what it must)
#   ./install.sh --help          all options (unattended installs, status, upgrade, uninstall)
#
# Needs only what every Proxmox VE 8.2+/9 node ships: bash, Python 3 (standard library), pvesh/qm, curl, gpg.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Run this as root on a Proxmox VE node (for example: sudo ./install.sh)." >&2
  exit 1
fi
if ! command -v pveversion >/dev/null 2>&1 || ! command -v pvesh >/dev/null 2>&1; then
  echo "This is not a Proxmox VE node: VALOR's installer runs in a Proxmox node's shell." >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "Python 3.11 or newer is required (Proxmox VE 8.2+ ships it)." >&2
  exit 1
fi
for tool in curl gpg qemu-img ssh-keygen systemd-run; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Missing tool: $tool" >&2; exit 1; }
done

cd "$here"
exec python3 -m installer "$@"
