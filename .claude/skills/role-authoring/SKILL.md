---
name: role-authoring
description: Write or fix a VALOR service role (an idempotent script that installs and configures one service inside range VMs). Use when a range needs a service that has no role yet, or a role failed during a build.
---

# Writing a role

A role is `roles/<name>/role.sh` plus `roles/<name>/role.yaml`. The engine runs `role.sh` as **root** inside the
guest through the QEMU guest agent (stdin to `bash -s`), during build egress (the segment can reach public
mirrors). Look at `roles/nginx` and `roles/postgresql` first - copy their structure.

## role.yaml

```yaml
description: one line
families: [debian]          # apt-based OSes (Ubuntu, Debian) - the only family in the MVP
ports: [80]                 # informational: ports the service listens on
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
  `VALOR_GATEWAY`, and `VALOR_PARAM_<NAME>` for each parameter (upper-case).
- Already set: `set -euo pipefail`, `DEBIAN_FRONTEND=noninteractive`.
- Helpers:
  - `apt_install pkg...` - installs only missing packages, waits for apt locks (first-boot updates hold them),
    retries 3 times.
  - `write_file PATH MODE <<EOF ... EOF || true` - writes only when content differs (returns 1 when unchanged, hence
    `|| true`) and marks the run as changed.
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
