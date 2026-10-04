"""Service roles: idempotent scripts in roles/<name>/role.sh with metadata in role.yaml."""

from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import yaml

from .errors import ValorError
from .spec import RangeSpec

# Shared helpers available to every role script.
PRELUDE = r"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C.UTF-8
VALOR_CHANGED=0
apt_install() {
  # Install packages only if missing; waits for apt locks held by first-boot updates.
  local missing=()
  for p in "$@"; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep "install ok installed" >/dev/null || missing+=("$p"); done
  [ ${#missing[@]} -eq 0 ] && return 0
  local i
  for i in 1 2 3; do
    apt-get -o DPkg::Lock::Timeout=900 -qq update >/dev/null 2>&1 && \
    apt-get -o DPkg::Lock::Timeout=900 -qq -y install "${missing[@]}" >/dev/null && { VALOR_CHANGED=1; return 0; }
    sleep $((i * 10))
  done
  echo "apt_install failed for: ${missing[*]}" >&2
  return 1
}
VALOR_FAMILY=debian; [ -f /etc/redhat-release ] && VALOR_FAMILY=rhel
dnf_install() {
  local missing=() p i
  for p in "$@"; do rpm -q "$p" >/dev/null 2>&1 || missing+=("$p"); done
  [ ${#missing[@]} -eq 0 ] && return 0
  for i in 1 2 3; do
    dnf -q -y install "${missing[@]}" >/dev/null && { VALOR_CHANGED=1; return 0; }
    sleep $((i * 10))
  done
  echo "dnf install failed for: ${missing[*]}" >&2
  return 1
}
pkg_install() {
  # pkg_install DEBIAN_PACKAGES... [-- RHEL_PACKAGES...]; without "--" the same names are used for both families
  local deb=() rhel=() seen=0 p
  for p in "$@"; do if [ "$p" = "--" ]; then seen=1; elif [ $seen = 1 ]; then rhel+=("$p"); else deb+=("$p"); fi; done
  [ $seen = 0 ] && rhel=("${deb[@]}")
  if [ "$VALOR_FAMILY" = rhel ]; then dnf_install "${rhel[@]}"; else apt_install "${deb[@]}"; fi
}
selinux_port() {
  # selinux_port TYPE PORT: allow a service type on a non-standard TCP port when SELinux is enforcing
  command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = Enforcing ] || return 0
  dnf_install policycoreutils-python-utils
  semanage port -l | awk -v t="$1" '$1==t' | grep -qw "$2" || { semanage port -a -t "$1" -p tcp "$2" 2>/dev/null || semanage port -m -t "$1" -p tcp "$2"; VALOR_CHANGED=1; }
}
firewall_open() {
  # firewall_open PORT: open a TCP port in firewalld if it runs (Rocky/Alma); no-op elsewhere
  systemctl is-active -q firewalld 2>/dev/null || return 0
  firewall-cmd -q --query-port="$1/tcp" || { firewall-cmd -q --permanent --add-port="$1/tcp"; firewall-cmd -q --reload; VALOR_CHANGED=1; }
}
write_file() {
  # write_file PATH MODE  (content on stdin); only touches the file when content differs
  local path="$1" mode="${2:-0644}" tmp
  tmp=$(mktemp)
  cat > "$tmp"
  if [ -f "$path" ] && cmp -s "$tmp" "$path"; then rm -f "$tmp"; return 1; fi
  install -D -m "$mode" "$tmp" "$path"; rm -f "$tmp"; VALOR_CHANGED=1; return 0
}
changed() { VALOR_CHANGED=1; }
trap 'echo "VALOR-ROLE-CHANGED=$VALOR_CHANGED"' EXIT
"""


@dataclass
class Role:
    name: str
    script: str                 # role.sh (Debian/RHEL family); "" when the role is Windows-only
    meta: dict
    ps_script: str = ""         # role.ps1 (Windows)

    @property
    def digest(self) -> str:
        return hashlib.sha256((self.script + self.ps_script + repr(sorted(self.meta.items()))).encode()).hexdigest()[:16]

    @property
    def families(self) -> list[str]:
        return self.meta.get("families") or (["debian"] if self.script else ["windows"])

    def script_for(self, family: str) -> str:
        return self.ps_script if family == "windows" else self.script


def load_role(roles_dir: Path, name: str) -> Role:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", name):
        raise ValorError("role_invalid", f"invalid role name '{name}'")
    d = roles_dir / name
    sh, ps = d / "role.sh", d / "role.ps1"
    if not sh.is_file() and not ps.is_file():
        available = sorted(p.name for p in roles_dir.iterdir() if (p / "role.sh").is_file() or (p / "role.ps1").is_file())
        raise ValorError("role_missing", f"role '{name}' does not exist",
                         hint=f"Available roles: {', '.join(available)}. New roles go in roles/<name>/role.sh or "
                              "role.ps1 (see the role-authoring skill).")
    meta = yaml.safe_load((d / "role.yaml").read_text()) if (d / "role.yaml").is_file() else {}
    return Role(name, sh.read_text() if sh.is_file() else "", meta or {}, ps.read_text() if ps.is_file() else "")


def resolve_param(spec: RangeSpec, value) -> str:
    """Host names become their IP (/32), segment names their CIDR; lists are space-separated."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return " ".join(resolve_param(spec, v) for v in value)
    s = str(value)
    if any(h.name == s for h in spec.hosts):
        return f"{spec.host(s).address}/32"
    if any(seg.name == s for seg in spec.segments):
        return str(spec.segment(s).cidr)
    return s


def role_env(spec: RangeSpec, host, role: Role, params: dict) -> dict[str, str]:
    seg = spec.segment(host.segment)
    declared = role.meta.get("params", {}) or {}
    env = {
        "VALOR_RANGE": spec.name, "VALOR_HOST": host.name, "VALOR_ROLE": role.name,
        "VALOR_ADDRESS": str(host.address), "VALOR_SEGMENT": seg.name, "VALOR_SEGMENT_CIDR": str(seg.cidr),
        "VALOR_GATEWAY": str(seg.gateway),
    }
    for pname, pdef in declared.items():
        if pname not in params:
            if (pdef or {}).get("required"):
                raise ValorError("role_param_missing", f"role {role.name} on {host.name} needs parameter '{pname}'",
                                 host=host.name, hint=(pdef or {}).get("description"))
            if (pdef or {}).get("default") is not None:
                env[f"VALOR_PARAM_{pname.upper()}"] = resolve_param(spec, pdef["default"])
    for k, v in params.items():
        if declared and k not in declared:
            raise ValorError("role_param_unknown", f"role {role.name} has no parameter '{k}'", host=host.name,
                             hint=f"Known parameters: {', '.join(declared) or 'none'}")
        env[f"VALOR_PARAM_{k.upper()}"] = resolve_param(spec, v)
    return env


def build_script(role: Role, env: dict[str, str]) -> str:
    exports = "\n".join(f"export {k}={shlex.quote(v)}" for k, v in sorted(env.items()))
    return f"{exports}\n{PRELUDE}\n# ---- role {role.name} ----\n{role.script}\n"
