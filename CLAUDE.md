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
- Don't touch the cluster directly (`sudo`, `qm`, `pvesh`, `ssh` are denied here). Try changes in an installed
  VALOR (its web UI or the `valor` CLI inside the VALOR VM), and leave installer runs (`./install.sh --upgrade`)
  to an administrator (root).

## Layout

| Path | What |
|---|---|
| `installer/`, `install.sh` | the installer (Python standard library only; runs on Proxmox nodes) |
| `appliance/` | files used inside the VALOR VM: `setup.sh`, nginx, nftables, systemd units, `requirements.lock` |
| `valor/` | engine (validate, plan, apply, verify, destroy, journal), the AI client and the `valor` CLI |
| `valor/web/` | web API (FastAPI): auth + MFA + LDAP, sessions, CSRF, audit, settings, ranges, jobs, worker |
| `web/` | web UI (React + Vite + React Flow); `web/dist` is built and committed |
| `roles/`, `baselines/`, `templates/` | content shipped with VALOR |
| `examples/` | example range specs (shipped with VALOR; the tests use them too) |
| `tests/` | `pytest`: engine, web API, installer logic - no cluster needed |
| `ci/`, `eval/` | example CI pipelines and the success-metrics script (both run inside the VALOR VM) |
| `docs/` | install, user, architecture, API, CLI, content and development guides (index: `docs/README.md`) |
| `tools/lock.py` | regenerates `appliance/requirements.lock` |

`valor/spec-reference.md` is the spec format for people **and the product's AI prompt** (the chat builder and range
chats include it verbatim): keep it about the spec format and free of anything specific to this cluster.

## Workflow

```bash
.venv/bin/python -m pytest              # always before committing
cd web && npm run build                 # after UI changes; commit dist/ with the source
```

When behavior, a setting, a route or a command changes, update the matching page in `docs/` in the same change
(new API routes: `docs/API.md` and `ACCESS` in `tests/test_security.py`).

Changes reach an installed VALOR VM after an administrator runs `./install.sh --upgrade` (root).

## Skills (engine knowledge; to ship with the in-product agent, ROADMAP #28)

- `role-authoring`, `compliance`, `proxmox-reference` (with `known-issues.md`).

Compliance results are always described as **aligned with** a framework's themes - never compliant or certified.
