# *peeks at a VM's screen through the browser* OwO valor/web/consoles.py gives you VM consoles in the browser! ^w^
# The browser NEVER talks to Proxmox directly. Instead: VALOR asks Proxmox for a console session (with its own API
# token), gives the browser a one-time session id (valid 60 s, tied to your VALOR sign-in), and then RELAYS the
# websocket traffic both ways. Works for the graphical screen (VNC, shown by noVNC) and the serial console.
# Only operators and admins may open consoles, and every open/close is audited *wags tail*
"""Browser consoles: the VALOR VM relays a VM's screen (VNC) or serial console between the browser and Proxmox.

The browser only ever talks to VALOR. VALOR asks Proxmox for a console session with its API token, hands the
browser a one-time session id (valid 60 s, bound to the user's VALOR session), and relays the websocket.
Opening a console is audited; operators and admins may do it.
"""

# Lazy type hints UwU
from __future__ import annotations

# asyncio runs the two copy loops at once; secrets makes session ids; time checks deadlines; dataclass/field
# describe a pending console >w<
import asyncio
import secrets
import time
from dataclasses import dataclass, field

# FastAPI: the router, Depends, Request, and WebSocket + its disconnect error; pydantic models the request body OwO
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

# credentials (the range login, for the "Type username" button), load_catalog/range_vms (OS info and the range's
# VMs), ValorError, PVE (Proxmox), db (audit), core helpers (roles, errors, sessions, the session loader,
# require) and the spec loader from ranges.py ^w^
from .. import credentials
from ..cluster import load_catalog, range_vms
from ..errors import ValorError
from ..pve import PVE
from . import db
from .core import ROLES, ApiError, Session, _load, require
from .ranges import _load as load_range_spec

# CONNECT_WITHIN: the browser must open the websocket within 60 s of asking. MAX_DURATION: a console session ends
# after 8 hours at most UwU
router = APIRouter(tags=["consoles"])
CONNECT_WITHIN = 60          # seconds between creating a console session and opening its websocket
MAX_DURATION = 8 * 3600


# Pending: a console that was asked for but not connected yet >w<
#   owner      the VALOR session that asked (only that session may connect)
#   username   who, for the audit log; range/host/vmid = which VM
#   kind       "vnc" or "serial"
#   port/ticket/pve_user  what Proxmox handed out for its console proxy
#   created    when, to enforce CONNECT_WITHIN
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


# All pending consoles, by session id (in memory; one web process) OwO
PENDING: dict[str, Pending] = {}


# ConsoleIn: the body of "open a console": just the kind, vnc (default) or serial ^w^
class ConsoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="vnc", pattern="^(vnc|serial)$")


# _usernames: the usernames offered by the console's "Type username" button (from the range login), or none UwU
def _usernames(cfg, name: str, host: str) -> list[dict]:
    """Who signs in on this VM with the range password (none when the range has no login yet or no spec)."""
    login = credentials.load(cfg, name) if credentials.enabled(cfg) else None
    if not login:
        return []
    try:
        spec, _ = load_range_spec(cfg, name)
        return credentials.host_usernames(credentials.accounts(spec, load_catalog(cfg), login["username"]), host)
    except (ApiError, ValorError, OSError):
        return []


# _prune: forget pending consoles older than CONNECT_WITHIN >w<
def _prune() -> None:
    """Forget console sessions nobody connected to in time."""
    now = time.time()
    for sid in [sid for sid, pending in PENDING.items() if now - pending.created > CONNECT_WITHIN]:
        PENDING.pop(sid, None)


# POST /api/ranges/<name>/vms/<host>/console: ask for a console. Operators only OwO
@router.post("/api/ranges/{name}/vms/{host}/console")
def open_console(name: str, host: str, body: ConsoleIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = request.app.state.cfg
    pve = PVE(cfg)
    # Find the VM: it must belong to this range (by host name AND the range's tag) ^w^
    vm = next((vm for vm in range_vms(pve, name) if vm.host == host and f"valor-range-{name}" in vm.tags), None)
    if vm is None:
        raise ApiError(404, "no_vm", f"Range {name} has no VM {host}.")
    # A console only works on a running VM UwU
    if pve.vm_status(vm.vmid).get("status") != "running":
        raise ApiError(409, "not_running", f"{host} is not running. Start it first.")
    # Ask Proxmox to start its console proxy: vncproxy for the screen, termproxy for serial >w<
    info = pve.vncproxy(vm.vmid) if body.kind == "vnc" else pve.termproxy(vm.vmid)
    # Tidy old entries, make a random session id, and remember the pending console OwO
    _prune()
    sid = secrets.token_urlsafe(24)
    PENDING[sid] = Pending(s.id_hash, s.username, name, host, vm.vmid, body.kind, str(info["port"]), info["ticket"],
                           info.get("user", ""))
    # Audit "console.open" with the VM and the kind ^w^
    s.audit("console.open", target=f"{name}/{host}", detail={"kind": body.kind, "vmid": vm.vmid})
    # Answer the browser: the session id, the VNC password (Proxmox's one-time ticket, which noVNC uses to
    # authenticate the stream), whether a range login exists, and the usernames for the Type button UwU
    return {"session": sid, "kind": body.kind, "host": host, "vmid": vm.vmid,
            # noVNC authenticates the VNC stream with Proxmox's one-time ticket (valid for this port only)
            "vnc_password": info["ticket"] if body.kind == "vnc" else None,
            "login": bool(credentials.load(cfg, name)) if credentials.enabled(cfg) else False,
            "usernames": _usernames(cfg, name, host)}


# The websocket /api/console/<sid>: where the relaying happens *rolls up sleeves* >w<
@router.websocket("/api/console/{sid}")
async def relay(websocket: WebSocket, sid: str):
    """Check the browser (same origin, same VALOR session that asked for the console, in time), connect to
    Proxmox's console websocket with VALOR's token, then copy data both ways until one side closes."""
    # Cross-site websocket hijacking guard: websockets aren't covered by the usual same-origin rules, so check the
    # Origin header ourselves; a foreign origin is closed with code 4403 OwO
    origin = websocket.headers.get("origin", "")
    if origin and origin.split("://", 1)[-1] != websocket.headers.get("host", ""):
        await websocket.close(code=4403)               # cross-site websocket hijacking guard
        return
    # Load the VALOR session from the websocket's cookie ^w^
    conn = db.connect(websocket.app.state.wcfg.db)
    try:
        s = _load(websocket, conn)
    finally:
        conn.close()
    # pop() removes the pending entry, so each session id can be used only ONCE UwU
    pending = PENDING.pop(sid, None)                  # one use only
    # Refuse (code 4401) unless: signed in fully, operator or higher, a pending console exists, it was asked for by
    # THIS same session, and in time >w< *guards the tunnel*
    if (s is None or s.stage != "full" or ROLES[s.user["role"]] < ROLES["operator"] or pending is None
            or pending.owner != s.id_hash or time.time() - pending.created > CONNECT_WITHIN):
        await websocket.close(code=4401)
        return
    # The websocket client library, imported only when needed OwO
    from websockets.asyncio.client import connect

    # Where Proxmox's console websocket is, with the auth headers and the TLS settings ^w^
    pve = PVE(websocket.app.state.cfg)
    url, headers, ssl_context = pve.console_websocket(pending.vmid, pending.port, pending.ticket)
    upstream = None
    # Connect to Proxmox. Its proxy sometimes needs a moment to start listening, so try twice (1 s apart); a second
    # failure closes the browser's socket with 1011 (server error) UwU
    for attempt in range(2):                           # Proxmox's proxy can need a moment to start listening
        try:
            upstream = await connect(url, additional_headers=headers, ssl=ssl_context, subprotocols=["binary"],
                                     max_size=None, open_timeout=15, ping_interval=None)
            break
        except Exception:
            if attempt:
                await websocket.close(code=1011)
                return
            await asyncio.sleep(1)
    # Accept the browser's websocket, agreeing on the "binary" subprotocol when the browser offers it (noVNC does) >w<
    wanted = websocket.scope.get("subprotocols") or []
    await websocket.accept(subprotocol="binary" if "binary" in wanted else None)
    started = time.time()
    # Serial consoles: Proxmox's terminal proxy first expects "user:ticket" as a login line OwO
    if pending.kind == "serial":                       # Proxmox's terminal proxy wants "user:ticket" first
        await upstream.send(f"{pending.pve_user}:{pending.ticket}\n".encode())

    # browser_to_pve: copy everything the browser sends (bytes or text) up to Proxmox, until it disconnects ^w^
    async def browser_to_pve():
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
            data = msg.get("bytes") if msg.get("bytes") is not None else msg.get("text")
            if data is not None:
                await upstream.send(data)

    # pve_to_browser: copy everything from Proxmox down to the browser, keeping bytes as bytes and text as text UwU
    async def pve_to_browser():
        async for data in upstream:
            if isinstance(data, bytes):
                await websocket.send_bytes(data)
            else:
                await websocket.send_text(data)

    # Run both copy loops at once; stop when EITHER side ends (or after MAX_DURATION) >w<
    tasks = [asyncio.create_task(browser_to_pve()), asyncio.create_task(pve_to_browser())]
    try:
        await asyncio.wait(tasks, timeout=MAX_DURATION, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, ValorError):
        pass
    # Clean up no matter what: stop both loops, close both sockets (ignoring errors from one that's already closed),
    # and audit "console.close" with how long it was open OwO *waves bye to the VM*
    finally:
        for task in tasks:
            task.cancel()
        await upstream.close()
        try:
            await websocket.close()
        except Exception:
            pass
        conn = db.connect(websocket.app.state.wcfg.db)
        try:
            db.audit(conn, "console.close", "ok", username=pending.username, target=f"{pending.range}/{pending.host}",
                     detail={"kind": pending.kind, "seconds": round(time.time() - started)})
        finally:
            conn.close()
