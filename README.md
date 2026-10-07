# V.A.L.O.R.

Turns a description of a test environment into a **segmented, hardened and verified** set of VMs on Proxmox VE,
and rebuilds that same environment on demand. Champlain College capstone (Dan). Where this is going:
[ROADMAP.md](ROADMAP.md).

**Core principle: the agent plans, the engine executes.** The AI only writes declarative range specs. A
deterministic Python engine is the only component that calls the Proxmox API, and a person approves every build
and every teardown.

## Install (any Proxmox VE 8.2+ / 9 cluster)

```bash
git clone https://github.com/Dleifnesor/valor && cd valor
./install.sh          # as root on the Proxmox node that should host VALOR
```

The installer discovers the cluster and asks only what it must, always with a detected default. It creates a
least-privilege API identity, an isolated range network (with an automatic rollback), verified OS templates and
the **VALOR VM**, provisioned entirely over the QEMU guest agent. Then open `https://<valor-vm>/` from the LAN or
a VPN and sign in. The first password is in `/root/valor-<id>-credentials.txt` on the node, and two-factor
authentication is set up at the first sign-in. Unattended installs, every setting, upgrade and uninstall:
[docs/INSTALL.md](docs/INSTALL.md).

## What the web UI does today

| Page | |
|---|---|
| Dashboard | health checks: Proxmox API, token scope, templates, range network, certificate, disk, OS updates |
| Ranges | every range with its build and verification status |
| Range | **topology map** (React Flow): add VMs, services and network segments (presets: DMZ, users, servers, management, attacker lab, isolated), edit traffic rules, resize or remove VMs right on the map - changes collect as a draft shown in the plan colors and are built through Review plan → Approve; a **Chat** panel on the map answers questions (Question), proposes changes as a draft with VALOR's plan (Plan) or edits the YAML with a diff (Code), kept per range; hosts, tests, verification matrix, spec, journal, history |
| Consoles | every VM's screen (noVNC) or serial console (xterm.js) in the browser, through VALOR (operators, audited) |
| Login | one generated password per range for the VM consoles (Linux `valor`, Windows `Administrator` / `DOMAIN\Administrator`), encrypted at rest, shown to operators, audited, rotatable |
| Power, snapshots | start / shut down / reboot a range or one VM; snapshots, automatic `valor-clean` after each verified build, reset to a snapshot |
| Access | WireGuard per range on the range router: peer configs with QR codes, live connection status, new keys per peer |
| ISO library | download from a verified catalog (signed checksums), by URL, upload from the browser, delete; attach an ISO to a host (`iso:`) or install a Rocky/Alma host from its ISO (`install: iso`) |
| Blueprints | keep a spec as a blueprint and deploy copies - numbered or one per student - each with its own VLANs, networks and WireGuard peer |
| Chat builder | describe an environment; an AI model (Anthropic Claude or any OpenAI-compatible endpoint, set up by an admin) writes the spec, VALOR validates it and shows it on the map; building stays plan → approve |
| Spec editor | write or paste a range spec, check it against the cluster, see it on the map |
| Plan → approve | the plan is shown on the map in color (added / changed / rebuilt / removed). Approving runs exactly that plan. |
| Jobs | builds, verifications and teardowns with their live step log |
| Users, audit log | roles admin / operator / viewer; every change is audited |
| Settings | notification channels (email, Discord, Slack, Teams, in-app), LDAP / Active Directory, certificate |

## How a build works

1. **Validate**: schema (names, addressing, overlaps, references) + cluster (templates, bridges, VLAN conflicts,
   reserved networks, roles, memory).
2. **Plan**: per VM `create / update / replace / converge / start / restamp / keep / remove`, plus totals against
   free memory.
3. **Apply** (idempotent): linked clones of the OS template -> cloud-init networking -> guest agent -> temporary
   build egress -> roles -> hardening baseline -> **final policy last** -> every VM is stamped with the spec version.
   An unchanged spec re-applies with **zero changes**.
4. **Verify**: connectivity tests from inside the guests (explicit + automatic policy/isolation/egress tests) and
   every baseline check on every host.
5. **Journal**: one Markdown page per range: topology, hosts, network, policy, verification matrix, history.

## Architecture

| Component | Where | What |
|---|---|---|
| Installer | `install.sh`, `installer/` | Runs on a Proxmox node: Python standard library only |
| VALOR VM | Ubuntu 24.04, `appliance/` | nginx (TLS) -> web API (FastAPI, unix socket) + job worker. SQLite. nftables. |
| Web UI | `web/` | React + TypeScript + Vite + React Flow; `web/dist` is committed |
| Web API | `valor/web/` | accounts, MFA, LDAP, sessions, CSRF, audit, settings, ranges, plans, jobs |
| Engine | `valor/` | validate, plan, apply, verify, destroy, journal (proxmoxer, Pydantic) |
| Content | `roles/`, `baselines/`, `templates/` | idempotent service roles, a hardening baseline, the OS catalog |
| Range router | per range, automatic | Ubuntu + nftables: segmentation, policy, NAT |

## Security model

- **Proxmox**: one privilege-separated token. VM rights end at the range pool; it can clone only from the template
  pool and can allocate only on the range storage and bridges. It gets HTTP 403 on everything else, **including
  the VALOR VM itself**. The installer checks this.
- **VALOR VM**: default-deny firewall: HTTPS only from the LAN/VPN networks chosen at install, SSH (keys only)
  only from the Proxmox nodes. Hardened systemd units with an unprivileged user. Hash-pinned Python packages.
  Automatic security updates.
- **Web**: TLS 1.2+, HSTS, strict CSP. Argon2id passwords; mandatory TOTP with replay protection and recovery
  codes. `__Host-` cookies (HttpOnly, Secure, SameSite=Strict) with idle/absolute timeouts; CSRF tokens + Origin
  checks; rate limits and lockout; an audit log of every change. Stored secrets are encrypted with AES-256-GCM.
- **Ranges**: segments sit on a bridge with no physical port. Routers deny everything not in the spec, and
  "internet" egress never reaches private addresses or the cluster's own networks. WireGuard peers reach only
  the segments listed in `reach` - never the internet or the LAN - and their keys are generated and encrypted by
  VALOR; viewing a peer config or a range login is audited.
- **AI**: the chat builder's model only writes text. Its spec is validated, and building it still needs a person
  to plan and approve. The provider key is encrypted, never shown again, and budgets limit tokens per user.
- **Tests**: `tests/test_security.py` checks that every API route has a reviewed access level, that sign-in is
  required everywhere, and that every state change needs the CSRF token, the same origin and a JSON body.
- **Compliance claims**: results are "aligned with" common CIS Level 1 themes or the selected frameworks' technical
  requirements (NIST SP 800-171, NIST SP 800-53, PCI DSS, HIPAA), never "compliant" or "certified".

## Range specs, roles and baselines

A range spec (YAML) lists segments (VLAN + private CIDR, optional internet egress), hosts (OS, size, roles), the
allowed traffic between segments and optional tests. Examples are in `ranges/`. Roles (`roles/<name>/role.sh` +
`role.yaml`) are idempotent and parameterized. The baseline `baselines/ubuntu-l1.yaml` has check + fix
controls. See `.claude/skills/range-build/spec-reference.md` for the full format.

## Engine CLI (CI/CD and break-glass)

Inside the VALOR VM (`valor ...`) or a development install:

```
valor validate web2tier.yaml | plan web2tier.yaml [--show-policy] | apply web2tier.yaml --verify
valor verify web2tier.yaml | destroy web2tier --yes | journal web2tier | job <id>
```

Exit codes: 0 ok, 1 invalid spec, 2 build failed, 3 verification failed, 4 engine busy, 5 other. `--json` for
machine output. Example pipelines: `ci/`.

## Development

Claude Code in this repository is for developing VALOR (see `CLAUDE.md`).

```bash
python3 -m venv .venv && .venv/bin/pip install -r appliance/requirements.lock pytest httpx
.venv/bin/python -m pytest                 # engine, web API and installer logic; no cluster needed
cd web && npm ci && npm run build          # web UI (see web/README.md); commit web/dist
python3 tools/lock.py                      # after changing appliance/requirements.in
./install.sh --upgrade                     # push the working tree into an installed VALOR VM
```

The MVP's Claude Code harness (MCP server, skills, `valor-admin`) still works on the development cluster. It is
how the engine is exercised during development.

## Limitations (today)

- Ranges live on one node; multi-node ranges through Proxmox SDN are milestone 5.
- Range routers get their uplink address from the LAN's DHCP (NAT through the VALOR VM: issue #15).
- Guests: Ubuntu 24.04, Debian 13, Kali, Rocky Linux 10, AlmaLinux 10, Windows Server 2022/2025 (Desktop and
  Core) and Windows 11 Enterprise (evaluation media; bring your own license for longer use). Firewall appliances
  are milestone 4.
- "Exposed to the internet" means internet egress; inbound port publishing is not implemented.
