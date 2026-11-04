# *puts on a tiny hard hat* OwO valor/baseline.py does security HARDENING baselines! ^w^
# A baseline is a hardening profile (baselines/<id>.yaml): a list of "controls", each with a little shell `check`
# (succeeds when the VM is already compliant) and a `fix` (makes it compliant, safe to run again and again).
# On every build VALOR glues all of a host's controls into ONE script (bundle()), runs it in the VM, and records
# for every control whether it passed before and after. Those results feed the build journal and the compliance
# report *wags tail proudly*
"""Compliance baselines: controls with a check (reads effective state) and an idempotent fix.

A baseline (baselines/<id>.yaml) is a hardening profile: a list of controls, each a small shell `check` that
succeeds when the host is compliant and a `fix` that makes it so. On every converge VALOR runs one generated
script per host (`bundle`) that checks each control, fixes the failing ones and checks again; the
"before/after" result per control goes into the build journal and the compliance report.
"""

# Lazy type hints UwU
from __future__ import annotations

# hashlib makes the baseline's digest (a fingerprint); re checks names and parses results; dataclass builds the
# Control/Baseline classes; Path finds the files >w<
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# PyYAML reads the baseline files OwO
import yaml

# ValorError for bad/missing baselines; APT_SETTLE = a shell helper (shared with roles) that waits until apt isn't
# busy (e.g. with automatic updates) before installing anything ^w^
from .errors import ValorError
from .roles import APT_SETTLE


# Control: one hardening rule UwU
#   id          short unique id, e.g. "ssh-root-login"
#   area        a group for the report (ssh, accounts, network, ...)
#   title       a human sentence
#   check/fix   the shell snippets described above
#   applies_to  "all", only the "router", or only "non-router" hosts
#   reference   the vendor/benchmark doc it came from (only when it really came from one)
#   theme       which CIS Level 1 theme it lines up with (no section numbers on purpose) >w<
@dataclass
class Control:
    id: str
    area: str
    title: str
    check: str
    fix: str
    applies_to: str = "all"          # all | router | non-router
    reference: str | None = None     # only recorded when taken from vendor/benchmark documentation
    theme: str | None = None         # the CIS Level 1 theme the control is aligned with (no section numbers)

    # applies: does this control apply to this host? "all" always does; otherwise "router" controls only apply to
    # the router and "non-router" ones only to the others (comparing two booleans does both cases at once) OwO
    def applies(self, is_router: bool) -> bool:
        return self.applies_to == "all" or (self.applies_to == "router") == is_router


# Baseline: a whole profile ^w^
#   id/title    its name and a human title
#   families    OS families it's for (debian, rhel, windows)
#   controls    the full list (inherited ones included)
#   digest      a fingerprint of the profile's text; when it changes, hosts re-run the baseline
#   windows     which Windows profile Windows hosts get when THIS (Linux) profile is the range's baseline UwU
@dataclass
class Baseline:
    id: str
    title: str
    families: list[str]
    controls: list[Control]
    digest: str
    windows: str = "windows-l1"      # the profile Windows hosts get when this is the range's (Linux) baseline


# load_baseline: read one profile by name (None for "none"). `_seen` is private: it remembers the chain of
# profiles being loaded, to catch a profile that (directly or not) extends itself >w<
def load_baseline(baselines_dir: Path, name: str, _seen: tuple = ()) -> Baseline | None:
    """A baseline profile. `extends: <profile>` starts from that profile's controls (a control with the same id
    replaces the inherited one); the digest covers the whole chain, so a parent change re-converges the hosts."""
    # "none" = no baseline at all ^w^
    if name == "none":
        return None
    # The name must look like a profile name; that also stops paths like "../x" escaping the folder *guards with tail*
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", name):
        raise ValorError("baseline_invalid", f"invalid baseline name '{name}'")
    # Already in the chain? Then it extends itself, which would loop forever: refuse OwO
    if name in _seen:
        raise ValorError("baseline_invalid", f"baseline '{name}' extends itself")
    # The file must exist; the hint lists the available profiles UwU
    path = baselines_dir / f"{name}.yaml"
    if not path.is_file():
        raise ValorError("baseline_missing", f"baseline '{name}' does not exist",
                         hint=f"Available: {', '.join(available(baselines_dir))}")
    # Read the text (the digest needs the exact text) and parse it; turn each control dict into a Control >w<
    text = path.read_text()
    raw = yaml.safe_load(text)
    controls = [Control(**control) for control in raw.get("controls") or []]
    # The digest starts out covering this file's own text ^w^
    digest_source = text
    # `extends: <parent>`: load the parent (adding ourselves to the chain), keep the parent's controls EXCEPT ones we
    # redefine (same id), then add our own. The digest then also covers the parent's digest, so editing the parent
    # makes children re-converge too OwO *nuzzles the family tree*
    if raw.get("extends"):
        parent = load_baseline(baselines_dir, raw["extends"], (*_seen, name))
        own_ids = {control.id for control in controls}
        controls = [control for control in parent.controls if control.id not in own_ids] + controls
        digest_source = parent.digest + text
    # Build the Baseline. The digest is the first 16 hex chars of SHA-256 (plenty to notice a change). Families
    # default to debian; the Windows counterpart defaults to windows-l1 UwU
    return Baseline(raw["id"], raw["title"], raw.get("families", ["debian"]), controls,
                    hashlib.sha256(digest_source.encode()).hexdigest()[:16], raw.get("windows", WINDOWS_BASELINE))


# available: every profile name (file names without .yaml), sorted >w<
def available(baselines_dir: Path) -> list[str]:
    return sorted(path.stem for path in baselines_dir.glob("*.yaml"))


# The default Windows profile ^w^
WINDOWS_BASELINE = "windows-l1"


# baseline_for: which profile one host gets. "none" -> nothing. Windows hosts get the Windows counterpart of the
# range's profile (unless the range's profile is itself a Windows one); everyone else gets the range's profile OwO
def baseline_for(baselines_dir: Path, spec_baseline: str, family: str) -> Baseline | None:
    """The baseline a host gets: the spec's (Linux) baseline, or that profile's Windows counterpart (windows-l1
    unless the profile names another) for Windows hosts; none means none."""
    if spec_baseline == "none":
        return None
    if family == "windows":
        linux = load_baseline(baselines_dir, spec_baseline)
        return load_baseline(baselines_dir, linux.windows if linux and "windows" not in linux.families
                             else WINDOWS_BASELINE)
    return load_baseline(baselines_dir, spec_baseline)


# _fn: turn a control id into something safe inside a shell function name ("ssh-root-login" -> "ssh_root_login").
# Anything that isn't a letter, digit or _ becomes _ UwU
def _fn(control_id: str) -> str:
    """A control id as a shell function name suffix (ssh-root-login -> ssh_root_login)."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", control_id)


# bundle: write ONE shell script that handles every control for one host *rolls up sleeves*
#   is_router  so router-only/non-router controls are picked right
#   fix        True = check, fix what fails, check again; False = only check (used by verify, changes nothing)
# For each control it prints "VALOR-CONTROL <id> <before> <after>" (pass/fail each) for parse_results() >w<
def bundle(baseline: Baseline, is_router: bool, *, fix: bool) -> str:
    """One script for all applicable controls; prints 'VALOR-CONTROL <id> <before> <after>' per control."""
    # no pipefail: checks like `sshd -T | grep -q` would fail on SIGPIPE when grep exits early
    # The script's preamble ^w^:
    #   set -u                  using an unset variable is an error (catches typos in checks)
    #   DEBIAN_FRONTEND/LC_ALL  no interactive apt prompts; predictable (English, UTF-8) command output
    #   APT=...                 apt-get that waits up to 15 min for the package lock, quiet, auto-yes
    parts = ["set -u", "export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8",
             "APT='apt-get -o DPkg::Lock::Timeout=900 -qq -y'",
             # family + one package helper for Debian/Ubuntu/Kali (apt) and Rocky/Alma/RHEL (dnf)
             "VALOR_FAMILY=debian; [ -f /etc/redhat-release ] && VALOR_FAMILY=rhel",
             # installed(): is a package installed? rpm on RHEL, dpkg-query on Debian (the doubled backslashes become plain
             # quotes in the shell script) OwO
             "installed() { if [ $VALOR_FAMILY = rhel ]; then rpm -q \"$1\" >/dev/null 2>&1; "
             "else dpkg-query -W -f='${Status}' \"$1\" 2>/dev/null | grep -q 'install ok installed'; fi; }",
             # apt_settle() from roles.py, then pkg_install(): install packages with dnf or apt UwU
             APT_SETTLE,
             "pkg_install() { if [ $VALOR_FAMILY = rhel ]; then dnf -q -y install \"$@\" >/dev/null; "
             "else apt_settle; $APT update >/dev/null && $APT install \"$@\" >/dev/null; fi; }",
             # sshd -T / -t need /run/sshd, which only exists while sshd runs; Ubuntu 24.04 socket-activates ssh.
             "[ -d /run/sshd ] || install -d -m 0755 /run/sshd 2>/dev/null || true"]
    # Now one block per control that applies to this host >w<
    for control in baseline.controls:
        if not control.applies(is_router):
            continue
        # Define check_<name> and fix_<name> as shell functions. The ( ) body (instead of { }) runs each in a SUBSHELL,
        # so an `exit` inside a check or fix only ends that function, not the whole script ^w^
        name = _fn(control.id)
        parts.append(f"check_{name}() (\n{control.check.strip()}\n)")  # subshell: `exit` ends only this check
        parts.append(f"fix_{name}() (\n{control.fix.strip()}\n)")
        # Fixing mode: pass already -> "pass pass". Otherwise run the fix (its output into a log file), check again, and
        # print "fail pass" (fixed!) or "fail fail" (still failing; then the last 5 lines of the fix log go to stderr so
        # the journal shows why) OwO
        if fix:
            # check -> (fix -> check again); a fix's output only shows when it didn't help
            parts.append(
                f"if check_{name} >/dev/null 2>&1; then echo 'VALOR-CONTROL {control.id} pass pass'; "
                f"else fix_{name} >/tmp/valor-fix-{name}.log 2>&1; "
                f"if check_{name} >/dev/null 2>&1; then echo 'VALOR-CONTROL {control.id} fail pass'; "
                f"else echo 'VALOR-CONTROL {control.id} fail fail'; tail -5 /tmp/valor-fix-{name}.log >&2; fi; fi")
        # Check-only mode: just "pass pass" or "fail fail" UwU
        else:
            parts.append(f"if check_{name} >/dev/null 2>&1; then echo 'VALOR-CONTROL {control.id} pass pass'; "
                         f"else echo 'VALOR-CONTROL {control.id} fail fail'; fi")
    # Always exit 0: failing controls are REPORTED, they don't make the script itself "fail" >w<
    parts.append("exit 0")
    return "\n".join(parts) + "\n"


# parse_results: read the bundle's output lines back into result records for the journal and the report ^w^
def parse_results(baseline: Baseline, output: str) -> list[dict]:
    """The bundle's 'VALOR-CONTROL <id> <before> <after>' lines as result records (unknown ids are ignored)."""
    # Look up controls by id OwO
    by_id = {control.id: control for control in baseline.controls}
    results = []
    # Each line that matches "VALOR-CONTROL <id> <pass|fail> <pass|fail>" exactly, for a control we know, becomes
    # a record with the control's info, before/after, and fixed = "it failed before but passes now" UwU *wags tail*
    for line in output.splitlines():
        match = re.match(r"VALOR-CONTROL (\S+) (pass|fail) (pass|fail)$", line.strip())
        if match and match.group(1) in by_id:
            control = by_id[match.group(1)]
            before, after = match.group(2), match.group(3)
            results.append({"control": control.id, "area": control.area, "title": control.title,
                            "reference": control.reference, "theme": control.theme,
                            "before": before, "after": after, "fixed": before == "fail" and after == "pass"})
    return results
