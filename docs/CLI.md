# VALOR command line

| Command | Where it runs | Who | For |
|---|---|---|---|
| [`./install.sh`](#installsh) | a Proxmox node | root | install, status, upgrade, more OS templates, uninstall |
| [`valor`](#valor) | the VALOR VM (or a development install) | root or `valor` | the engine without the web UI: CI pipelines and break-glass |
| [`valor-web`](#valor-web) | the VALOR VM | root | the web service, the worker, and account rescue |
| [`valor-admin`](#valor-admin-and-valor-mcp-development-cluster) | the development cluster | root | the MVP's administrator tasks |
| [`valor-mcp`](#valor-admin-and-valor-mcp-development-cluster) | the development cluster | `valor` | the engine as MCP tools for Claude Code |

From a Proxmox node you reach commands inside the VALOR VM over the guest agent, which works even when the network
does not:

```bash
qm guest exec <vmid> -- valor --json info
```

## install.sh

Runs as root on a Proxmox VE 8.2+ node, from a clone of the repository. Details and every setting:
[INSTALL.md](INSTALL.md).

| Command | Does |
|---|---|
| `./install.sh` | Interactive install with detected defaults. Run it again after a failure: it resumes. |
| `./install.sh --answers site.toml --yes` | Unattended install; missing settings get the detected default. |
| `./install.sh --dry-run --save-answers site.toml` | Show the plan and write every setting to a file; change nothing. |
| `./install.sh --status` | What is installed, and whether the web UI answers. |
| `./install.sh --upgrade [--yes]` | Push this checkout into the VALOR VM. Data is kept; waits for running jobs first. |
| `./install.sh --template <os> [--rebuild]` | Build another OS template, or replace one with a fresh build. |
| `./install.sh --uninstall [--purge-ranges]` | Remove VALOR; range VMs stay unless `--purge-ranges`. |

Other options: `--id <id>` (the instance id, default `valor`, for more than one installation on a cluster),
`--version`, `--help`.

## valor

The engine's command line. Inside the VALOR VM it is on the `PATH`; run as root it switches to the `valor` user by
itself. Specs are read from the ranges directory (`/var/lib/valor/data/ranges/` in the VALOR VM); a spec outside it is
refused.

```
valor [--json] info                          cluster facts for writing specs (templates, roles, VLANs in use, ...)
valor [--json] validate SPEC                 schema and cluster checks
valor [--json] plan SPEC [--show-policy]     what a build would do per VM (and the router's ruleset)
valor [--json] apply SPEC [--verify] [--background]
valor [--json] verify SPEC [--background]    connectivity tests and baseline checks
valor [--json] destroy RANGE --yes [--background]
valor [--json] journal SPEC                  write journals/<range>.md again
valor [--json] guest-run RANGE HOST [--timeout S] -- COMMAND...
valor [--json] job status ID | job list
```

`SPEC` is a file name in the ranges directory (`web2tier.yaml`). `--json` goes before the command and prints
machine-readable output. `--background` returns a job id at once (then `valor job status <id>`).

`apply` from the command line runs without a separate approval step: whoever can run it as `valor` is trusted.
Use the web UI where a person should approve each plan.

| Exit code | Meaning |
|---|---|
| 0 | ok |
| 1 | the spec is invalid |
| 2 | the build or teardown failed |
| 3 | verification failed |
| 4 | the engine is busy (another job holds the lock) |
| 5 | another error |

Example pipelines for GitHub Actions and GitLab CI are in [`ci/`](../ci/). They were written for the development
cluster's harness (a self-hosted runner next to `/srv/valor`); on an installed VALOR, run the same commands in the
VALOR VM.

## valor-web

Runs the web service and the worker (the systemd units call these), and rescues accounts. As root in the VALOR VM:

```
valor-web serve [--uds PATH | --host H --port N] [--static DIR]
valor-web worker
valor-web migrate                                    create or upgrade the database schema
valor-web create-admin --username NAME --password-file FILE [--display-name TEXT]
valor-web create-user --username NAME --role admin|operator|viewer --password-file FILE [--display-name TEXT]
valor-web list-users
valor-web delete-user USER
valor-web unlock USER                                clear a lockout
valor-web reset-mfa USER                             set up MFA again at the next sign-in
valor-web reset-password USER --password-file FILE   the user must change it at the next sign-in
```

Break-glass from the Proxmox node, when nobody can sign in:

```bash
qm guest exec <vmid> -- valor-web unlock admin
qm guest exec <vmid> -- valor-web reset-mfa admin
```

## valor-admin and valor-mcp (development cluster)

These belong to the MVP's development harness, where the engine is installed under `/opt/valor` on a Proxmox node
next to a checkout in `/srv/valor`. An installed VALOR does not use them: the installer does their work.

```
valor-admin template list | template build <os> [--force] | template test <os>
valor-admin bridge create <name> [--node NODE] [--comment TEXT]
valor-admin deploy                       copy the checkout's engine into /opt/valor
```

`valor-mcp` exposes the engine to Claude Code (configured in [`.mcp.json`](../.mcp.json)). Tools: `cluster_info`,
`range_validate`, `range_plan`, `range_apply`, `range_verify`, `range_destroy`, `job_status` and `guest_run`.
`range_apply`, `range_destroy` and `guest_run` ask the developer for approval
([`.claude/settings.json`](../.claude/settings.json)).
