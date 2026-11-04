# OwO *peeks through the VPN tunnel* valor/web/access.py is the web API for a range's WireGuard VPN access!
# Three endpoints: see the peers and whether they're connected (live handshakes read from the router), download a
# peer's config file (+ a QR code for phones; operators only, and audited), and rotate a peer's keys ^w^
"""WireGuard access to a range: peer list with live handshakes, peer configs (operators, audited), key rotation."""

# Lazy type hints UwU
from __future__ import annotations

# time: the current time, to tell if a handshake is recent >w<
import time

# FastAPI: APIRouter groups the endpoints, Depends injects the signed-in session, Request is the HTTP request.
# Pydantic: BaseModel + ConfigDict describe the (empty) request body OwO
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

# wireguard = the engine's WireGuard module (keys, configs); range_vms lists a range's VMs; ValorError for
# errors from Proxmox; PVE is the Proxmox client; ROUTER = the router's host name ^w^
from .. import wireguard
from ..cluster import range_vms
from ..errors import ValorError
from ..pve import PVE
from ..spec import ROUTER
# From the web core: ApiError (an HTTP error with a code), Session (who's signed in), qr_svg (draws QR codes)
# and require (the role check). From ranges.py: helpers to get the config, start a job, load a spec and check a
# range name *nuzzles*
from .core import ApiError, Session, qr_svg, require
from .ranges import _cfg, _job, _load, _name

# All endpoints here live under /api UwU
router = APIRouter(prefix="/api")


# RotateIn: the rotate endpoint takes an empty JSON body; extra="forbid" rejects any unexpected field, so typos
# or junk in a request fail loudly instead of being ignored >w<
class RotateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


# _wg: load the range's spec (with the name checked) and return it with its WireGuard settings (None when the
# range has no VPN access) ^w^
def _wg(cfg, name: str):
    """(the range's spec, its WireGuard settings or None)."""
    spec, _ = _load(cfg, _name(name))
    return spec, spec.access.wireguard if spec.access else None


# _handshakes: ask the router which peers connected lately. Returns {public key: unix time of latest handshake},
# or None when the router can't be asked (stopped, missing, Proxmox error) OwO
def _handshakes(cfg, name: str) -> dict[str, int] | None:
    """Latest handshake per public key, read from the router (None when the router can't be asked)."""
    try:
        # Find the range's router VM, but only if it's running *sniffs around for the router*
        pve = PVE(cfg)
        router = next((vm for vm in range_vms(pve, name) if vm.host == ROUTER and vm.status == "running"), None)
        if router is None:
            return None
        # Run `wg show <interface> dump` on the router through the guest agent (20 s limit) and parse its output ^w^
        result = pve.exec(router.vmid, ["wg", "show", wireguard.IFACE, "dump"], timeout=20)
        return wireguard.handshakes(result.out) if result.ok else None
    # Any Proxmox problem = "don't know" (None), never an error page: this is just a status display UwU
    except ValorError:
        return None


# GET /api/ranges/<name>/wireguard: the VPN status. Anyone with at least the viewer role may look >w<
@router.get("/ranges/{name}/wireguard")
def status(name: str, request: Request, s: Session = Depends(require("viewer"))) -> dict:
    cfg = _cfg(request)
    spec, wg = _wg(cfg, name)
    # No WireGuard in the spec = nothing else to say OwO
    if wg is None:
        return {"configured": False}
    # The saved keys (only there after the router was configured), and the live handshakes (only asked when there
    # are keys); now = the current unix time ^w^
    data = wireguard.load(cfg, spec.name)
    seen = _handshakes(cfg, spec.name) if data else None
    now = int(time.time())
    peers = []
    # One entry per peer named in the spec *counts the peers on paws*
    for peer in wg.peers:
        keys = (data or {}).get("peers", {}).get(peer)          # None until the router was configured
        # The peer's latest handshake time (None if it never connected or the router can't be asked) UwU
        handshake = (seen or {}).get(keys["public"]) if keys else None
        # A peer is "connected" when its latest handshake was less than 180 s ago. WireGuard re-handshakes about every 2
        # minutes while a tunnel is in use, so anything older means it's idle or gone >w<
        peers.append({"name": peer, "address": keys["address"] if keys else None, "ready": bool(keys),
                      "latest_handshake": handshake or None,
                      "connected": bool(handshake) and now - handshake < 180})    # WireGuard re-handshakes every 2 min
    # The full status: built yet?, the port, the VPN subnet, which segments the VPN reaches (all when not limited),
    # the public endpoint people connect to, the peers, and whether the router answered ^w^
    return {"configured": True, "built": bool(data), "port": wg.port, "network": str(wg.network),
            "reach": wg.reach or [segment.name for segment in spec.segments],
            "endpoint": wireguard.endpoint(spec, data) if data else None, "peers": peers,
            "router_reachable": seen is not None}


# GET .../wireguard/peers/<peer>: one peer's config file. Operators only (it contains a private key!) *ears perk up*
@router.get("/ranges/{name}/wireguard/peers/{peer}")
def peer_config(name: str, peer: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    spec, wg = _wg(cfg, name)
    # Unknown peer = 404 OwO
    if wg is None or peer not in wg.peers:
        raise ApiError(404, "no_peer", f"Range '{name}' has no WireGuard peer '{peer}'.")
    # Keys are created when the router is configured, so before the first apply there's nothing to hand out (409 =
    # "conflict with the current state") >w<
    data = wireguard.load(cfg, spec.name)
    if not data or peer not in data.get("peers", {}):
        raise ApiError(409, "not_built", "Apply the spec first: keys are created when the router is configured.")
    # The endpoint (public address:port) must be known to write a usable config ^w^
    endpoint = wireguard.endpoint(spec, data)
    if endpoint is None:
        raise ApiError(409, "no_endpoint", "The router's address is not known yet; re-apply the range.")
    # Build the .conf text, write an audit entry (someone downloaded VPN keys!), and return the file name, the text
    # and a QR code ("l" = low error correction, so the dense config still fits) UwU *wags tail*
    conf = wireguard.peer_conf(data, spec, peer, endpoint)
    s.audit("range.wireguard.config", target=spec.name, detail={"peer": peer})
    return {"peer": peer, "filename": f"{spec.name}-{peer}.conf", "config": conf,
            "qr_svg": qr_svg(conf, error="l")}


# POST .../wireguard/peers/<peer>/rotate: make new keys for a peer (e.g. a laptop got lost). It starts a
# "wg_rotate" engine job and returns the job, with an audit entry for the rotation >w<
@router.post("/ranges/{name}/wireguard/peers/{peer}/rotate")
def rotate(name: str, peer: str, body: RotateIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    spec, wg = _wg(cfg, name)
    if wg is None or peer not in wg.peers:
        raise ApiError(404, "no_peer", f"Range '{name}' has no WireGuard peer '{peer}'.")
    return _job(s, cfg, "wg_rotate", {"range": spec.name, "spec": f"{spec.name}.yaml", "peers": [peer]},
                "range.wireguard.rotate", {"peer": peer})
