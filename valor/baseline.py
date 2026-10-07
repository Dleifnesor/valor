"""Compliance baselines: controls with a check (reads effective state) and an idempotent fix."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .errors import ValorError


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

    def applies(self, is_router: bool) -> bool:
        return self.applies_to == "all" or (self.applies_to == "router") == is_router


@dataclass
class Baseline:
    id: str
    title: str
    claim: str
    families: list[str]
    controls: list[Control]
    digest: str
    windows: str = "windows-l1"      # the profile Windows hosts get when this is the range's (Linux) baseline


def load_baseline(baselines_dir: Path, name: str, _seen: tuple = ()) -> Baseline | None:
    """A baseline profile. `extends: <profile>` starts from that profile's controls (a control with the same id
    replaces the inherited one); the digest covers the whole chain, so a parent change re-converges the hosts."""
    if name == "none":
        return None
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", name):
        raise ValorError("baseline_invalid", f"invalid baseline name '{name}'")
    if name in _seen:
        raise ValorError("baseline_invalid", f"baseline '{name}' extends itself")
    path = baselines_dir / f"{name}.yaml"
    if not path.is_file():
        raise ValorError("baseline_missing", f"baseline '{name}' does not exist",
                         hint=f"Available: {', '.join(available(baselines_dir))}")
    text = path.read_text()
    raw = yaml.safe_load(text)
    own = [Control(**c) for c in raw.get("controls") or []]
    digest_src = text
    if raw.get("extends"):
        parent = load_baseline(baselines_dir, raw["extends"], (*_seen, name))
        mine = {c.id for c in own}
        own = [c for c in parent.controls if c.id not in mine] + own
        digest_src = parent.digest + text
    return Baseline(raw["id"], raw["title"], raw.get("claim", "aligned-with"), raw.get("families", ["debian"]),
                    own, hashlib.sha256(digest_src.encode()).hexdigest()[:16], raw.get("windows", WINDOWS_BASELINE))


def available(baselines_dir: Path) -> list[str]:
    return sorted(p.stem for p in baselines_dir.glob("*.yaml"))


WINDOWS_BASELINE = "windows-l1"


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


def _fn(cid: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", cid)


def bundle(baseline: Baseline, is_router: bool, *, fix: bool) -> str:
    """One script for all applicable controls; prints 'VALOR-CONTROL <id> <before> <after>' per control."""
    # no pipefail: checks like `sshd -T | grep -q` would fail on SIGPIPE when grep exits early
    parts = ["set -u", "export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8",
             "APT='apt-get -o DPkg::Lock::Timeout=900 -qq -y'",
             # family + one package helper for Debian/Ubuntu/Kali (apt) and Rocky/Alma/RHEL (dnf)
             "VALOR_FAMILY=debian; [ -f /etc/redhat-release ] && VALOR_FAMILY=rhel",
             "installed() { if [ $VALOR_FAMILY = rhel ]; then rpm -q \"$1\" >/dev/null 2>&1; "
             "else dpkg-query -W -f='${Status}' \"$1\" 2>/dev/null | grep -q 'install ok installed'; fi; }",
             "pkg_install() { if [ $VALOR_FAMILY = rhel ]; then dnf -q -y install \"$@\" >/dev/null; "
             "else $APT update >/dev/null && $APT install \"$@\" >/dev/null; fi; }",
             # sshd -T / -t need /run/sshd, which only exists while sshd runs; Ubuntu 24.04 socket-activates ssh.
             "[ -d /run/sshd ] || install -d -m 0755 /run/sshd 2>/dev/null || true"]
    for c in baseline.controls:
        if not c.applies(is_router):
            continue
        f = _fn(c.id)
        parts.append(f"check_{f}() (\n{c.check.strip()}\n)")  # subshell: `exit` ends only this check
        parts.append(f"fix_{f}() (\n{c.fix.strip()}\n)")
        if fix:
            parts.append(
                f"if check_{f} >/dev/null 2>&1; then echo 'VALOR-CONTROL {c.id} pass pass'; "
                f"else fix_{f} >/tmp/valor-fix-{f}.log 2>&1; "
                f"if check_{f} >/dev/null 2>&1; then echo 'VALOR-CONTROL {c.id} fail pass'; "
                f"else echo 'VALOR-CONTROL {c.id} fail fail'; tail -5 /tmp/valor-fix-{f}.log >&2; fi; fi")
        else:
            parts.append(f"if check_{f} >/dev/null 2>&1; then echo 'VALOR-CONTROL {c.id} pass pass'; "
                         f"else echo 'VALOR-CONTROL {c.id} fail fail'; fi")
    parts.append("exit 0")
    return "\n".join(parts) + "\n"


def parse_results(baseline: Baseline, output: str) -> list[dict]:
    by_id = {c.id: c for c in baseline.controls}
    res = []
    for line in output.splitlines():
        m = re.match(r"VALOR-CONTROL (\S+) (pass|fail) (pass|fail)$", line.strip())
        if m and m.group(1) in by_id:
            c = by_id[m.group(1)]
            res.append({"control": c.id, "area": c.area, "title": c.title, "reference": c.reference, "theme": c.theme,
                        "before": m.group(2), "after": m.group(3), "fixed": m.group(2) == "fail" and m.group(3) == "pass"})
    return res
