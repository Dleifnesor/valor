"""Browser consoles: the VALOR VM relays a VM's screen (VNC) or serial console between the browser and Proxmox.

The browser only ever talks to VALOR. VALOR asks Proxmox for a console session with its API token, hands the
browser a one-time session id (valid 60 s, bound to the user's VALOR session), and relays the websocket.
Opening a console is audited; operators and admins may do it.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from .. import credentials
from ..cluster import range_vms
from ..errors import ValorError
from ..pve import PVE
from . import db
from .core import ROLES, ApiError, Session, _load, require

router = APIRouter(tags=["consoles"])
CONNECT_WITHIN = 60          # seconds between creating a console session and opening its websocket
MAX_DURATION = 8 * 3600
KINDS = ("vnc", "serial")


@dataclass
class Pending:
    owner: str                # VALOR session id_hash
    username: str
    range: str
    host: str
    vmid: int
    kind: str
    port: str
    ticket: str
    pve_user: str
    created: float = field(default_factory=time.time)


PENDING: dict[str, Pending] = {}


class ConsoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="vnc", pattern="^(vnc|serial)$")


def _prune() -> None:
    now = time.time()
    for k in [k for k, p in PENDING.items() if now - p.created > CONNECT_WITHIN]:
        PENDING.pop(k, None)


@router.post("/api/ranges/{name}/vms/{host}/console")
def open_console(name: str, host: str, body: ConsoleIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = request.app.state.cfg
    pve = PVE(cfg)
    vm = next((v for v in range_vms(pve, name) if v.host == host and f"valor-range-{name}" in v.tags), None)
    if vm is None:
        raise ApiError(404, "no_vm", f"Range {name} has no VM {host}.")
    if pve.vm_status(vm.vmid).get("status") != "running":
        raise ApiError(409, "not_running", f"{host} is not running. Start it first.")
    info = pve.vncproxy(vm.vmid) if body.kind == "vnc" else pve.termproxy(vm.vmid)
    _prune()
    sid = secrets.token_urlsafe(24)
    PENDING[sid] = Pending(s.id_hash, s.username, name, host, vm.vmid, body.kind, str(info["port"]), info["ticket"],
                           info.get("user", ""))
    s.audit("console.open", target=f"{name}/{host}", detail={"kind": body.kind, "vmid": vm.vmid})
    return {"session": sid, "kind": body.kind, "host": host, "vmid": vm.vmid,
            # noVNC authenticates the VNC stream with Proxmox's one-time ticket (valid for this port only)
            "vnc_password": info["ticket"] if body.kind == "vnc" else None,
            "login": bool(credentials.load(cfg, name)) if credentials.enabled(cfg) else False}


@router.websocket("/api/console/{sid}")
async def relay(websocket: WebSocket, sid: str):
    origin = websocket.headers.get("origin", "")
    if origin and origin.split("://", 1)[-1] != websocket.headers.get("host", ""):
        await websocket.close(code=4403)               # cross-site websocket hijacking guard
        return
    conn = db.connect(websocket.app.state.wcfg.db)
    try:
        s = _load(websocket, conn)
    finally:
        conn.close()
    p = PENDING.pop(sid, None)
    if (s is None or s.stage != "full" or ROLES[s.user["role"]] < ROLES["operator"] or p is None
            or p.owner != s.id_hash or time.time() - p.created > CONNECT_WITHIN):
        await websocket.close(code=4401)
        return
    from websockets.asyncio.client import connect

    pve = PVE(websocket.app.state.cfg)
    url, headers, ctx = pve.console_websocket(p.vmid, p.port, p.ticket)
    upstream = None
    for attempt in range(2):                           # Proxmox's proxy can need a moment to start listening
        try:
            upstream = await connect(url, additional_headers=headers, ssl=ctx, subprotocols=["binary"],
                                     max_size=None, open_timeout=15, ping_interval=None)
            break
        except Exception:
            if attempt:
                await websocket.close(code=1011)
                return
            await asyncio.sleep(1)
    wanted = websocket.scope.get("subprotocols") or []
    await websocket.accept(subprotocol="binary" if "binary" in wanted else None)
    started = time.time()
    if p.kind == "serial":
        await upstream.send(f"{p.pve_user}:{p.ticket}\n".encode())

    async def browser_to_pve():
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
            data = msg.get("bytes") if msg.get("bytes") is not None else msg.get("text")
            if data is not None:
                await upstream.send(data)

    async def pve_to_browser():
        async for data in upstream:
            if isinstance(data, bytes):
                await websocket.send_bytes(data)
            else:
                await websocket.send_text(data)

    tasks = [asyncio.create_task(browser_to_pve()), asyncio.create_task(pve_to_browser())]
    try:
        await asyncio.wait(tasks, timeout=MAX_DURATION, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, ValorError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        await upstream.close()
        try:
            await websocket.close()
        except Exception:
            pass
        conn = db.connect(websocket.app.state.wcfg.db)
        try:
            db.audit(conn, "console.close", "ok", username=p.username, target=f"{p.range}/{p.host}",
                     detail={"kind": p.kind, "seconds": round(time.time() - started)})
        finally:
            conn.close()
