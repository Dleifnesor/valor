# Installing VALOR

VALOR installs from the shell of any Proxmox VE node. The installer looks at the cluster, asks only what it
cannot decide, and creates everything VALOR needs. Then you use VALOR from a browser.

## Requirements

| | |
|---|---|
| Proxmox VE | 8.2 or newer (8.x and 9.x), a single node or a cluster |
| Node tools | only what every Proxmox node has: bash, Python 3.11+ (standard library), `pvesh`/`qm`, curl, gpg |
| Resources for VALOR | 2 vCPU, 4 GiB RAM and 32 GiB disk for the VALOR VM (adjustable), plus whatever your ranges use |
| Storage | one storage with VM disks that supports linked clones: ZFS, LVM-thin, Ceph RBD, or a directory/NFS storage (qcow2). Plain (thick) LVM does not. |
| Snippets | a directory-type storage (usually `local`) for one cloud-init file used while building templates. The installer enables `snippets` on it if needed. |
| Network | the node needs internet access during the install (Ubuntu cloud images, Ubuntu and Python packages). Range routers get their uplink address from your LAN's DHCP (issue #15 adds a NAT option for networks without DHCP). |
| Browser access | from the LAN or a VPN subnet you list; VALOR is never exposed to the internet |

## Install

```bash
git clone https://github.com/Dleifnesor/valor
cd valor
./install.sh
```

Run it on the node that should host VALOR and its ranges (ranges live on one node until milestone 5 adds
multi-node ranges through Proxmox SDN).

The installer:

1. **Checks the node**: Proxmox version, memory, internet access, and pending network changes (it refuses to touch
   the network while other changes wait in `/etc/network/interfaces.new`).
2. **Discovers** nodes, storages (type, content, free space, linked-clone support), bridges, the management network
   and its VLAN, the networks the node uses or routes to, free VMIDs and existing VALOR templates.
3. **Shows a plan** with a default for every setting, then asks *install / edit / quit*. *Edit* walks through
   every question; Enter keeps the default.
4. **Creates the Proxmox identity**: roles, a token-only user, a privilege-separated API token, three pools (ranges,
   templates, the VALOR VM) and ACLs on exactly those pools, the range storage, the two bridges and node status.
5. **Creates the range network**: a VLAN-aware bridge without physical ports (the first free `vmbrN`, N >= 100).
   It is added through the Proxmox API after a backup and a diff check, with a 5-minute rollback timer. The timer
   restores the old configuration unless the node still reaches its gateway afterwards.
6. **Builds OS templates** from official cloud images. Each checksum list's signature is verified with the
   shipped signing key, the image checksum is verified too, and the QEMU guest agent is baked in. Existing VALOR
   templates are reused.
7. **Creates the VALOR VM** (Ubuntu 24.04 LTS) and provisions it **over the guest agent**: no SSH or network
   access to the VM is needed. Inside, `appliance/setup.sh` installs hash-pinned Python packages, nginx (TLS), the
   firewall, the web service and the job worker.
8. **Checks** that the web UI answers with a valid certificate, and that the engine's token gets HTTP 403 on the
   VALOR VM.

At the end it prints the URL. The first admin's password is saved root-only in
`/root/valor-<id>-credentials.txt`. At the first sign-in you set up two-factor authentication (an authenticator
app) and receive 10 recovery codes.

## Settings

| Setting | Default | Notes |
|---|---|---|
| `id` | `valor` | Prefix of every Proxmox object: pools `<id>-ranges`, `<id>-templates`, `<id>-system`, user `<id>@pve` |
| `instance_name` | `VALOR` (+ cluster name) | Shown in the UI and in authenticator apps |
| `vm_storage`, `range_storage` | a storage named `*valor*`, else the one with the most free space that supports linked clones | Templates live on the range storage (linked clones need the same storage) |
| `vm_cores`, `vm_memory`, `vm_disk` | 2, 4096 MiB, 32 GiB | |
| `vm_bridge`, `vm_vlan` | the bridge (and VLAN) of the node's default route | Users reach the web UI on this network |
| `vm_ip` | `dhcp` | Or `192.168.1.50/24` with `vm_gateway` and `vm_dns`. With DHCP, reserve the address in your DHCP server. |
| `vm_hostname`, `vm_domain` | `valor`, the node's search domain | Both go into the certificate |
| `range_bridge` | `new` | Or the name of an existing VLAN-aware bridge **without** physical ports |
| `uplink` | `lan-dhcp` | Range routers' internet uplink on `vm_bridge` |
| `vmid_start` | first free block of 1000 at or above 4000 | Ranges use `start`..`start+899`, templates `+900`..`+989`, the VALOR VM `start+999` |
| `vlan_min`, `vlan_max` | 100, 3999 | VLANs on the range bridge |
| `reserved_networks` | every network the node is on or routes to | Segments never overlap them and range egress never reaches them |
| `nameservers` | 1.1.1.1, 9.9.9.9 | DNS for range VMs (public, because range egress may only reach public addresses) |
| `internet_probe` | `1.1.1.1:443` | What the egress tests try to reach |
| `templates` | `ubuntu-24.04` | Linux templates to build now (`ubuntu-24.04` is required). Also `ubuntu-26.04`, `debian-13`, `debian-12`, `kali`, `rocky-10`, `alma-10`; add more later, including Windows: [More operating systems](#more-operating-systems) |
| `snippets_storage` | a storage with snippets, else `local` | |
| `iso_storage` | the ISO storage with the most free space, shared storage first | Where the ISO library downloads and uploads media |
| `iso_storages` | every active storage with ISO content | ISO storages VALOR may list (read-only except `iso_storage`) |
| `allowed_networks` | the management network | Add VPN subnets that should reach the web UI |
| `tls` | `valor-ca` | `valor-ca`: VALOR's own CA (download `https://<vm>/ca.crt` and trust it once). `own`: your certificate (`tls_cert`, `tls_key`: PEM files on the node). ACME DNS-01 is planned (issue #7). |
| `admin_user` | `admin` | |

## Unattended and repeatable installs

Every setting can come from a TOML answers file. Anything missing gets the detected default.

```bash
./install.sh --dry-run --save-answers site.toml   # show the plan, write all settings, change nothing
./install.sh --answers site.toml --yes            # install exactly that, without questions
```

```toml
[valor]
id = "valor"

[vm]
vm_storage = "local-lvm"
vm_ip = "192.168.10.50/24"
vm_gateway = "192.168.10.1"
vm_dns = ["192.168.10.1"]

[ranges]
range_storage = "local-lvm"

[web]
allowed_networks = ["192.168.10.0/24", "10.8.0.0/24"]   # LAN + WireGuard clients
```

## What the installer creates on the cluster

| Object | Name | Removed by `--uninstall` |
|---|---|---|
| Roles | `ValorEngine`, `ValorTemplateUser`, `ValorStorage`, `ValorNodeAudit`, `ValorISO`, `ValorISOReader`, `ValorDownload` | yes, if VALOR created them and nothing else uses them |
| User + token | `<id>@pve`, `<id>@pve!appliance` (privilege-separated) | token yes; user only if VALOR created it |
| Pools | `<id>-ranges`, `<id>-templates`, `<id>-system` | if empty |
| ACLs | the pools above, the range storage, the two bridges, node status, the ISO storages | yes |
| Bridge | `vmbrN` | if VALOR created it and no VM uses it (same rollback safety net) |
| Templates | `tpl-<os>` in `<id>-templates` | the ones VALOR built, once no range depends on them |
| VALOR VM | `<hostname>`, VMID `start+999`, pool `<id>-system` | yes |
| Snippet | `<snippets storage>/snippets/valor-template-vendor.yaml` | yes |
| Record | `/etc/pve/priv/valor/<id>.json` (+ break-glass SSH key) | yes |

The install record lives in `/etc/pve/priv`: it is cluster-wide and root-only. Upgrade and uninstall use it,
so they only ever touch what this installation made. A failed install can be resumed by running the installer
again.

## Day 2

```bash
./install.sh --status                       # what is installed, and does the web UI answer?
./install.sh --upgrade                      # push this checkout's version into the VALOR VM (data is kept)
./install.sh --template debian-13           # build another OS template
./install.sh --template rocky-10 --rebuild  # a fresh build (current updates); the old template is removed
./install.sh --uninstall                    # remove VALOR, keep range VMs
./install.sh --uninstall --purge-ranges     # remove VALOR and every range VM
```

Break-glass, as root on the node (works even if the network is broken):

```bash
qm guest exec <vmid> -- valor-web unlock admin       # clear a lockout
qm guest exec <vmid> -- valor-web reset-mfa admin    # set up MFA again at the next sign-in
```

## More operating systems

**Linux** templates come from official cloud images whose checksums (and, where the publisher signs them, the
checksum list's signature) are verified:

```bash
./install.sh --template kali
./install.sh --template alma-10
```

**Windows** templates are built from evaluation ISOs with an unattended setup, the VirtIO guest tools and sysprep.
The installer takes the ISOs from VALOR's ISO library, so download them there first (**ISO library -> Download to
the cluster**): the Windows evaluation medium and the VirtIO drivers (`virtio-win`). Then:

```bash
./install.sh --template windows-server-2022          # also -2022-core, windows-server-2025(-core), windows-11
```

A Windows build takes a while (setup, updates, sysprep). Range VMs are test systems, so VALOR turns off their license
checks (online license validation, activation attempts and notifications) and renews the evaluation before it runs
out: with Microsoft's rearm (`slmgr /rearm`), when less than 14 days are left, at every build and from a daily task in
the VM (scheduled task *VALOR evaluation renewal*, 03:00; Windows restarts 10 minutes after a rearm). Windows Server
can be rearmed 5 times (180 days each), Windows 11 once (90 days); after that, rebuild the template from current
evaluation media and replace the hosts, or bring your own license. `--rebuild` builds a fresh template and removes the old one (or keeps it, with a warning, while linked clones
of it still exist). Ranges use the newest template: the next plan of an existing range shows `replace` for its hosts
on that OS, so they are rebuilt from the new template only when someone approves that plan.

The OS list and how to add one: [CONTENT.md](CONTENT.md#os-catalog).

## Security

The token, pools, firewall, web and secrets protections are described in the README's
[security model](../README.md#security-model). The installer checks the two that matter most before it finishes: the
web UI answers with a valid certificate, and the engine's token gets HTTP 403 on the VALOR VM.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "pending network changes" | Apply or revert them in the Proxmox UI (System > Network), then run the installer again. |
| The VM gets no IP address | Your LAN has no DHCP server for it: use a static `vm_ip`. |
| The browser warns about the certificate | Download `https://<vm>/ca.crt` (also linked on the sign-in page) and add it to your OS or browser trust store. |
| "cannot verify the Proxmox API certificate" | The node uses a custom certificate that doesn't cover its IP address. Make sure the node's FQDN resolves, or reinstall the default certificate. |
| Setup failed inside the VM | `qm guest exec <vmid> -- tail -50 /var/log/valor-setup.log`, fix, run the installer again (it resumes). |
