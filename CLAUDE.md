# V.A.L.O.R. - development project

VALOR turns descriptions of test environments into segmented, hardened, verified sets of VMs on Proxmox VE. Users
install it with `./install.sh` on any Proxmox node and work in its web UI (see `README.md`, `ROADMAP.md`).
**Claude Code in this repository is for developing VALOR**, not for operating it.

## Rules

- **The agent plans, the engine executes.** In the product, AI output is a range spec; only the engine (`valor/`)
  calls the Proxmox API. Keep that boundary in every feature.
- **Portable.** Nothing may assume this development cluster: no node, storage, bridge, network or VMID in code.
  The installer discovers them; tests use synthetic facts (`tests/test_installer.py`, `tests/test_portability.py`).
- **Secure by default.** Least privilege for the API token, approvals for every build/destroy, no secrets in logs,
  specs or chat. Never read `/etc/valor` (credentials).
- Don't touch the cluster directly (`sudo`, `qm`, `pvesh`, `ssh` are denied here). Exercise the engine through the
  `valor` MCP tools (`range_apply`, `range_destroy` and `guest_run` need the user's approval), and leave
  installer runs to an administrator (root).

## Layout

| Path | What |
|---|---|
| `installer/`, `install.sh` | the installer (Python standard library only; runs on Proxmox nodes) |
| `appliance/` | files used inside the VALOR VM: `setup.sh`, nginx, nftables, systemd units, `requirements.lock` |
| `valor/` | engine (validate, plan, apply, verify, destroy, journal) and CLI |
| `valor/web/` | web API (FastAPI): auth + MFA + LDAP, sessions, CSRF, audit, settings, ranges, jobs, worker |
| `web/` | web UI (React + Vite + React Flow); `web/dist` is built and committed |
| `roles/`, `baselines/`, `templates/` | content shipped with VALOR |
| `ranges/` | example range specs (the development harness reads them here) |
| `tests/` | `pytest`: engine, web API, installer logic - no cluster needed |
| `docs/INSTALL.md` | install guide and answers-file reference |
| `tools/lock.py` | regenerates `appliance/requirements.lock` |

## Workflow

```bash
.venv/bin/python -m pytest              # always before committing
cd web && npm run build                 # after UI changes; commit dist/ with the source
```

Engine changes reach the development harness after an administrator runs `valor-admin deploy`, and an installed
VALOR VM after `./install.sh --upgrade` (both root).

## Skills (engine knowledge; packaged for the in-product agent in milestone 2)

- `/range-build`, `/range-destroy <name>` - build or tear down a range through the MCP tools.
- `role-authoring`, `compliance`, `proxmox-reference` (with `known-issues.md`).

Compliance results are always described as **aligned with** a framework's themes - never compliant or certified.
