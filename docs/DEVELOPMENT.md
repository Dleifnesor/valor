# Developing VALOR

How to set up, test and change VALOR. Read [ARCHITECTURE.md](ARCHITECTURE.md) first for where things live.

- [Rules](#rules)
- [Setup](#setup)
- [Tests](#tests)
- [The web UI](#the-web-ui)
- [Trying a change on a cluster](#trying-a-change-on-a-cluster)
- [Common changes](#common-changes)
- [Continuous integration](#continuous-integration)
- [The Claude Code harness](#the-claude-code-harness)
- [Evaluation](#evaluation)

## Rules

- **The agent plans, the engine executes.** AI output is a spec or a checked tool call; only the engine calls the
  Proxmox API. Keep that boundary in every feature.
- **Portable.** No node, storage, bridge, network or VMID in code, content or tests. The installer discovers them;
  tests use synthetic facts (`tests/test_installer.py`, `tests/test_portability.py`).
- **Secure by default.** Least privilege for the token; a person approves every build and destroy; every change is
  audited; secrets never go on a command line, into a VM config, a spec, a chat or a log. Every new API route needs a
  reviewed access level (see [below](#add-an-api-route)).
- **Idempotent.** Applying an unchanged spec makes zero changes. Roles and baseline fixes can run any number of times.
- **Honest compliance.** Results are "aligned with" a framework, never "compliant" or "certified".
- **Errors that help.** Raise `ValorError` with a code, the step, the host, the useful lines of output and a hint
  that says what to do.

## Setup

```bash
git clone https://github.com/Dleifnesor/valor && cd valor
python3 -m venv .venv
.venv/bin/pip install --require-hashes --no-deps -r appliance/requirements.lock
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest -q
```

Python 3.11+ works; the VALOR VM runs 3.12. The installer (`installer/`) must keep working with the standard library
only. CI checks that it imports nothing else.

## Tests

`pytest` runs without a cluster: Proxmox is replaced by fakes and the installer works on captured command output.

| File | Covers |
|---|---|
| `test_spec.py`, `test_policy_and_tests.py` | Spec schema and defaults, the nftables policy, automatic tests. |
| `test_plan_roles_baseline.py`, `test_apply_flow.py` | Desired state, plan classification, roles and baselines, the build order (build egress only while hosts converge). |
| `test_services.py` | Every role loads and is valid shell; generic roles; parameter handling. |
| `test_windows.py`, `test_compliance.py`, `test_wireguard.py` | Windows hosts, compliance frameworks and the report, WireGuard. |
| `test_lifecycle.py`, `test_editing.py`, `test_blueprints.py`, `test_isos.py` | Power, snapshots, logins; drafts and map edits; blueprints; the ISO library. |
| `test_web.py`, `test_security.py` | The web API: sign-in, MFA, sessions, users; access level of every route, CSRF and origin checks, headers, cookies. |
| `test_builder.py`, `test_chats.py` | AI provider settings, the validate-and-fix loop, budgets; saved chats, the range agent, approvals, read-only commands. |
| `test_installer.py`, `test_portability.py` | Installer parsing, defaults and validation; engine behavior that must not depend on any one cluster. |

Run them before every commit. Live checks against a real cluster (build a range, verify it, destroy it) are done by
hand or with the [harness](#the-claude-code-harness); describe what you ran in the pull request.

## The web UI

```bash
cd web
npm ci
npm run dev          # http://localhost:5173, proxies /api to a local valor-web on port 8080
npm run build        # type-check, then build into dist/
```

A local API for the dev server needs a config with `[web] secure_cookies = false` and a database path:

```bash
VALOR_CONFIG=dev.toml valor-web serve --port 8080
```

`web/dist` is committed: rebuild it and commit it together with the source. CI fails when it is out of date. Keep
the UI's plain style: neutral greys, one blue accent, flat panels, no gradients. Every form field has a `name`.

## Trying a change on a cluster

As root on the Proxmox node, from your checkout:

```bash
./install.sh --upgrade
```

It packs the working tree (engine, web service, built UI, roles, baselines, catalogs), waits for running jobs in
the VALOR VM, then installs it there over the guest agent. Data, accounts and settings are kept. The setup log
inside the VM is `/var/log/valor-setup.log`.

To test the installer itself, use a throwaway Proxmox VE: a nested Proxmox VE VM (a range host with `nested: true`) on
an isolated test network, as the portability test did ([ROADMAP #17](../ROADMAP.md)).

## Common changes

### Add an API route

1. Add it to the module's `router` with a dependency that states who may call it: `Depends(require("viewer" |
   "operator" | "admin"))`, or `stage(...)` for sign-in steps. A new router also goes into `API_ROUTERS` in
   `valor/web/app.py`.
2. Add the route to `ACCESS` in `tests/test_security.py`. Until you do, the tests fail: that is the review step.
3. Audit state changes (`s.audit(...)`), and never return secrets unless the route exists to show one (then add it to
   `SECRETS` in the same test file).
4. Add it to [API.md](API.md).

### Change the database

Append a script to `MIGRATIONS` in `valor/web/db.py`. Never edit an existing one: `migrate()` runs the scripts after
the database's `PRAGMA user_version`, at every start of the web service and the worker.

### Change the engine

- New spec fields go into `valor/spec.py` (strict models: unknown keys are errors), the
  [spec reference](../.claude/skills/range-build/spec-reference.md) and, when the cluster must be checked,
  `valor/validate.py`.
- Anything that changes a VM must show up in the plan: add it to `hw` (needs a reboot or rebuild) or to `conv_hash`
  (converges in place) in `valor/plan.py`. Otherwise an unchanged-spec apply cannot notice it, or notices it
  forever.
- Cluster calls go through `valor/pve.py`.
- If the token needs a new Proxmox privilege, add it in `installer/identity.py` (`./install.sh --upgrade` updates
  the roles) and to the table in [INSTALL.md](INSTALL.md).

### Add a role, a baseline control or an OS

See [CONTENT.md](CONTENT.md): [writing a role](CONTENT.md#writing-a-role), [baselines](CONTENT.md#baselines),
[adding an OS](CONTENT.md#adding-an-os). Map new controls to framework requirements in
`baselines/frameworks/frameworks.yaml` only where the mapping is real.

### Change what the AI is told

The chat builder's and range chat's prompts are assembled in `valor/ai.py` (`reference`, `SPEC_RULES`,
`builder_prompt`, `range_prompt`) and include
[`spec-reference.md`](../.claude/skills/range-build/spec-reference.md) verbatim. That file is also the Claude Code
harness's spec reference, so a change there reaches both. Keep it about the spec format and free of anything specific
to one cluster (the prompt adds this cluster's facts). After a prompt change, run a few builder requests against a
real model, including a small local one: what a large model ignores, a small one may follow literally.

The range agent's tools and the read-only command allowlist are in `valor/agent.py`. Read-only must stay
conservative: when in doubt, a command needs approval.

### Change Python dependencies

Edit `appliance/requirements.in` (exact pins), then `python3 tools/lock.py` regenerates
`appliance/requirements.lock` with hashes for every published file. The VALOR VM installs only from the lock
(`--require-hashes`).

### Change the installer

Standard library only, Python 3.11. Split parsing from running commands, so `tests/test_installer.py` can test it
with captured output. Anything the installer creates goes into the install record (`installer/record.py`), so
upgrade and uninstall know about it. Network changes need the backup and rollback timer of `installer/network.py`.

## Continuous integration

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on every pull request and on `main`:

- `python`: installs the locked dependencies, runs `pytest`, and checks that the installer imports only the
  standard library;
- `web`: `npm ci`, `npm run build`, and fails if `web/dist` differs from the committed build;
- `shell`: `bash -n` (and `shellcheck` as a warning) on `install.sh`, `appliance/setup.sh` and `appliance/valor-tls`.

Changes go through pull requests and are squash-merged.

## The Claude Code harness

The repository carries the MVP's Claude Code harness, still used to exercise the engine on the development cluster:

- [`.mcp.json`](../.mcp.json) starts `valor-mcp` (as the `valor` user) with the engine's tools; building, destroying
  and running commands in guests ask for approval ([`.claude/settings.json`](../.claude/settings.json)).
- Skills in `.claude/skills/`: `range-build` (with the spec reference), `range-destroy`, `role-authoring`,
  `compliance`, `proxmox-reference` (with `known-issues.md`, a log of solved build errors).
- `valor-admin` ([CLI.md](CLI.md#valor-admin-and-valor-mcp-development-cluster)) copies the checkout's engine into the
  harness (`valor-admin deploy`, as root).

[`CLAUDE.md`](../CLAUDE.md) holds the rules for Claude Code sessions in this repository.

## Evaluation

[`eval/run_metrics.py`](../eval/run_metrics.py) measures the MVP's success metrics for one or more specs: for each
cycle it destroys, plans, applies with verification (timed), applies again (must change nothing) and plans again
(must be all `keep`), then compares the plans of all cycles. Results go to `eval/results/` (not committed). It uses
the harness's engine (`sudo -u valor /opt/valor/bin/valor`).
