"""WireGuard remote access to a range: a tunnel that ends on the range router.

VALOR generates every key itself and keeps them encrypted (state/ranges/<name>/wireguard.json, AES-GCM with
VALOR's secret key). The router gets its private key and the peers' public keys inside a script sent over the
guest agent's stdin (never a command line or the Proxmox VM config). A peer's config - its private key included -
is shown only to operators through the web UI, and every view is audited.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import time

from . import state
from .crypto import Box
from .errors import ValorError
from .roles import APT_SETTLE
from .spec import RangeSpec

IFACE = "wg0"
KEEPALIVE = 25


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def keypair() -> tuple[str, str]:
    """(private, public) in WireGuard's base64 format (X25519)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    raw = serialization.Encoding.Raw
    key = X25519PrivateKey.generate()
    return (_b64(key.private_bytes(raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())),
            _b64(key.public_key().public_bytes(raw, serialization.PublicFormat.Raw)))


def _box(cfg) -> Box | None:
    return Box.try_file(cfg.secret_key_file)


def _path(cfg, name: str):
    return state.range_dir(cfg, name) / "wireguard.json"


def _context(name: str) -> str:
    return f"wireguard:{name}"


def load(cfg, name: str) -> dict | None:
    path = _path(cfg, name)
    box = _box(cfg)
    if not path.is_file() or box is None:
        return None
    return json.loads(box.open_text(path.read_text(), _context(name)))


def _save(cfg, name: str, data: dict) -> None:
    """Encrypted, written to a temp file first: a crash mid-write never leaves a broken key file."""
    box = _box(cfg)
    path = _path(cfg, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(box.seal_text(json.dumps(data), _context(name)))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def set_router_address(cfg, name: str, address: str) -> None:
    data = load(cfg, name)
    if data and data.get("router_address") != address:
        data["router_address"] = address
        _save(cfg, name, data)


def forget(cfg, name: str) -> None:
    _path(cfg, name).unlink(missing_ok=True)


def digest(cfg, spec: RangeSpec) -> str | None:
    """Changes when the router's WireGuard setup must change: the spec's section or a key rotation."""
    settings = spec.access.wireguard if spec.access else None
    if settings is None:
        return None
    current = load(cfg, spec.name) if _box(cfg) else None
    generation = (current or {}).get("generation", 0)       # bumped by key rotations, not by adding peers
    return hashlib.sha256(json.dumps({"spec": settings.model_dump(mode="json"), "generation": generation},
                                     sort_keys=True).encode()).hexdigest()[:16]


def ensure(cfg, spec: RangeSpec, rotate: tuple[str, ...] = ()) -> dict:
    """Keys and tunnel addresses for the spec's peers: existing ones are kept (configs stay valid), new peers get
    keys, removed peers are dropped. rotate: peer names (or 'server') that get new keys."""
    settings = spec.access.wireguard
    if _box(cfg) is None:
        raise ValorError("wireguard_unavailable", "WireGuard access needs VALOR's secret key; this engine has none")
    data = load(cfg, spec.name) or {}
    if rotate:
        data["generation"] = data.get("generation", 0) + 1
    if not data.get("server") or "server" in rotate:
        private, public = keypair()
        data["server"] = {"private": private, "public": public}

    network = ipaddress.IPv4Network(str(settings.network))
    # A changed tunnel network invalidates every peer address, so all peers start over.
    known_peers = data.get("peers", {}) if data.get("network") == str(network) else {}
    addresses = list(network.hosts())
    server_address = str(addresses[0])
    taken = {peer["address"] for name, peer in known_peers.items() if name in settings.peers}
    peers = {}
    for name in settings.peers:
        peer = known_peers.get(name)
        if peer is None or name in rotate:
            private, public = keypair()
            # A rotated peer keeps its address; a new one gets the first free address after the router's.
            address = peer["address"] if peer else next(str(a) for a in addresses[1:] if str(a) not in taken)
            taken.add(address)
            peer = {"private": private, "public": public, "psk": _b64(os.urandom(32)), "address": address,
                    "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        peers[name] = peer
    data.update(network=str(network), server_address=server_address, peers=peers)
    _save(cfg, spec.name, data)
    return data


def server_conf(data: dict, spec: RangeSpec) -> str:
    """wg0.conf for the router: its own key and one [Peer] per person or device."""
    settings = spec.access.wireguard
    prefix = ipaddress.IPv4Network(data["network"]).prefixlen
    lines = [f"# VALOR range {spec.name}: WireGuard access (generated; do not edit)", "[Interface]",
             f"Address = {data['server_address']}/{prefix}", f"ListenPort = {settings.port}",
             f"PrivateKey = {data['server']['private']}"]
    for name, peer in data["peers"].items():
        lines += ["", f"# peer {name}", "[Peer]", f"PublicKey = {peer['public']}", f"PresharedKey = {peer['psk']}",
                  f"AllowedIPs = {peer['address']}/32"]
    return "\n".join(lines) + "\n"


def reach_cidrs(spec: RangeSpec) -> list[str]:
    settings = spec.access.wireguard
    names = settings.reach or [segment.name for segment in spec.segments]
    return [str(spec.segment(name).cidr) for name in names]


def peer_conf(data: dict, spec: RangeSpec, peer: str, endpoint: str) -> str:
    """The config file a person imports into their WireGuard app (routes only the range's networks)."""
    keys = data["peers"][peer]
    allowed = ", ".join([*reach_cidrs(spec), data["network"]])
    return "\n".join([
        f"# VALOR range {spec.name} - WireGuard access for '{peer}'",
        "# Keep this file private: it contains the peer's private key.",
        "[Interface]", f"PrivateKey = {keys['private']}", f"Address = {keys['address']}/32", "",
        "[Peer]", f"PublicKey = {data['server']['public']}", f"PresharedKey = {keys['psk']}",
        f"Endpoint = {endpoint}", f"AllowedIPs = {allowed}", f"PersistentKeepalive = {KEEPALIVE}", ""])


def endpoint(spec: RangeSpec, data: dict) -> str | None:
    """Where peers connect: the spec's endpoint (e.g. a port forward), else the router's uplink address."""
    settings = spec.access.wireguard
    if settings.endpoint:
        return settings.endpoint if ":" in settings.endpoint else f"{settings.endpoint}:{settings.port}"
    host = data.get("router_address")
    return f"{host}:{settings.port}" if host else None


def router_script(conf: str | None) -> str:
    """Bash for the router: install/refresh wg0 (conf) or remove it (None). Prints the uplink address users connect to."""
    if conf is None:
        return f"""set -uo pipefail
systemctl disable --now -q wg-quick@{IFACE} 2>/dev/null
ip link del {IFACE} 2>/dev/null
rm -f /etc/wireguard/{IFACE}.conf
if ip link show {IFACE} >/dev/null 2>&1; then echo "wg0 is still present" >&2; exit 1; fi
echo VALOR-WG-REMOVED
"""
    return f"""set -euo pipefail
{APT_SETTLE}command -v wg >/dev/null || {{ export DEBIAN_FRONTEND=noninteractive; apt_settle; apt-get -o DPkg::Lock::Timeout=600 -qq update >/dev/null; apt-get -o DPkg::Lock::Timeout=600 -qq -y install wireguard-tools >/dev/null; }}
umask 077
mkdir -p /etc/wireguard
cat > /etc/wireguard/{IFACE}.conf.valor-new <<'VALOR_WG_EOF'
{conf}VALOR_WG_EOF
if cmp -s /etc/wireguard/{IFACE}.conf.valor-new /etc/wireguard/{IFACE}.conf && ip link show {IFACE} >/dev/null 2>&1; then
  rm -f /etc/wireguard/{IFACE}.conf.valor-new
else
  mv /etc/wireguard/{IFACE}.conf.valor-new /etc/wireguard/{IFACE}.conf
  systemctl enable -q wg-quick@{IFACE}
  if ip link show {IFACE} >/dev/null 2>&1; then
    wg syncconf {IFACE} <(wg-quick strip {IFACE})        # keeps connected peers' sessions
  else
    systemctl restart wg-quick@{IFACE}
  fi
fi
wg show {IFACE} listen-port | sed 's/^/VALOR-WG-PORT /'
echo "VALOR-WG-PEERS $(wg show {IFACE} peers | wc -l)"
"""


def handshakes(dump: str) -> dict[str, int]:
    """`wg show wg0 dump` -> {public key: latest handshake (unix time, 0 = never)}."""
    out = {}
    for line in dump.splitlines()[1:]:              # the first line is the interface itself
        fields = line.split("\t")
        if len(fields) >= 5 and fields[4].isdigit():
            out[fields[0]] = int(fields[4])
    return out
