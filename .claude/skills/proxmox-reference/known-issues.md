# Known issues and fixes

Append an entry whenever a build error is resolved: symptom (error code / message), cause, fix, date.
Newest at the bottom. Keep entries short.

## apt lock held on first boot
- **Symptom**: role or baseline fails with `Could not get lock /var/lib/dpkg/lock-frontend`.
- **Cause**: Ubuntu runs apt-daily / unattended-upgrades right after first boot.
- **Fix**: built in - `apt_install` and `$APT` use `-o DPkg::Lock::Timeout=900` and retry. If it still happens,
  the guest probably has no internet during the build (check the router's build egress and DNS).

## `sshkeys` rejected by the API
- **Symptom**: HTTP 400 on `sshkeys` when configuring a VM.
- **Cause**: Proxmox expects the key list URL-encoded *inside* the form value.
- **Fix**: built in (`pve.update_config` encodes it). Keep in mind for any new code that sets `sshkeys`.

## MCP SDK 2.x API
- **Symptom**: `No module named 'mcp.server.fastmcp'`.
- **Cause**: mcp >= 2 renamed FastMCP to `MCPServer` (`from mcp.server.mcpserver import MCPServer`).
- **Fix**: the VALOR server uses `MCPServer`. (2026-10-02)

## `Missing privilege separation directory: /run/sshd` (baseline SSH controls fail)
- **Symptom**: `baseline_failed` on ssh-root-login / ssh-password-auth / ssh-max-auth-tries; details show
  `Missing privilege separation directory: /run/sshd`.
- **Cause**: Ubuntu 24.04 socket-activates ssh, so `/run/sshd` only exists while a connection is open; `sshd -T` and
  `sshd -t` refuse to run without it.
- **Fix**: the baseline runner creates `/run/sshd` before running controls (`valor/baseline.py`). (2026-10-02, first
  build of web2tier)

## Check fails intermittently with exit 141 (`cmd | grep -q` under `pipefail`)
- **Symptom**: a control or role check fails although the setting is correct (e.g. `ssh-password-auth` "fail fail"
  while `sshd -T` shows `passwordauthentication no`).
- **Cause**: `grep -q` exits at the first match; the writer (`sshd -T`, `ss`, `dpkg-query`) then gets SIGPIPE and,
  with `set -o pipefail`, the pipeline fails with 141 - about 9 times in 10 for `sshd -T`.
- **Fix**: the baseline runner no longer sets `pipefail`; roles use `grep ... >/dev/null` instead of `grep -q`.
  (2026-10-02, second build of web2tier)

## Baseline stops with "script error" after some controls
- **Symptom**: `baseline_failed ... script error`; the details list only the controls before the one that failed.
- **Cause**: a check used `exit 1`, which inside a shell function ends the whole baseline script.
- **Fix**: checks and fixes now run in their own subshell (`check_x() ( ... )`), so `exit` only ends that control.
  (2026-10-02, third build of web2tier)
