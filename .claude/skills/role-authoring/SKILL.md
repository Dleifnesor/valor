---
name: role-authoring
description: Write or fix a VALOR service role (an idempotent script that installs and configures one service inside range VMs). Use when a range needs a service that has no role yet, or a role failed during a build.
---

# Writing a role

A role is `roles/<name>/role.yaml` plus `role.sh` (Linux) and/or `role.ps1` (Windows). The engine runs the script
inside the guest through the QEMU guest agent - `role.sh` as **root** (bash, script on stdin), `role.ps1` as SYSTEM -
during build egress (the segment can reach public mirrors). Look at `roles/nginx` and `roles/postgresql` first (and
`roles/ad-member` for Windows) - copy their structure. Full reference with every key and helper:
`docs/CONTENT.md` (Writing a role).

## role.yaml

```yaml
description: one line
families: [debian, rhel]    # debian (Ubuntu, Debian, Kali) | rhel (Rocky, Alma) | windows
ports: [80]                 # TCP ports the service listens on (or ports_param: <param> to take them from a param)
params:                     # every parameter the script reads; unknown params in a spec are rejected
  port:
    default: 80
    description: ...
  allow_from:
    required: false         # set true if the spec must provide it
    default: ""
    description: host or segment names; the engine resolves them to address/prefix (space separated)
```

## role.sh contract

- Environment: `VALOR_RANGE`, `VALOR_HOST`, `VALOR_ROLE`, `VALOR_ADDRESS`, `VALOR_SEGMENT`, `VALOR_SEGMENT_CIDR`,
  `VALOR_GATEWAY`, `VALOR_RANGE_HOSTS`, `VALOR_NAMESERVERS`, `VALOR_FAMILY` (debian | rhel), and
  `VALOR_PARAM_<NAME>` for each parameter (upper-case).
- Already set: `set -euo pipefail`, `DEBIAN_FRONTEND=noninteractive`.
- Helpers:
  - `pkg_install deb-pkg... [-- rhel-pkg...]` - installs only missing packages with apt or dnf, waits for apt locks
    (first-boot updates hold them), retries 3 times (`apt_install`, `dnf_install` also exist).
  - `write_file PATH MODE <<EOF ... EOF` - writes only when content differs and marks the run as changed. Feed it a
    heredoc or `< <(cmd)`, never `cmd | write_file` (a subshell: the change would not count).
  - `firewall_open PORT`, `selinux_port TYPE PORT`, `wait_port PORT`, `run_container NAME IMAGE [opts]` - see
    `docs/CONTENT.md`.
  - `changed` - mark that something changed (e.g. after creating a DB user).
  - `$VALOR_CHANGED` - `1` if anything changed in this run; restart/reload services only then.
- **Idempotent**: running twice must leave the system the same and change nothing the second time. Check before
  you create (users, databases, symlinks); never append blindly to config files.
- **Validate inputs** you put into config files or SQL (`[[ "$X" =~ ^...$ ]]`) and fail with a clear message on
  stderr.
- **End with a check** that proves the service works (e.g. `systemctl is-active -q nginx` and
  `ss -ltn "sport = :80" | grep -q LISTEN`). Exit non-zero on failure with a message on stderr - the engine returns
  the last lines to you.
- No secrets in the spec. Generate them on the host (`/root/.valor-...`, mode 600) like the postgresql role does.

## Testing a new role

Add it to a host in a spec, `range_validate`, `range_plan`, apply after approval. A role change makes the plan show
`converge` for the hosts using it (no reboot). Remember the router policy must allow the traffic the service needs.
