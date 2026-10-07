---
name: compliance
description: How VALOR's compliance baseline works and how to change it - controls with a check and an idempotent fix, applied during builds and re-checked during verification. Use when a baseline control fails, when the user asks about hardening/compliance results, or when adding or changing controls.
---

# Compliance baseline

Baseline profiles in `baselines/`: `ubuntu-l1` (Linux, **aligned with common CIS Level 1 themes**) and `windows-l1`;
`linux-moderate` and `windows-moderate` extend them (`extends:`) with the controls the compliance frameworks need
(lockout, password policy, notices, idle logout, audit rules, time sync). A Linux profile names its Windows
counterpart (`windows:`). `baselines/frameworks/frameworks.yaml` maps control ids and design checks to framework
requirement ids (NIST SP 800-171, NIST SP 800-53 Low/Moderate, PCI DSS, HIPAA, CIS themes); `valor/compliance.py`
builds the per-framework report. These are reproducible starting points, **not** certified implementations.
Always report results as "aligned with", never "compliant with" or "certified".

## Structure of a control

```yaml
- id: ssh-root-login                 # stable id
  area: SSH                          # SSH | Kernel | Auditing | Patching | Filesystem | ...
  title: SSH root login disabled
  theme: CIS Level 1 - SSH server configuration    # the theme only, no section numbers
  reference: "OpenSSH sshd_config(5): PermitRootLogin"  # vendor/upstream documentation the control comes from
  applies_to: all                    # all | router | non-router
  check: |                           # exit 0 = compliant; read the EFFECTIVE state (sshd -T, sysctl -n, systemctl)
    sshd -T 2>/dev/null | grep -qx 'permitrootlogin no'
  fix: |                             # idempotent change; persist it (drop-in files), then apply it live
    ...
```

- `check` must read effective state, not just a file (e.g. `sshd -T`, `sysctl -n`), and must also confirm the
  setting is persisted where that matters.
- `fix` must be idempotent and must make `check` pass. Use drop-ins VALOR owns:
  `/etc/ssh/sshd_config.d/01-valor-baseline.conf`, `/etc/sysctl.d/60-valor-baseline.conf`.
  `$APT` is set to `apt-get -o DPkg::Lock::Timeout=900 -qq -y`.
- Controls run **without** `pipefail` (so `cmd | grep -q x` is safe there). In roles, which use `pipefail`,
  write `cmd | grep x >/dev/null` instead of `grep -q` - an early-exiting `grep -q` makes the writer die of SIGPIPE.
- **References**: record a `reference` only when it comes from real documentation (man pages, kernel docs, vendor
  docs). Never invent benchmark section numbers.

## When it runs

- Build: for each host, each applicable control runs `check`; if it fails, `fix` then `check` again. A control that
  still fails stops the build with `baseline_failed` (details show the fix output).
- Verification: `check` only, on every host including the router; the journal shows a control x host matrix.
- `ip-forwarding-disabled` applies to non-routers only; the router forwards by design.

## Fixing a failing control

Read the error details, inspect with `guest_run` if needed (approval required), then correct `check` and/or `fix`
in the YAML and re-apply - a baseline change makes every host `converge` (no reboot). Note that isolated segments
lose internet access after the build, so automatic updates there only work while build egress is open.
