# Range spec reference (`apiVersion: valor/v1`)

```yaml
apiVersion: valor/v1            # optional, default valor/v1
name: web2tier                  # 2-15 chars: a-z, 0-9, '-'; starts with a letter. VM names become <name>-<host>
description: >                  # free text, shown in the journal
  What this range is for.
baseline: ubuntu-l1             # the Linux hardening baseline (default ubuntu-l1, covers Debian/Ubuntu/Kali and
                                # Rocky/Alma). Windows hosts get its Windows counterpart automatically (windows-l1;
                                # windows-moderate with linux-moderate) - never put a Windows profile here.
                                # linux-moderate: ubuntu-l1 plus lockout, password policy, notices, idle logout,
                                # audit rules, time sync - required by the compliance frameworks below.
                                # 'none' turns hardening off for every host (Windows too): only when asked to.
compliance:                     # optional: frameworks the range is built toward and reported on ("aligned with",
                                # never certified). Needs baseline: linux-moderate (cis-l1 alone: ubuntu-l1).
  frameworks: [nist-800-171]    # nist-800-171 (CUI / CMMC L2), nist-800-53-moderate, nist-800-53-low, pci-dss,
                                # hipaa, cis-l1
  scope: [lan]                  # segments holding the regulated data (CUI, cardholder data, ePHI): checked to
                                # have no internet access
router:                         # optional; the router 'rtr' is always created automatically
  cores: 1                      # 1-8
  memory: 1024                  # MiB, 512-8192
  disk: 10                      # GiB
  # os: ubuntu-24.04            # default: engine default OS

segments:                       # 1-16; each is one VLAN behind the router
  - name: dmz                   # unique; not equal to a host name
    vlan: 110                   # unique, unused, inside the usable range from cluster_info
    cidr: 10.110.0.0/24         # private, /16../29, must not overlap other segments or reserved networks
    internet: true              # egress to public addresses through the router's NAT (default false)
    description: ""

hosts:                          # 1-40
  - name: web                   # unique; 'rtr' is reserved
    segment: dmz
    address: 10.110.0.10        # inside the segment; .1 is the router (gateway)
    os: ubuntu-24.04            # optional; must have a template (cluster_info.templates). Linux: ubuntu-24.04,
                                # debian-13, kali, rocky-10, alma-10. Windows: windows-server-2022(-core),
                                # windows-server-2025(-core), windows-11 (CPU/memory/disk floors from the catalog)
    cores: 1                    # 1-16
    memory: 1024                # MiB, 512-65536
    disk: 10                    # GiB, 8-500 (can grow later, never shrink)
    iso: tails-7.0-amd64.iso    # optional: an ISO from the ISO library attached as a CD-ROM (changing it: no reboot)
    install: template           # template (default: clone the cloud image, ~1 min) or iso: install from the OS's
                                # installer ISO with a generated kickstart (rocky-10, alma-10; ~10 min; the ISO
                                # must be in the ISO library)
    nested: false               # true: pass hardware virtualization through (CPU type host) to run a hypervisor
                                # (e.g. Proxmox VE) inside the host; needs VT-x/AMD-V + nested KVM on the node
    roles:                      # applied in order; see roles/<name>/role.yaml for params
      - name: nginx
      - name: postgresql
        params:
          allow_from: [web]     # host names become /32, segment names their network

policy:                         # allowed flows between segments; everything else is denied
  - from: dmz                   # segment or host
    to: lan                     # segment or host (must be in a different segment)
    proto: tcp                  # tcp | udp | icmp | any
    ports: [5432]               # required for tcp/udp; e.g. [80, 443, "8000-8100"]
    description: web tier to database

tests:                          # explicit promises to verify (from inside the guests)
  - name: web reaches db on 5432
    from: web                   # a host
    to: db                      # a host, an IPv4 address or 'internet'
    proto: tcp                  # tcp | icmp
    port: 5432                  # needed for tcp (internet defaults to 443)
    expect: open                # open | closed

auto_tests: true                # also generate policy, isolation (tcp/22 between unrelated segments) and egress tests

access:                         # optional remote access to the range
  wireguard:                    # a WireGuard tunnel that ends on the range router
    peers: [alice, bob]         # one config per person/device (lowercase names); keys are generated by VALOR
    reach: [dmz, lan]           # segments peers may reach (default: all segments)
    port: 51820                 # UDP port on the router's uplink (LAN) address, 1024-65535
    network: 10.250.0.0/24      # tunnel addresses (router = first); must not overlap the segments
    endpoint: ""                # host[:port] peers connect to, e.g. a port forward (default: the router's address)
```

## Windows hosts and Active Directory

- Windows hosts are configured over the QEMU guest agent (no cloud-init): name, static address, DNS and the range
  login (Administrator; on domain controllers the domain Administrator) are set by VALOR, then PowerShell roles run.
- Roles: `ad-dc` (new forest: `domain`, optional `netbios`), `ad-dc-replica` (`domain`, `dc`), `ad-member`
  (`domain`, `dc`, optional `dc2`), `iis`. A host whose role names another host in `dc`/`dc2` waits for it
  (domain controllers first, then members).
- Domain traffic needs a policy rule between the segments, e.g. `{from: users, to: srv, proto: any}`.
- Windows hosts get the `windows-l1` baseline (SMBv1 off, SMB signing, firewall, NLA, LLMNR off, audit, Defender);
  with `baseline: linux-moderate` they get `windows-moderate` (plus lockout, password history and complexity, logon
  notice, screen lock, audit policy, security log size, time sync - on domain controllers set as domain policy).

## Compliance frameworks

- `compliance.frameworks` makes VALOR validate the design for them and report verification evidence per requirement
  id (e.g. 800-171 3.1.8, 800-53 AC-7, PCI DSS 8.3.4): baseline controls on every host plus design checks -
  deny-by-default isolation, no internet in `compliance.scope`, and central logging (a `syslog-server` host with
  `syslog-client` on the other Linux hosts). Design gaps are warnings; a too-weak baseline is an error.
- A design for a framework: put the regulated data in its own segment(s) listed in `scope` without internet, allow
  only the flows the services need, add a log server and time server (`ntp-server`, `ntp-client` elsewhere), and
  keep administration on its own segment where it makes sense.
- Most requirements of every framework are organizational (policies, training, incident response); the report lists
  them as not covered. Never call a range "compliant" or "certified".
- Rocky/Alma hosts use the same roles as Ubuntu where the role lists `families: [debian, rhel]`.

## What the engine does with it

- **Router `rtr`**: NIC 0 on the uplink bridge (DHCP from the home LAN, NAT), one NIC per segment on the internal
  VLAN bridge with the segment's first address. nftables: input closed, forward default **drop**, one accept per
  policy rule, egress only for `internet: true` segments and never to private addresses.
- **Hosts**: linked clones of the OS template, one NIC on their segment's VLAN, static address, gateway = router,
  DNS 1.1.1.1 / 9.9.9.9, user `valor` with the engine's SSH key (break-glass only; management uses the guest agent).
- **Build order**: VMs -> temporary build egress for every segment (package installs) -> roles -> baseline ->
  router baseline -> **final policy last** -> WireGuard on the router (when `access.wireguard` is set).
- **WireGuard**: the router listens on `port` on its uplink address; peers' tunnel addresses may reach only the
  `reach` segments (no internet through the tunnel). Peer configs (with QR codes) are shown to operators in the
  range's Access tab; each view is audited, and an operator can give a peer new keys.
- **Stamps**: every VM has tags `valor`, `valor-range-<name>`, `valor-spec-<version>` and metadata in its notes
  (spec version, hardware hash, converged hash). Re-applying an unchanged spec changes nothing.

## Choosing addresses and VLANs

- Pick VLANs not listed in `cluster_info.vlans_in_use` and inside `segment_bridge.usable_vlans`.
- Keep segment networks distinct per range; `10.<vlan>.0.0/24` style is easy to read.
- Avoid `reserved_networks` (home LAN 192.168.1.0/24, cluster 10.10.10.0/24).
