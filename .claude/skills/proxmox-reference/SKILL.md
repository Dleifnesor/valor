---
name: proxmox-reference
description: Reference for how VALOR uses Proxmox VE (API, cloud-init, guest agent, permissions, networking) with live documentation links and a growing known-issues list. Use when a build error mentions Proxmox, the guest agent, cloud-init, permissions (HTTP 403), VLANs/bridges, templates or storage.
---

# Proxmox VE reference for VALOR

Cluster: Proxmox VE 9.x. The engine (user `valor@pve`, token `valor@pve!engine`) talks to the API on the node
configured in `/etc/valor/config.toml` (you cannot read it - ask the user/admin if a setting matters).
**Check `known-issues.md` first** - it lists errors already solved and their fixes.

## What the engine's token may do

| Path | Role | Means |
|---|---|---|
| `/pool/valor` | ValorEngine | create/configure/start/stop/delete VMs **in that pool only**; guest agent incl. exec |
| `/pool/valor-templates` | ValorTemplateUser | clone templates (no changes to them) |
| `/storage/valor-tank` | ValorStorage | allocate disks on VALOR's ZFS dataset (`tank/valor`, 1 TB quota) |
| `/sdn/zones/localnetwork/vmbr100`, `.../vmbr0` | PVESDNUser | attach NICs to the segment bridge and the router uplink |
| `/nodes/<node>` | ValorNodeAudit | read node status (free memory) |

A **403** means the spec asked for something outside these (another storage, bridge, or a VM outside the pool).
Fix the spec; never ask for broader rights.

Administrator-only (`valor-admin`, run by root, not available to you): build templates
(`valor-admin template build <os>`), create bridges (`valor-admin bridge create <name>`), deploy the engine.

## Mechanics

- **Templates**: `templates/catalog.yaml` maps `os` -> template VMID (9000 ubuntu-24.04, 9001 ubuntu-26.04,
  9002 debian-13, 9003 debian-12). Images are signature/checksum verified; qemu-guest-agent is baked in.
  Range VMs are **linked clones** (instant, ZFS).
- **Cloud-init** (PVE-generated): user `valor`, SSH key, static IP + gateway per NIC (`ipconfigN`), DNS 1.1.1.1 /
  9.9.9.9, `ciupgrade 0` (no upgrade at first boot - isolated segments have no internet then).
- **Guest agent** is the only management channel (no management network). PVE 9 split agent privileges:
  `VM.GuestAgent.Audit`, `.FileRead`, `.FileWrite`, `.FileSystemMgmt`, `.Unrestricted` (exec needs Unrestricted).
- **Networking**: segment NICs on `vmbr100` (VLAN-aware, **no physical port** - traffic never leaves the node)
  with `tag=<vlan>`; router uplink on `vmbr0` (home LAN, DHCP). Ranges therefore live on one node.

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
