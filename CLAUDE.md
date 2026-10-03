# V.A.L.O.R. - operator project

This project turns plain-language descriptions of test environments into segmented, hardened, verified sets of
VMs on the Proxmox VE cluster "valor" - and rebuilds them on demand.

## The one rule: the agent plans, the engine executes

- You write **range specs** (`ranges/<name>.yaml`), **roles** (`roles/<name>/`) and **baselines** (`baselines/`).
- The **VALOR engine** is the only thing that touches Proxmox. You reach it through the `valor` MCP tools:
  `cluster_info`, `range_validate`, `range_plan`, `range_apply`, `range_verify`, `range_destroy`, `job_status`,
  `guest_run`.
- `range_apply`, `range_destroy` and `guest_run` always need the user's approval. Show the plan first.
- Never try to read credentials (`/etc/valor`), use `sudo`, `qm`, `pvesh` or SSH, or edit the engine code in
  `valor/` (engine changes are deployed by an administrator with `valor-admin deploy`).

## Skills

- `/range-build` - build or change a range from a description (main workflow).
- `/range-destroy <name>` - tear a range down.
- `role-authoring`, `compliance`, `proxmox-reference` (with `known-issues.md`) - knowledge used while building.

## Layout

| Path | What |
|---|---|
| `ranges/` | range specs (source of truth, under version control) |
| `roles/<name>/role.sh`, `role.yaml` | idempotent service roles (nginx, postgresql, ...) |
| `baselines/ubuntu-l1.yaml` | hardening baseline aligned with CIS Level 1 themes |
| `templates/catalog.yaml` | OS catalog (template VMIDs); templates are built by an administrator |
| `journals/<range>.md` | build journals written by the engine; add notes in `journals/<range>.notes.md` |
| `ci/` | example CI pipelines (same engine, no agent) |
| `valor/` | engine source (read-only for you) |

Compliance results are always described as **aligned with** CIS Level 1 themes - never compliant or certified.
