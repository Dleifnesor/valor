# Range spec reference (`apiVersion: valor/v1`)

```yaml
apiVersion: valor/v1            # optional, default valor/v1
name: web2tier                  # 2-15 chars: a-z, 0-9, '-'; starts with a letter. VM names become <name>-<host>
description: >                  # free text, shown in the journal
  What this range is for.
baseline: ubuntu-l1             # baselines/<id>.yaml, or 'none'
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
    os: ubuntu-24.04            # optional; must have a template (cluster_info.templates)
    cores: 1                    # 1-16
    memory: 1024                # MiB, 512-65536
    disk: 10                    # GiB, 8-500 (can grow later, never shrink)
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
```

## What the engine does with it

- **Router `rtr`**: NIC 0 on the uplink bridge (DHCP from the home LAN, NAT), one NIC per segment on the internal
  VLAN bridge with the segment's first address. nftables: input closed, forward default **drop**, one accept per
  policy rule, egress only for `internet: true` segments and never to private addresses.
- **Hosts**: linked clones of the OS template, one NIC on their segment's VLAN, static address, gateway = router,
  DNS 1.1.1.1 / 9.9.9.9, user `valor` with the engine's SSH key (break-glass only; management uses the guest agent).
- **Build order**: VMs -> temporary build egress for every segment (package installs) -> roles -> baseline ->
  router baseline -> **final policy last**.
- **Stamps**: every VM has tags `valor`, `valor-range-<name>`, `valor-spec-<version>` and metadata in its notes
  (spec version, hardware hash, converged hash). Re-applying an unchanged spec changes nothing.

## Choosing addresses and VLANs

- Pick VLANs not listed in `cluster_info.vlans_in_use` and inside `segment_bridge.usable_vlans`.
- Keep segment networks distinct per range; `10.<vlan>.0.0/24` style is easy to read.
- Avoid `reserved_networks` (home LAN 192.168.1.0/24, cluster 10.10.10.0/24).
