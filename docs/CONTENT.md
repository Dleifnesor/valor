# Roles, baselines and catalogs

What VALOR ships to build ranges with, and how to add to it: service **roles**, hardening **baselines**, the
**compliance framework** mapping, and the **OS and ISO catalogs**. Specs use these by name
([spec reference](../valor/spec-reference.md)).

In the VALOR VM this content lives in `/opt/valor/share/` and is replaced by `./install.sh --upgrade`. Change it in
the repository, not in the VM.

- [Roles](#roles)
- [Writing a role](#writing-a-role)
- [Baselines](#baselines)
- [Compliance frameworks](#compliance-frameworks)
- [OS catalog](#os-catalog)
- [ISO catalog](#iso-catalog)

## Roles

A role installs and configures one service on a host. `families` says which OS families it supports: `debian`
(Ubuntu, Debian, Kali), `rhel` (Rocky, Alma) and `windows`. Parameters marked * are required. Each role's
`role.yaml` describes every parameter.

| Role | Families | What it does | Parameters |
|---|---|---|---|
| `nginx` | debian, rhel | nginx with a page naming the host and range, or a reverse proxy (`proxy_to`) | `port`, `server_name`, `proxy_to`, `proxy_port` |
| `apache` | debian, rhel | Apache HTTP server with a page naming the host and range | `port` |
| `postgresql` | debian, rhel | PostgreSQL on the segment address, reachable only from the listed clients | `allow_from`, `database`, `user` |
| `mariadb` | debian, rhel | MariaDB with one database and user, reachable from the listed clients | `allow_from`, `database`, `user` |
| `redis` | debian | Redis on the segment address (no password), optionally limited to clients | `port`, `allow_from` |
| `dns-server` | debian, rhel | BIND with a zone naming every host of the range; forwards other queries | `zone`, `allow_from`, `forwarders` |
| `dhcp-server` | debian, rhel | dnsmasq DHCP for its own segment (for extra clients; range hosts keep fixed addresses) | `range_start`, `range_end`, `lease`, `dns` |
| `mail-server` | debian, rhel | Postfix accepting mail for the range's domain from range hosts | `domain`, `allow_from` |
| `ftp-server` | debian, rhel | vsftpd for local users, optionally anonymous read-only | `anonymous` |
| `samba-share` | debian, rhel | SMB share at `/srv/share/<share>` for the user `share` | `share`, `guest`, `writable` |
| `ntp-server` | debian, rhel | chrony time server for the range (keeps serving without internet) | `allow_from`, `upstream` |
| `ntp-client` | debian, rhel, windows | Take time from the range's time server | `server`* |
| `syslog-server` | debian, rhel | rsyslog receiving on TCP/UDP 514, stored per host under `/var/log/remote/<host>/` | `allow_from` |
| `syslog-client` | debian, rhel | Forward logs to the range's log server over TCP 514 | `server`* |
| `container` | debian, rhel | Any container image as a service (Docker on Debian family, Podman on Rocky/Alma) | `image`*, `name`, `publish`, `env` |
| `juice-shop` | debian, rhel | OWASP Juice Shop, a deliberately insecure web shop (container) | `port` |
| `dvwa` | debian, rhel | DVWA, a deliberately vulnerable PHP/MySQL app (container) | `port` |
| `attacker-tools` | debian, rhel | nmap, netcat, tcpdump, curl, dig | `extra_packages` |
| `dev-tools` | debian, rhel | git, build tools, Python virtual environments, tmux, vim | `extra_packages` |
| `packages` | debian, rhel | *Generic*: any distribution packages, their services enabled and ports opened | `packages`*, `rhel_packages`, `services`, `ports`, `epel` |
| `custom-script` | debian, rhel, windows | *Generic*: your own idempotent script (bash as root, PowerShell as SYSTEM). Shown in full on the plan before approval. | `script`*, `ports` |
| `ad-dc` | windows | Domain controller for a new forest (AD DS + DNS) | `domain`*, `netbios` |
| `ad-dc-replica` | windows | Additional domain controller for an existing domain | `domain`*, `dc`* |
| `ad-member` | windows | Join an Active Directory domain | `domain`*, `dc`*, `dc2` |
| `windows-dhcp` | windows | Windows Server DHCP for its own segment | `range_start`, `range_end`, `dns`, `lease_hours` |
| `windows-file-share` | windows | SMB share at `C:\Shares\<share>` | `share`, `read_only` |
| `iis` | windows | IIS with a page naming the host and range | |
| `windows-features` | windows | *Generic*: Windows Server roles and features, or Windows capabilities | `features`, `capabilities`, `services`, `ports` |

Parameters that name hosts or segments (`allow_from`, `server`, `dc`, `proxy_to`...) take range names: a host name
becomes its address (`/32`), a segment name its network. Remember the policy: a service is only reachable from
another segment if a policy rule allows it.

## Writing a role

A role is a directory `roles/<name>/` with `role.yaml` and `role.sh` (Linux), `role.ps1` (Windows) or both. The
engine runs the script inside the guest over the QEMU guest agent (bash as root, PowerShell as SYSTEM) while the
range still has its temporary build internet access. Start from a similar role (`nginx` and `postgresql` are good
models; `ad-member` for Windows).

### role.yaml

```yaml
description: >                 # one or two sentences; also what the AI reads about the role
  PostgreSQL server on the host's segment address, reachable only from the listed clients
families: [debian, rhel]       # debian | rhel | windows
ports: [5432]                  # TCP ports the service listens on (the map and the AI use this) ...
# ports_param: port            # ... or: take the ports from this parameter
# category: generic            # for packages / custom-script style roles
# wait_for: [dc]               # converge after the host named in this parameter (domain members after their DC)
params:                        # every parameter the script reads; unknown parameters in a spec are rejected
  allow_from:
    default: ""
    description: host or segment names; resolved to address/prefix (space separated)
  database:
    default: app
    raw: true                  # pass as written (no host/segment name resolution)
    pattern: '^[a-z_][a-z0-9_]{0,30}$'     # every word must match, or the build stops with role_param_invalid
    max_length: 31
    description: database name
  # required: true             # the spec must set it
```

### role.sh (Linux)

The script gets `set -euo pipefail` and these variables: `VALOR_RANGE`, `VALOR_HOST`, `VALOR_ROLE`,
`VALOR_ADDRESS`, `VALOR_SEGMENT`, `VALOR_SEGMENT_CIDR`, `VALOR_GATEWAY`, `VALOR_RANGE_HOSTS` (`name=address ...`),
`VALOR_NAMESERVERS`, `VALOR_FAMILY` (`debian` or `rhel`) and `VALOR_PARAM_<NAME>` for each parameter.

| Helper | Does |
|---|---|
| `pkg_install DEB... [-- RHEL...]` | Install missing packages with apt or dnf (waits for apt locks, retries). Without `--` the same names are used for both families. `apt_install` and `dnf_install` also exist. |
| `write_file PATH [MODE]` | Write stdin to PATH only when the content differs, and record the change. Feed it a heredoc or `< <(cmd)`, never `cmd \| write_file` (a pipeline runs it in a subshell, so the change would not count). |
| `changed` | Record that something changed (after creating a user or database, for example). |
| `$VALOR_CHANGED` | `1` when something changed in this run: restart or reload services only then. |
| `firewall_open PORT [tcp\|udp]` | Open a port in firewalld if it runs (Rocky/Alma); nothing elsewhere. |
| `selinux_port TYPE PORT` | Allow a service on a non-standard port when SELinux is enforcing. |
| `wait_port PORT [SECONDS]` | Wait until something listens on a TCP port. |
| `run_container NAME IMAGE [options...]` | Create or update a container (Docker or Podman), only when its settings changed; restarted on boot. |

Rules:

- **Idempotent.** A second run must change nothing. Check before creating users, databases or links; never append to
  a config file blindly.
- **Restart after a change.** `systemctl enable --now` does not restart a service that is already running.
- **Validate inputs** you put into config files or SQL (`[[ $X =~ ^...$ ]]` or a `pattern` in `role.yaml`) and fail
  with a clear message on stderr.
- **End with a check** that proves the service works (`systemctl is-active -q`, `ss -ltn "sport = :5432"`). The last
  lines of output are what the build error shows.
- Under `pipefail`, write `cmd | grep x >/dev/null`, not `grep -q`: an early-exiting `grep -q` can kill the writer
  with SIGPIPE and fail the pipeline.
- **No secrets in the spec.** Generate them on the host (mode 600 under `/root`), as `postgresql` and `samba-share`
  do.

### role.ps1 (Windows)

The script runs as SYSTEM with the same values as PowerShell variables (`$VALOR_HOST`, `$VALOR_PARAM_DOMAIN`...),
plus `$VALOR_LOGIN_PASSWORD` (the range login, for example as the domain Administrator's password). Helpers:
`Changed`, `Write-RebootRequired` (VALOR reboots the host and runs the role again), `Open-TcpPort`, `Wait-Port`,
`Wait-Until`, `Set-DnsByRole` (point DNS at a domain controller and keep it there), `Get-Words`.

### Testing a role

Add it to a host in a spec, then plan and build. A changed role makes the plan show `converge` for the hosts that use
it (no reboot). [`tests/test_services.py`](../tests/test_services.py) checks that every role loads and is valid
shell; add a test there for anything special (parameter handling, generated config).

## Baselines

A baseline is a list of controls, each with a `check` and an idempotent `fix`. During the build every control runs
its check; if it fails, the fix runs and the check runs again, and a control that still fails stops the build
(`baseline_failed`). During verification only the checks run, on every host including the router.

| Profile | For | Contents |
|---|---|---|
| `ubuntu-l1` (default) | Ubuntu, Debian, Kali, Rocky, Alma | SSH (no root login, no passwords, auth tries, config permissions), ASLR, no SUID core dumps, no IP forwarding (except routers and container hosts), auditd, automatic security updates, sticky `/tmp`. Aligned with common CIS Level 1 themes. |
| `linux-moderate` | the same | `ubuntu-l1` plus login banner, account lockout, password quality and history, idle shell timeout, audit rules and audit log protection, time sync, sudo logging, unneeded services off. Needed by the compliance frameworks. |
| `windows-l1` | Windows Server, Windows 11 | SMBv1 off, SMB signing, firewall on, guest account off, minimum password length, LLMNR off, RDP with NLA, logon auditing, Defender real-time protection. |
| `windows-moderate` | the same | `windows-l1` plus lockout, password history and complexity, logon notice, screen lock, audit policy, security log size, time sync (set as domain policy on domain controllers). |

A spec names the Linux profile (`baseline:`); Windows hosts get its counterpart (`windows:` in the profile)
automatically. `baseline: none` turns hardening off for every host.

### Control format

```yaml
- id: ssh-root-login                  # stable id (the framework mapping refers to it)
  area: SSH
  title: SSH root login disabled
  theme: CIS Level 1 - SSH server configuration     # the theme only, never a benchmark section number
  reference: "OpenSSH sshd_config(5): PermitRootLogin"   # only from real vendor/upstream documentation
  applies_to: all                     # all | router | non-router
  check: |                            # exit 0 = compliant; read the EFFECTIVE state (sshd -T, sysctl -n, systemctl)
    sshd -T 2>/dev/null | grep -qx 'permitrootlogin no'
  fix: |                              # idempotent; persist it (drop-in files VALOR owns), then apply it live
    ...
```

- Linux controls run without `pipefail` (`cmd | grep -q x` is fine there), each in its own subshell, so `exit` ends
  only that control. `$APT` is an `apt-get` with a lock timeout; `$VALOR_FAMILY` tells Debian from RHEL.
- Use VALOR's drop-ins: `/etc/ssh/sshd_config.d/01-valor-baseline.conf`, `/etc/sysctl.d/60-valor-baseline.conf`.
- Windows profiles set `shell: powershell`: `check` is an expression that is true when compliant, `fix` is
  idempotent PowerShell, and `$IsDC` is set on domain controllers.
- A profile with `extends: <profile>` starts from that profile's controls; a control with the same id replaces the
  inherited one.

A baseline change makes every host `converge` (no reboot). Results are reported as **aligned with** the themes, never
as compliant or certified.

## Compliance frameworks

[`baselines/frameworks/frameworks.yaml`](../baselines/frameworks/frameworks.yaml) maps VALOR's evidence to framework
requirement ids. [`valor/compliance.py`](../valor/compliance.py) uses it to validate designs and build the report.

| Id (in `compliance.frameworks`) | Framework | Profile it needs |
|---|---|---|
| `nist-800-171` | NIST SP 800-171 Rev. 2 (CUI; assessed by CMMC Level 2) | `linux-moderate` |
| `nist-800-53-moderate` | NIST SP 800-53 Rev. 5, Moderate baseline | `linux-moderate` |
| `nist-800-53-low` | NIST SP 800-53 Rev. 5, Low baseline | `linux-moderate` |
| `pci-dss` | PCI DSS v4.0.1 | `linux-moderate` |
| `hipaa` | HIPAA Security Rule (45 CFR 164.308, 164.312) | `linux-moderate` |
| `cis-l1` | CIS Benchmarks Level 1, by theme | `ubuntu-l1` |

The file has four parts:

- `frameworks`: id, title, the profile it needs, and (`only`) the requirement ids a baseline selects;
- `requirements`: the requirement ids and their titles per framework family;
- `controls`: baseline control id -> requirement ids;
- `design`: checks of the range's design -> requirement ids. There are three: `deny-by-default` (the router
  separates segments and the isolation tests pass), `scope-no-internet` (the segments in `compliance.scope` have no
  internet) and `central-logging` (a `syslog-server` receives the logs of every other Linux host).

A requirement passes when all its evidence passes on every host it applies to; it is *partial* when only some hosts
have evidence. Requirements without technical evidence are listed as not covered. CIS Benchmarks and ISO/IEC 27001
texts are licensed, so VALOR names themes and ids only.

## OS catalog

[`templates/catalog.toml`](../templates/catalog.toml) lists the operating systems VALOR can turn into templates:

| OS (`os:` in a spec) | Family | Source |
|---|---|---|
| `ubuntu-24.04` (default) | debian | Ubuntu cloud image, signed checksums |
| `ubuntu-26.04` | debian | Ubuntu cloud image, signed checksums |
| `debian-13`, `debian-12` | debian | Debian generic cloud image |
| `kali` | debian | Kali generic cloud image |
| `rocky-10`, `alma-10` | rhel | GenericCloud images (need an x86-64-v3 CPU); also `install: iso` from the minimal ISO |
| `windows-server-2025`, `windows-server-2025-core`, `windows-server-2022`, `windows-server-2022-core` | windows | Evaluation ISO from the ISO library: unattended setup, VirtIO guest tools, sysprep |
| `windows-11` | windows | Windows 11 Enterprise evaluation ISO (UEFI, Secure Boot, TPM 2.0) |

Entry keys: `family`, `description`, the image (`image_url`, or `image_base` + `image_pattern` for rolling names,
`archive` when it is packed), its checksums (`checksums_url`, `checksum_type`) and, where the publisher signs them,
`checksums_sig_url`, `signing_key_file` (in `templates/keys/`) and `signing_key_fingerprint`. Optional: `cpu_min`,
`install_iso` (the ISO catalog entry for `install: iso`), and for Windows `iso`, `edition` (a pattern matched against
the image names in `install.wim`) and `default_cores` / `default_memory` / `default_disk` (floors: smaller sizes in a
spec are raised to them).

Templates are found by tags (`valor-template`, `os-<name>`) in VALOR's template pool, never by VMID. The installer
builds them (`./install.sh --template <os>`).

### Adding an OS

1. Add an entry to `catalog.toml`. Prefer an official cloud image with a signed checksum list, and ship the
   publisher's key in `templates/keys/` with its fingerprint.
2. Make sure the image gets the QEMU guest agent: the installer bakes it in from
   [`templates/vendor-data.yaml`](../templates/vendor-data.yaml).
3. Check the roles and baselines for its family; a new family needs work in `valor/roles.py`, `valor/baseline.py` and
   probably `valor/apply.py`.
4. Build it on a test cluster (`./install.sh --template <os>`), build a range with it and verify it.
5. Update the OS list in the [spec reference](../valor/spec-reference.md) (the AI reads it).

## ISO catalog

[`templates/isos.toml`](../templates/isos.toml) lists the installer media the ISO library offers in one click: Ubuntu
Server, Debian netinst, Rocky and Alma minimal (with kickstart support), Kali, the Windows evaluation media and the
VirtIO drivers. An entry names either a checksum list plus a filename `pattern` (the newest match wins, so point
releases are followed) or a fixed `url`. Where the publisher signs the list, the signature is checked against the key
in `templates/keys/` before anything is downloaded; Proxmox then checks the file's checksum.

Keys: `title`, `kind` (`linux`, `windows`, `drivers`), `family`, `checksums_url`, `checksums_sig_url`,
`signing_key_file`, `signing_key_fingerprint`, `base_url`, `pattern`, `algorithm`, and `install = "kickstart"` with
`kickstart_repos` for media VALOR can install unattended.
