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
    script: str
    meta: dict

    @property
    def digest(self) -> str:
        return hashlib.sha256((self.script + repr(sorted(self.meta.items()))).encode()).hexdigest()[:16]


def load_role(roles_dir: Path, name: str) -> Role:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", name):
        raise ValorError("role_invalid", f"invalid role name '{name}'")
    d = roles_dir / name
    script = d / "role.sh"
    if not script.is_file():
        available = sorted(p.name for p in roles_dir.iterdir() if (p / "role.sh").is_file())
        raise ValorError("role_missing", f"role '{name}' does not exist",
                         hint=f"Available roles: {', '.join(available)}. New roles go in roles/<name>/role.sh "
                              "(see the role-authoring skill).")
    meta = yaml.safe_load((d / "role.yaml").read_text()) if (d / "role.yaml").is_file() else {}
    return Role(name, script.read_text(), meta or {})


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
