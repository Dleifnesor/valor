"""WireGuard access to a range: peer list with live handshakes, peer configs (operators, audited), key rotation."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from .. import wireguard
from ..cluster import range_vms
from ..errors import ValorError
from ..pve import PVE
from ..spec import ROUTER
from .core import ApiError, Session, qr_svg, require
from .ranges import _cfg, _job, _load, _name

router = APIRouter(prefix="/api")


class RotateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _wg(cfg, name: str):
    spec, _ = _load(cfg, _name(name))
    wg = spec.access.wireguard if spec.access else None
    return spec, wg


def _handshakes(cfg, name: str) -> dict[str, int] | None:
    """Latest handshake per public key, read from the router (None when the router can't be asked)."""
    try:
        pve = PVE(cfg)
        vm = next((v for v in range_vms(pve, name) if v.host == ROUTER and v.status == "running"), None)
        if vm is None:
            return None
        res = pve.exec(vm.vmid, ["wg", "show", wireguard.IFACE, "dump"], timeout=20)
        return wireguard.handshakes(res.out) if res.ok else None
    except ValorError:
        return None


@router.get("/ranges/{name}/wireguard")
def status(name: str, request: Request, s: Session = Depends(require("viewer"))) -> dict:
    cfg = _cfg(request)
    spec, wg = _wg(cfg, name)
    if wg is None:
        return {"configured": False}
    data = wireguard.load(cfg, spec.name)
    seen = _handshakes(cfg, spec.name) if data else None
    now = int(time.time())
    peers = []
    for peer in wg.peers:
        p = (data or {}).get("peers", {}).get(peer)
        hs = (seen or {}).get(p["public"]) if p else None
        peers.append({"name": peer, "address": p["address"] if p else None, "ready": bool(p),
                      "latest_handshake": hs or None, "connected": bool(hs) and now - hs < 180})
    return {"configured": True, "built": bool(data), "port": wg.port, "network": str(wg.network),
            "reach": wg.reach or [x.name for x in spec.segments],
            "endpoint": wireguard.endpoint(spec, data) if data else None, "peers": peers,
            "router_reachable": seen is not None}


@router.get("/ranges/{name}/wireguard/peers/{peer}")
def peer_config(name: str, peer: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    spec, wg = _wg(cfg, name)
    if wg is None or peer not in wg.peers:
        raise ApiError(404, "no_peer", f"Range '{name}' has no WireGuard peer '{peer}'.")
    data = wireguard.load(cfg, spec.name)
    if not data or peer not in data.get("peers", {}):
        raise ApiError(409, "not_built", "Apply the spec first: keys are created when the router is configured.")
    endpoint = wireguard.endpoint(spec, data)
    if endpoint is None:
        raise ApiError(409, "no_endpoint", "The router's address is not known yet; re-apply the range.")
    conf = wireguard.peer_conf(data, spec, peer, endpoint)
    s.audit("range.wireguard.config", target=spec.name, detail={"peer": peer})
    return {"peer": peer, "filename": f"{spec.name}-{peer}.conf", "config": conf,
            "qr_svg": qr_svg(conf, error="l")}


@router.post("/ranges/{name}/wireguard/peers/{peer}/rotate")
def rotate(name: str, peer: str, body: RotateIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    spec, wg = _wg(cfg, name)
    if wg is None or peer not in wg.peers:
        raise ApiError(404, "no_peer", f"Range '{name}' has no WireGuard peer '{peer}'.")
    return _job(s, cfg, "wg_rotate", {"range": spec.name, "spec": f"{spec.name}.yaml", "peers": [peer]},
                "range.wireguard.rotate", {"peer": peer})
