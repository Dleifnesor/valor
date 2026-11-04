# *unpacks a bag of service scripts* OwO valor/roles.py loads and runs ROLES! ^w^
# A role installs and configures ONE service on a host (nginx, a DNS server, an AD domain controller...). Roles
# live in roles/<name>/: role.sh (bash, Linux), role.ps1 (PowerShell, Windows) and role.yaml (which OS families,
# which parameters, which ports). VALOR runs roles on EVERY converge, so they must be safe to re-run: change only
# what differs, and report whether something changed (VALOR_CHANGED) *wags tail*
"""Service roles: idempotent scripts in roles/<name>/role.sh with metadata in role.yaml.

A role installs and configures one service on a host (nginx, an AD domain controller, ...). VALOR runs it on
every converge, so it must be safe to run again: it only changes what differs and reports whether it changed
something (VALOR_CHANGED), which decides if services restart. Linux roles are bash (role.sh) and get PRELUDE's
helpers; Windows roles are PowerShell (role.ps1, wrapped in windows.py). role.yaml declares the OS families,
the parameters (required, default, pattern, ...) and optionally `wait_for` / `ports`.
"""

# Lazy type hints UwU
from __future__ import annotations

# hashlib (the role digest), re (names, patterns), shlex (safe shell quoting), dataclass (Role), Path >w<
import hashlib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

# PyYAML reads role.yaml OwO
import yaml

# ValorError; RangeSpec ^w^
from .errors import ValorError
from .spec import RangeSpec

# APT_SETTLE is a raw string (r"""...""": backslashes stay as they are) holding a bash function. Everything inside
# the triple quotes is SHELL code that runs inside the VMs UwU
# Before every `apt-get update` VALOR runs (roles, baselines, the router). A clone's apt-daily.timer is overdue
# (Persistent=true, last run when the template was built), so Ubuntu starts its daily list refresh right at boot -
# often before the build has internet - and it can hang for long holding the package-list lock, which `apt-get
# update` does not wait for (DPkg::Lock::Timeout covers only installs). It only refreshes the lists, which VALOR
# does next anyway: stop it, then wait for any other holder.
APT_SETTLE = r"""apt_settle() {
  systemctl stop apt-daily.service 2>/dev/null || true
  local i
  for i in $(seq 1 120); do
    command -v fuser >/dev/null 2>&1 && fuser /var/lib/apt/lists/lock >/dev/null 2>&1 || return 0
    sleep 5
  done
}
"""

# PRELUDE: the bash helpers every Linux role script gets, prepended before the role's own code >w<:
#   set -euo pipefail          stop on errors, unset variables and failed pipes
#   apt_install / dnf_install  install packages only when missing, retrying, setting VALOR_CHANGED
#   pkg_install                one call for both families: "pkg_install deb-pkgs -- rhel-pkgs"
#   selinux_port               allow a service on a non-standard port under SELinux
#   firewall_open              open a port in firewalld (Rocky/Alma)
#   wait_port                  wait until something listens on a TCP port
#   container_engine / run_container / container_cmd  Docker or Podman, (re)creating a container only when its
#                              settings changed (tracked with a label holding a hash of them)
#   write_file                 write a file only when its content differs (sets VALOR_CHANGED)
#   changed                    mark "something changed"
#   trap ... EXIT              always print VALOR-ROLE-CHANGED=0/1 at the end, for the engine to read
# Shared helpers available to every role script.
PRELUDE = r"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C.UTF-8
VALOR_CHANGED=0
""" + APT_SETTLE + r"""apt_install() {
  # Install packages only if missing; waits for apt locks held by first-boot updates.
  local missing=()
  for p in "$@"; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep "install ok installed" >/dev/null || missing+=("$p"); done
  [ ${#missing[@]} -eq 0 ] && return 0
  local i
  for i in 1 2 3; do
    apt_settle
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
  semanage port -l | awk -v t="$1" '$1==t' | grep -w "$2" >/dev/null || { semanage port -a -t "$1" -p tcp "$2" 2>/dev/null || semanage port -m -t "$1" -p tcp "$2"; VALOR_CHANGED=1; }
}
firewall_open() {
  # firewall_open PORT [tcp|udp]: open a port in firewalld if it runs (Rocky/Alma); no-op elsewhere
  local proto="${2:-tcp}"
  systemctl is-active -q firewalld 2>/dev/null || return 0
  firewall-cmd -q --query-port="$1/$proto" || { firewall-cmd -q --permanent --add-port="$1/$proto"; firewall-cmd -q --reload; VALOR_CHANGED=1; }
}
wait_port() {
  # wait_port PORT [SECONDS]: wait until something listens on TCP PORT
  local i
  for i in $(seq 1 "${2:-60}"); do ss -ltnH "sport = :$1" | grep . >/dev/null && return 0; sleep 1; done
  echo "nothing listens on TCP port $1" >&2; return 1
}
container_engine() {
  # Docker on Debian/Ubuntu/Kali, Podman (with its docker command) on Rocky/Alma. Published ports reach containers
  # through IP forwarding: mark the host so the baseline's ip-forwarding-disabled control exempts it.
  if [ ! -f /etc/valor/container-host ]; then
    install -d /etc/valor
    echo "runs containers (VALOR container roles): IP forwarding stays on" > /etc/valor/container-host; changed
  fi
  if [ "$(sysctl -n net.ipv4.ip_forward)" != 1 ]; then
    sed -i '/^net.ipv4.ip_forward/d' /etc/sysctl.d/60-valor-baseline.conf 2>/dev/null || true
    echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/61-valor-containers.conf
    sysctl -q -w net.ipv4.ip_forward=1; changed
  fi
  if [ "$VALOR_FAMILY" = rhel ]; then
    pkg_install -- podman podman-docker
    touch /etc/containers/nodocker
    systemctl is-enabled -q podman-restart.service 2>/dev/null || { systemctl enable -q podman-restart.service; changed; }
  else
    pkg_install docker.io
    systemctl is-active -q docker || { systemctl enable -q --now docker; changed; }
  fi
}
run_container() {
  # run_container NAME IMAGE [docker run options...]: (re)creates the container only when its settings changed
  local name="$1" image="$2" first want cur; shift 2
  case "$image" in                       # fully qualified (Podman will not guess a registry without a terminal)
    */*) first=${image%%/*}; [[ "$first" == *.* || "$first" == *:* || "$first" == localhost ]] || image="docker.io/$image" ;;
    *) image="docker.io/library/$image" ;;
  esac
  container_engine
  want=$(printf '%s\n' "$image" "$@" | sha256sum | cut -c1-16)
  cur=$(docker inspect -f '{{ index .Config.Labels "valor.settings" }}' "$name" 2>/dev/null || true)
  local i
  if [ "$cur" != "$want" ]; then
    docker pull -q "$image" >/dev/null
    for i in 1 2 3; do
      docker rm -f "$name" >/dev/null 2>&1 || true
      container_cmd run -d --name "$name" --restart=always --label "valor.settings=$want" "$@" "$image" >/dev/null && break
      [ "$i" = 3 ] && return 1; sleep 5
    done
    changed
  elif [ "$(docker inspect -f '{{ .State.Running }}' "$name")" != true ]; then
    for i in 1 2 3; do
      container_cmd start "$name" >/dev/null && break
      [ "$i" = 3 ] && return 1; sleep 5
    done
    changed
  fi
}
container_cmd() {
  # On Rocky/Alma, SELinux keeps systemd from attaching a container's device filter when the container is started
  # from the guest agent's context: start it from a transient systemd unit instead. Callers retry: systemd refuses
  # new units for a moment while it reconnects to D-Bus.
  if [ "$VALOR_FAMILY" = rhel ]; then
    systemd-run --quiet --wait --pipe --collect -p KillMode=process docker "$@"
  else
    docker "$@"
  fi
}
write_file() {
  # write_file PATH MODE  (content on stdin); only touches the file when content differs, then sets VALOR_CHANGED=1.
  # Succeeds either way: under set -e a "nothing to do" status ended model-written scripts (re-runs of a fixed host).
  # Feed it with a heredoc or < <(cmd), never `cmd | write_file`: a pipeline runs it in a subshell and the change
  # would not count (VALOR_CHANGED), so services would not restart.
  local path="$1" mode="${2:-0644}" tmp
  tmp=$(mktemp)
  cat > "$tmp"
  if [ -f "$path" ] && cmp -s "$tmp" "$path"; then rm -f "$tmp"; return 0; fi
  install -D -m "$mode" "$tmp" "$path"; rm -f "$tmp"; VALOR_CHANGED=1; return 0
}
changed() { VALOR_CHANGED=1; }
trap 'echo "VALOR-ROLE-CHANGED=$VALOR_CHANGED"' EXIT
"""


# Role: one loaded role: its name, bash script (or ""), metadata dict, and PowerShell script (or "") OwO
@dataclass
class Role:
    name: str
    script: str                 # role.sh (Debian/RHEL family); "" when the role is Windows-only
    meta: dict                  # role.yaml
    ps_script: str = ""         # role.ps1 (Windows)

    # digest: a fingerprint of the role's scripts + metadata; when it changes, every host using the role re-converges ^w^
    @property
    def digest(self) -> str:
        """Changes when the role's scripts or metadata change, so hosts using it re-converge (see plan.py)."""
        content = self.script + self.ps_script + repr(sorted(self.meta.items()))
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    # families: OS families the role supports (from role.yaml; otherwise debian for a bash role, windows for a
    # PowerShell-only role) UwU
    @property
    def families(self) -> list[str]:
        return self.meta.get("families") or (["debian"] if self.script else ["windows"])


# load_role: read a role from disk >w<
def load_role(roles_dir: Path, name: str) -> Role:
    # The name must look like a role name (no path tricks) OwO
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", name):
        raise ValorError("role_invalid", f"invalid role name '{name}'")
    folder = roles_dir / name
    bash_file, powershell_file, meta_file = folder / "role.sh", folder / "role.ps1", folder / "role.yaml"
    # No script at all = it doesn't exist; the hint lists the roles that do ^w^
    if not bash_file.is_file() and not powershell_file.is_file():
        available = sorted(path.name for path in roles_dir.iterdir()
                           if (path / "role.sh").is_file() or (path / "role.ps1").is_file())
        raise ValorError("role_missing", f"role '{name}' does not exist",
                         hint=f"Available roles: {', '.join(available)}. New roles go in roles/<name>/role.sh or "
                              "role.ps1 (see the role-authoring skill).")
    # Read role.yaml (empty when missing) and both scripts (empty when missing) UwU
    meta = yaml.safe_load(meta_file.read_text()) if meta_file.is_file() else {}
    return Role(name,
                bash_file.read_text() if bash_file.is_file() else "",
                meta or {},
                powershell_file.read_text() if powershell_file.is_file() else "")


# resolve_param: turn a parameter value into text for the role's environment >w<
def resolve_param(spec: RangeSpec, value, raw: bool = False) -> str:
    """Host names become their IP (/32), segment names their CIDR; lists are space-separated.
    raw parameters (package names, scripts) are passed as written."""
    # Booleans become "true"/"false"; lists become space-separated words OwO
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return " ".join(resolve_param(spec, item, raw) for item in value)
    text = str(value)
    # "raw" parameters (package names, scripts) are passed exactly as written ^w^
    if raw:
        return text
    # A host name becomes its address /32; a segment name becomes its network (so roles can write firewall rules
    # using names from the spec) UwU
    if any(host.name == text for host in spec.hosts):
        return f"{spec.host(text).address}/32"
    if any(segment.name == text for segment in spec.segments):
        return str(spec.segment(text).cidr)
    return text


# role_env: the environment variables a role runs with >w<
def role_env(spec: RangeSpec, host, role: Role, params: dict) -> dict[str, str]:
    """The environment a role script runs with: facts about the range and host (VALOR_*) plus the role's
    parameters (VALOR_PARAM_<NAME>), checked against what role.yaml declares. Raises ValorError for a missing,
    unknown or invalid parameter - validation calls this too, so the spec is rejected before a build."""
    segment = spec.segment(host.segment)
    # Facts about the range and this host, all prefixed VALOR_ OwO
    env = {
        "VALOR_RANGE": spec.name, "VALOR_HOST": host.name, "VALOR_ROLE": role.name,
        "VALOR_ADDRESS": str(host.address), "VALOR_SEGMENT": segment.name, "VALOR_SEGMENT_CIDR": str(segment.cidr),
        "VALOR_GATEWAY": str(segment.gateway),
        # every host of the range as name=address (e.g. for a DNS zone)
        "VALOR_RANGE_HOSTS": " ".join(f"{other.name}={other.address}" for other in spec.hosts),
    }
    # What role.yaml declares ^w^
    declared = role.meta.get("params", {}) or {}

    # Declared but not given: required ones are an error, the others fall back to their default (if any).
    for name, definition in declared.items():
        definition = definition or {}
        if name in params:
            continue
        if definition.get("required"):
            raise ValorError("role_param_missing", f"role {role.name} on {host.name} needs parameter '{name}'",
                             host=host.name, hint=definition.get("description"))
        if definition.get("default") is not None:
            env[f"VALOR_PARAM_{name.upper()}"] = resolve_param(spec, definition["default"], definition.get("raw"))

    # Every given parameter, checked, as VALOR_PARAM_<NAME> UwU
    for name, value in params.items():
        env[f"VALOR_PARAM_{name.upper()}"] = _checked_param(spec, host, role, declared, name, value)
    return env


# _checked_param: one given parameter: it must be declared (when the role declares any), not too long, and every
# word must match its pattern >w<
def _checked_param(spec: RangeSpec, host, role: Role, declared: dict, name: str, value) -> str:
    """One given parameter, resolved (host -> address, ...) and checked against its declaration."""
    if declared and name not in declared:
        raise ValorError("role_param_unknown", f"role {role.name} has no parameter '{name}'", host=host.name,
                         hint=f"Known parameters: {', '.join(declared) or 'none'}")
    definition = declared.get(name) or {}
    text = resolve_param(spec, value, definition.get("raw"))
    if definition.get("max_length") and len(text) > int(definition["max_length"]):
        raise ValorError("role_param_too_long", f"parameter '{name}' of role {role.name} on {host.name} is longer "
                         f"than {definition['max_length']} characters", host=host.name)
    # The pattern applies to every word (list parameters arrive space-separated).
    if definition.get("pattern") and not all(re.fullmatch(definition["pattern"], word) for word in text.split()):
        raise ValorError("role_param_invalid", f"parameter '{name}' of role {role.name} on {host.name} has an "
                         f"invalid value '{text[:80]}'", host=host.name, hint=definition.get("description"))
    return text


# role_ports: the TCP ports a role serves: a fixed list, or read from one of its parameters (e.g. a configurable
# port) OwO
def role_ports(role: Role, params: dict | None = None) -> list[int]:
    """TCP ports a role serves: fixed in role.yaml (ports), or taken from one of its parameters (ports_param)."""
    param = role.meta.get("ports_param")
    if not param:
        return list(role.meta.get("ports") or [])
    # The parameter's value (or its default), split into words ^w^
    default = ((role.meta.get("params") or {}).get(param) or {}).get("default") or ""
    value = (params or {}).get(param, default)
    words = value if isinstance(value, list) else re.split(r"[\s,]+", str(value))
    words = [str(word).split(":")[0] for word in words]          # "8080:80" (published container port) -> 8080
    # Keep valid port numbers only (1-65535) UwU
    return [int(word) for word in words if word.strip().isdigit() and 0 < int(word) < 65536]


# build_script: the complete bash script for one role on one host: every environment variable exported (quoted
# safely with shlex), then PRELUDE, then the role's own script >w< *ties it all with a bow*
def build_script(role: Role, env: dict[str, str]) -> str:
    """The complete bash script for one role on one host: exported environment, PRELUDE helpers, the role."""
    exports = "\n".join(f"export {key}={shlex.quote(value)}" for key, value in sorted(env.items()))
    return f"{exports}\n{PRELUDE}\n# ---- role {role.name} ----\n{role.script}\n"
