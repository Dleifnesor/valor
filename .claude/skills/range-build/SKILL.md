---
name: range-build
description: Build (or rebuild/converge) a VALOR test range on the Proxmox cluster from a plain-language description - e.g. "a DMZ with nginx and an isolated LAN with PostgreSQL". Use when the user asks to create, build, change or rebuild a test environment, lab or range, or runs /range-build.
---

# Build a range

You turn the user's description into a **range spec** (`/srv/valor/ranges/<name>.yaml`) and let the VALOR
engine build it. **You never change infrastructure directly** - no `qm`, `pvesh`, `ssh` or API calls. All changes
go through the MCP tools of the `valor` server. Read `spec-reference.md` (next to this file) for the format.

## Procedure

1. **Read the cluster.** Call `cluster_info`. Note free memory, available OS templates (`templates[*].present`),
   VLANs already in use, existing ranges, available roles and baselines.
2. **Design.** Map the description to segments, hosts, services (roles) and policy:
   - One segment per trust zone (e.g. dmz, lan, attacker). Each gets its own unused VLAN in the usable range and a
     private `/24` that does **not** overlap `reserved_networks` (home LAN / cluster). Convention: VLAN `1N0` ↔
     `10.1N0.0.0/24`.
   - `internet: true` only for segments the user wants online (egress to public addresses; private networks stay
     blocked). "Exposed to the internet" in a description means internet-connected (egress) in the MVP.
   - OS: use what the user asked for if a template exists; otherwise omit `os` (engine default, Ubuntu 24.04). If the
     user asks for an OS without a template, tell them an administrator must run `valor-admin template build <os>`.
   - Services only via roles that exist (`cluster_info.roles`). If one is missing, write it first (role-authoring skill).
   - Policy: list only the flows the user wants. Everything else is denied by default. Rules between hosts in the
     same segment are invalid (not routed).
   - Tests: add explicit tests for the key promises in the description (what must work, what must be blocked).
     Automatic tests (policy, isolation, egress) are added by the engine.
3. **Write the spec** to `/srv/valor/ranges/<name>.yaml` (range name: 2-15 chars, lowercase, digits, dashes).
4. **Validate.** `range_validate`. Fix every error in the spec and validate again until `ok`.
5. **Plan.** `range_plan`. Present to the user: hosts with segment, IP, OS and services; segments with VLAN and
   network; the policy; resource totals vs free memory; the tests that will run. **Ask for approval.** Do not apply
   without an explicit yes.
6. **Build.** `range_apply` (verify defaults to true) returns a job id. Poll `job_status` every ~20-30 s and give
   the user short progress updates (current step).
7. **On failure** the result has `error`, `step`, `host`, `details`, `hint` and `completed_steps`.
   - Diagnose from `details`; use `guest_run` (needs approval) only to inspect, never to fix.
   - Fix the cause **in the spec, the role script or the baseline** - never by hand - and re-apply. The build is
     idempotent: finished parts are kept.
   - Maximum **3 fix-and-reapply attempts**. Then stop and report what failed, what you tried and what you suggest.
   - When you resolve a new kind of error, add it to `.claude/skills/proxmox-reference/known-issues.md`.
8. **Verify and report.** The apply job's result includes `verify`; if you applied without it, run `range_verify`.
   Report: tests passed/total (with failures explained), isolation tests, baseline controls passed per host - and
   say the baseline is *aligned with* CIS Level 1 themes, never "compliant" or "certified".
9. **Journal.** The engine writes `journals/<name>.md`. If you fixed something, append a short note to
   `journals/<name>.notes.md` (`- <date> <what failed> -> <fix>`). Tell the user where the journal is.

## Changing an existing range

Edit its spec and run the same procedure. The plan shows `update` (CPU/RAM/disk, reboot), `replace` (OS, network or
address changed: VM rebuilt), `converge` (roles/baseline/policy changed, no reboot), `keep`, `remove`.
Re-applying an unchanged spec must show **no changes** - if it does not, that is a bug to report.

## Rules

- Infrastructure changes only through `range_apply` / `range_destroy` after user approval.
- Never try to read `/etc/valor` (credentials) - the engine holds them, you do not.
- Keep specs reproducible: no random values; everything the range needs is in the spec, roles and baseline.
