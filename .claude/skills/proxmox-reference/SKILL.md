---
name: proxmox-reference
description: Reference for how VALOR uses Proxmox VE (API, cloud-init, guest agent, permissions, networking) with live documentation links and a growing known-issues list. Use when a build error mentions Proxmox, the guest agent, cloud-init, permissions (HTTP 403), VLANs/bridges, templates or storage.
---

# Proxmox VE reference for VALOR

Proxmox VE 8.2+ or 9.x. The engine talks to the API with one privilege-separated token, on the node configured in
`/etc/valor/config.toml` (you cannot read it - ask the user/admin if a setting matters; the web UI's dashboard
and `valor info` in the VALOR VM show the values the engine uses). **Check `known-issues.md` first** - it lists errors already solved and their fixes.

## What the engine's token may do

The installer (`installer/identity.py`) grants exactly these; `<id>` is the instance id (default `valor`).

| Path | Role | Means |
|---|---|---|
| `/pool/<id>-ranges` | ValorEngine | create/configure/start/stop/delete VMs **in that pool only**; guest agent incl. exec |
| `/pool/<id>-templates` | ValorTemplateUser | clone templates (no changes to them) |
| `/storage/<range storage>` | ValorStorage | allocate range disks |
| `/sdn/zones/localnetwork/<range bridge>`, `.../<uplink bridge>` | PVESDNUser | attach NICs to the segment bridge and the router uplink |
| `/nodes/<node>` | ValorNodeAudit, ValorDownload | read node status (free memory); let Proxmox download ISOs |
| `/storage/<iso storage>` (+ readers) | ValorISO, ValorISOReader | the ISO library |

The VALOR VM sits in `<id>-system`, where the token has no rights. A **403** means the spec asked for something
outside these (another storage, bridge, or a VM outside the pool). Fix the spec; never ask for broader rights.

Administrator-only (root, not available to you): `./install.sh --template <os>` builds or replaces a template,
`./install.sh --upgrade` deploys a new VALOR version.

## Mechanics

- **Templates**: `templates/catalog.toml` lists the OSes. Templates are found by tags (`valor-template`,
  `os-<name>`) in the template pool, never by a fixed VMID. Images are signature/checksum verified; the QEMU guest
  agent is baked in. Range VMs are **linked clones** on the range storage (instant on ZFS, LVM-thin, Ceph, qcow2).
- **Cloud-init** (PVE-generated): user `valor`, SSH key, static IP + gateway per NIC (`ipconfigN`), DNS = the
  configured nameservers, `ciupgrade 0` (no upgrade at first boot - isolated segments have no internet then).
  Windows hosts have no cloud-init: VALOR prepares them over the guest agent (`valor/windows.py`).
- **Guest agent** is the only management channel (no management network). PVE 9 split agent privileges:
  `VM.GuestAgent.Audit`, `.FileRead`, `.FileWrite`, `.FileSystemMgmt`, `.Unrestricted` (exec needs Unrestricted).
- **Networking**: segment NICs on the range bridge (VLAN-aware, **no physical port** - traffic never leaves the
  node) with `tag=<vlan>`; the router's uplink on the management bridge (the LAN, DHCP). Ranges therefore live on one
  node.

## Live documentation

- Proxmox VE admin guide: https://pve.proxmox.com/pve-docs/pve-admin-guide.html
- API viewer: https://pve.proxmox.com/pve-docs/api-viewer/
- Cloud-init support: https://pve.proxmox.com/wiki/Cloud-Init_Support
- qm(1): https://pve.proxmox.com/pve-docs/qm.1.html
- User management and privileges: https://pve.proxmox.com/pve-docs/chapter-pveum.html
- QEMU guest agent: https://pve.proxmox.com/wiki/Qemu-guest-agent
- cloud-init docs: https://cloudinit.readthedocs.io/
- nftables wiki: https://wiki.nftables.org/

When something here contradicts the live documentation or the cluster's behaviour, trust the live source and
update this file and `known-issues.md`.
