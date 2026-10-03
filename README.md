# V.A.L.O.R. - MVP

A tool operated through **Claude Code** that turns a plain-language description of a test environment into a
segmented, hardened and verified set of VMs on the Proxmox VE cluster *valor*, and rebuilds that same environment
on demand. Implements the *V.A.L.O.R. Minimum Viable Product Specification v0.1* (Dan, Champlain College).

**Core principle: the agent plans, the engine executes.** Claude writes a declarative range spec (YAML); a
deterministic Python engine is the only component that calls the Proxmox API.

## Architecture

| Component | Responsibility | Implementation |
|---|---|---|
| Claude Code | agent interface, orchestration | operator account `valorop`, project `/srv/valor` |
| Skills | procedures and domain knowledge | `.claude/skills/{range-build,range-destroy,role-authoring,compliance,proxmox-reference}` |
| MCP server | engine operations as tools; long ops as background jobs | `valor/mcp_server.py` (MCP SDK 2.x `MCPServer`) |
| Engine | validate, plan, apply, verify, destroy | `valor/` (proxmoxer, Pydantic) |
| Range spec | source of truth per environment | `ranges/<name>.yaml` |
| Router VM | segmentation, policy, NAT | Ubuntu + nftables, created automatically per range |
| Guest management | config and tests inside VMs, no management network | QEMU guest agent |
| CLI | headless execution for CI/CD | `valor` (`valor/cli.py`) |
| Admin tool | templates, bridges, deployment (root) | `valor-admin` (`valor/admin.py`) |

MCP tools: `cluster_info`, `range_validate`, `range_plan`, `range_apply`, `range_verify`, `range_destroy`,
`job_status`, `guest_run`.

## How a build works

1. **Validate**: schema (Pydantic: names, addressing, overlaps, references) + cluster (templates, bridges, VLAN
   conflicts, reserved networks, roles, memory).
2. **Plan**: per VM `create / update / replace / converge / start / restamp / keep / remove`, plus totals vs free
   memory.
3. **Apply** (idempotent): linked clones of the OS template -> cloud-init networking + SSH key -> start -> guest agent
   -> router gets *temporary build egress* -> roles -> baseline -> router baseline -> **final policy last** -> every
   VM is stamped with the spec version and a converged-state hash. An unchanged spec re-applies with **zero changes**.
4. **Verify**: connectivity tests from inside the guests (explicit + automatic policy/isolation/egress tests) and
   every baseline check on every host.
5. **Journal**: `journals/<range>.md` - topology (Mermaid), hosts, network, policy, verification matrix, history.

Failures return a structured error: `error`, `message`, `step`, `host`, `details`, `hint`, `completed_steps`.
The agent fixes the spec/role/baseline and re-applies (max 3 attempts, per the `range-build` skill).

## Security model

- **Engine identity**: Linux user `valor` owns `/etc/valor` (config, API token, SSH key). The operator account
  `valorop` (Claude Code) cannot read it; it may only start the engine as `valor` through one sudo rule
  (`/etc/sudoers.d/valor`). Spec paths are confined to `ranges/` and parse errors never echo file content.
- **API token** `valor@pve!engine` (privilege-separated) is limited to:
  `/pool/valor` (VM lifecycle + guest agent), `/pool/valor-templates` (clone only), `/storage/valor-tank`
  (allocate), `/sdn/zones/localnetwork/vmbr100` and `/vmbr0` (bridge use), `/nodes/svr-02` (read status).
  It cannot see or touch any other VM (e.g. the VPN VM: HTTP 403).
- **Approvals**: `range_apply`, `range_destroy`, `guest_run` are `ask` rules in `.claude/settings.json`;
  `sudo`, `qm`, `pvesh`, `ssh`, reading `/etc/valor` and editing engine code are denied.
- **Isolation**: segments are VLANs on `vmbr100`, a bridge with **no physical port**; the router's uplink is NAT
  only, its input chain is closed, and "internet" egress excludes all private ranges (home LAN, cluster network).
- **Teardown** deletes only VMs in pool `valor` tagged `valor-range-<name>`.
- **Compliance claims**: results are "aligned with common CIS Level 1 themes", never "compliant" or "certified";
  control references come only from vendor/upstream documentation.

## Cluster resources (svr-02)

| Resource | Value |
|---|---|
| Storage | `valor-tank` = ZFS dataset `tank/valor`, quota 1 TB |
| Pools | `valor` (ranges), `valor-templates` |
| Segment bridge | `vmbr100` (VLAN-aware, no port); usable VLANs 100-3999 |
| Router uplink | `vmbr0` (home LAN, DHCP) |
| VMIDs | ranges 4000-4999; templates 9000+ |
| Templates | `templates/catalog.yaml` (ubuntu-24.04 built; ubuntu-26.04, debian-13, debian-12 on demand) |
| Reserved networks | 192.168.1.0/24 (home LAN), 10.10.10.0/24 (cluster) |

## Using it (operator)

Log in to svr-02 as **`valorop`** (a second SSH connection in the Claude desktop app), open `/srv/valor`, then:

```
/range-build a DMZ with an nginx web server exposed to the internet and an isolated LAN running PostgreSQL
             reachable only from the web server
```

Claude reads the cluster, writes `ranges/<name>.yaml`, validates, shows the plan, asks for approval, builds,
verifies and reports. `/range-destroy <name>` tears it down.

## CLI (CI/CD)

```
sudo -u valor /opt/valor/bin/valor validate web2tier.yaml
sudo -u valor /opt/valor/bin/valor plan web2tier.yaml [--show-policy]
sudo -u valor /opt/valor/bin/valor apply web2tier.yaml --verify
sudo -u valor /opt/valor/bin/valor verify web2tier.yaml
sudo -u valor /opt/valor/bin/valor destroy web2tier --yes
```

Exit codes: 0 ok, 1 invalid spec, 2 build failed, 3 verification failed, 4 engine busy, 5 other. `--json` for
machine output, `--background` to get a job id. Examples: `ci/github-actions.yml`, `ci/gitlab-ci.yml`.

## Administration (root)

```
valor-admin template list | build <os> [--force] | test <os>
valor-admin bridge create <name>      # port-less VLAN-aware bridge, with backup + 5-min rollback timer
valor-admin deploy                    # install engine from /srv/valor into /opt/valor/venv, wrappers, sudo rule
```

Engine config: `/etc/valor/config.toml`. Engine state and job logs: `/var/lib/valor/`.

## Development

```
cd /srv/valor && /opt/valor/venv/bin/python -m pytest      # unit tests, no cluster needed
```

Engine changes only take effect after `valor-admin deploy` (the operator cannot change the code the engine runs).

## Limitations (MVP scope)

- Ranges live on one node (svr-02): segment traffic stays on a port-less bridge by design.
- Linux cloud-init guests of the Debian family (Ubuntu, Debian). Windows, ISO installs, SDN, 802.1X, framework
  mappings, snapshots, multi-user: post-MVP (spec section 5.2 / 14).
- "Exposed to the internet" means internet egress; inbound port publishing is not implemented.
