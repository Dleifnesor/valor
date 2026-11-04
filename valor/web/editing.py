# *grabs a tiny paintbrush for the map* OwO valor/web/editing.py lets you EDIT a built range on the map! ^w^
# Your edits never touch the real VMs directly. They go into the range's DRAFT (a copy of the spec with your pending
# changes). The map shows the draft and what it would change; nothing is built until someone approves the plan
# (plan -> approve, like any other change) *wags tail*
"""Editing a built range on the map: its draft (pending changes) and the structured edits the map's controls send.
Nothing here touches the cluster; the draft is built through plan -> approve (ranges.plan/apply with draft=true)."""

# Lazy type hints UwU
from __future__ import annotations

# Any = any value (role parameters); Literal = exactly one of some strings >w<
from typing import Any, Literal

# FastAPI router + Depends + Request; pydantic models for the request bodies OwO
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

# drafts = the draft storage and the edit operations; _taken lists VLANs/networks already used; range_vms /
# vlans_in_use read the cluster; load_role/role_ports read roles (to know which ports a service needs); the spec
# size limit, normalize and parse_spec; topology builds the map; core/ranges helpers ^w^
from .. import drafts
from ..blueprints import _taken
from ..cluster import range_vms, vlans_in_use
from ..errors import ValorError
from ..pve import PVE
from ..roles import load_role, role_ports
from ..spec import MAX_SPEC_BYTES, normalize, parse_spec
from . import topology
from .core import ApiError, Session, require
from .ranges import _cfg, _load, _name

# All routes under /api UwU
router = APIRouter(prefix="/api")


# _In: request bodies here refuse unknown fields >w<
class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# RoleIn: a role on a new VM: its name (checked pattern) and parameters OwO
class RoleIn(_In):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,40}$")
    params: dict[str, Any] = Field(default_factory=dict)


# Op: ONE map edit. `op` says which kind; the other fields are used by some kinds and not others ^w^
# populate_by_name lets `from_` be filled from "from" (from is a Python keyword, so the field needs another name)
class Op(_In):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    op: Literal["add_host", "update_host", "remove_host", "add_role", "remove_role",
                "add_segment", "update_segment", "remove_segment", "add_rule", "remove_rule"]
    # Fields for VMs: name/host/segment/address (max 15 chars each), os, size limits (1-16 vCPU, 512 MiB-64 GiB RAM,
    # 8-500 GiB disk), description, roles, a single role + params, and allow_from (segments allowed to reach the new
    # services) UwU
    name: str | None = Field(default=None, max_length=15)
    host: str | None = Field(default=None, max_length=15)
    segment: str | None = Field(default=None, max_length=15)
    address: str | None = Field(default=None, max_length=15)
    os: str | None = Field(default=None, max_length=64)
    cores: int | None = Field(default=None, ge=1, le=16)
    memory: int | None = Field(default=None, ge=512, le=65536)
    disk: int | None = Field(default=None, ge=8, le=500)
    description: str | None = Field(default=None, max_length=200)
    roles: list[RoleIn] = Field(default_factory=list, max_length=10)
    role: str | None = Field(default=None, max_length=41)
    params: dict[str, Any] = Field(default_factory=dict)
    allow_from: list[str] = Field(default_factory=list, max_length=20)
    # segments
    # Fields for segments: VLAN tag (2-4094), CIDR, internet access, reachable over the VPN, and with_hosts (remove a
    # segment's VMs along with it) >w<
    vlan: int | None = Field(default=None, ge=2, le=4094)
    cidr: str | None = Field(default=None, max_length=18)
    internet: bool | None = None
    vpn_reach: bool | None = None
    with_hosts: bool = False
    # traffic rules
    # Fields for traffic rules: from, to, protocol, ports and index (which rule, for remove_rule) OwO
    from_: str | None = Field(default=None, alias="from", max_length=15)
    to: str | None = Field(default=None, max_length=15)
    proto: Literal["tcp", "udp", "icmp", "any"] | None = None
    ports: list[int | str] = Field(default_factory=list, max_length=20)
    index: int | None = Field(default=None, ge=0, le=500)


# OpsIn: 1 to 20 edits in one request ^w^
class OpsIn(_In):
    ops: list[Op] = Field(min_length=1, max_length=20)


# DraftIn: a whole draft spec as YAML (size-limited) UwU
class DraftIn(_In):
    yaml: str = Field(max_length=MAX_SPEC_BYTES)


# _alloc: what new segments must avoid: VLANs and networks taken by every range, the cluster and live VMs >w<
def _alloc(cfg) -> dict:
    """VLANs and networks taken by every range, the cluster and live VMs - for new segments."""
    try:
        # If Proxmox can't be asked, go on with what the saved specs say OwO
        live = vlans_in_use(PVE(cfg))
    except ValorError:
        live = {}
    vlans, nets = _taken(cfg, live)
    return {"vlans": set(vlans), "nets": [net for net, _ in nets], "vlan_range": (cfg.vlan_min, cfg.vlan_max)}


# _vms: host -> {vmid, status} for the map; {} if Proxmox can't be asked ^w^
def _vms(cfg, name: str) -> dict:
    try:
        return {vm.host: {"vmid": vm.vmid, "status": vm.status} for vm in range_vms(PVE(cfg), name) if vm.host}
    except ValorError:
        return {}


# view: the range as the map shows it: just the range when there's no draft; otherwise the draft, whether it's
# valid, a summary of the changes, the per-host actions, a YAML diff and the map with change colors UwU
def view(cfg, name: str) -> dict:
    """The range as the map should show it: with its draft (and what the draft changes) when there is one."""
    current, current_text = _load(cfg, name)
    stored = drafts.load(cfg, name)
    if stored is None:
        return {"draft": None, "topology": topology.build(current, _vms(cfg, name))}
    text, meta = stored
    # An invalid draft (e.g. hand-edited YAML with a mistake) is still shown, with the error, over the current map >w<
    try:
        draft = normalize(parse_spec(text), cfg.default_os)
    except ValorError as e:
        return {"draft": {"yaml": text, "meta": meta, "valid": False, "error": e.message, "errors": e.details},
                "topology": topology.build(current, _vms(cfg, name))}
    # What the draft changes compared to the current spec OwO
    changes = drafts.changes(current, draft)
    return {"draft": {"yaml": text, "meta": meta, "valid": True, "summary": changes["summary"],
                      "actions": changes["actions"],
                      "diff": drafts.unified_diff(drafts.spec_yaml(current), drafts.spec_yaml(draft), name)},
            "topology": topology.build(draft, _vms(cfg, name), changes)}


# GET /api/ranges/<name>/draft: the view (viewers may look) ^w^
@router.get("/ranges/{name}/draft")
def get_draft(name: str, request: Request, s: Session = Depends(require("viewer"))) -> dict:
    return view(_cfg(request), _name(name))


# PUT /api/ranges/<name>/draft: save a whole draft from YAML (operators). The range must exist; bad YAML gives a
# 400 with the problems; then audit and return the new view UwU
@router.put("/ranges/{name}/draft")
def put_draft(name: str, body: DraftIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    _load(cfg, _name(name))
    try:
        drafts.save(cfg, name, body.yaml, s.username, "yaml")
    except ValorError as e:
        raise ApiError(400, e.code, e.message, **({"details": e.details} if e.details else {}))
    s.audit("range.draft.save", target=name, detail={"source": "yaml"})
    return view(cfg, name)


# POST /api/ranges/<name>/draft/ops: apply map edits to the draft (operators) >w<
@router.post("/ranges/{name}/draft/ops")
def edit(name: str, body: OpsIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    """Map controls: add/remove VMs and services. Applied to the draft (started from the saved spec)."""
    cfg = _cfg(request)
    # Start from the existing draft, or from the saved spec when there's none OwO
    _, saved = _load(cfg, _name(name))
    draft = drafts.load(cfg, name)
    base = parse_spec(draft[0] if draft else saved)     # unnormalized: hosts without an OS keep the default
    # Every role the edits name must exist; their ports open the rules for `allow_from`.
    # Load every role the edits mention; an unknown role is a clear 400 ^w^
    loaded = {}
    for op in body.ops:
        for role in [op.role, *(ref.name for ref in op.roles)]:
            if role and role not in loaded:
                try:
                    loaded[role] = load_role(cfg.roles_dir, role)
                except ValorError:
                    raise ApiError(400, "unknown_role", f"There is no service (role) '{role}'.")

    # ports(): which ports a role with these params listens on (so allow_from can open exactly those) UwU
    def ports(role: str, params: dict) -> list[int]:
        return role_ports(loaded[role], params) if role in loaded else []
    try:
        # Free VLANs/networks are only looked up when an edit adds or changes a segment (it asks the cluster) >w<
        segment_ops = any(op.op in ("add_segment", "update_segment") for op in body.ops)
        data = drafts.apply_ops(base, [op.model_dump(exclude_none=True, by_alias=True) for op in body.ops], ports,
                                _alloc(cfg) if segment_ops else None)
        # Apply the edits, turn the result back into YAML, save it as the draft (source "map"), audit, and return the view OwO
        text = drafts.to_yaml(data)
        drafts.save(cfg, name, text, s.username, "map")
    except ValorError as e:
        raise ApiError(400, e.code, e.message, **({"details": e.details} if e.details else {}))
    s.audit("range.draft.edit", target=name, detail={"ops": [op.op for op in body.ops]})
    return view(cfg, name)


# GET .../draft/suggest-segment: a free VLAN and private network to prefill the Add-segment form ^w^
@router.get("/ranges/{name}/draft/suggest-segment")
def suggest_segment(name: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    """A free VLAN and private network for the Add-segment form (editable there)."""
    cfg = _cfg(request)
    _, saved = _load(cfg, _name(name))
    draft = drafts.load(cfg, name)
    data = parse_spec(draft[0] if draft else saved).model_dump(mode="json", by_alias=True, exclude_none=True)
    try:
        return {**drafts.suggest_segment(data, _alloc(cfg)), "vlan_range": [cfg.vlan_min, cfg.vlan_max]}
    except ValorError as e:
        raise ApiError(409, e.code, e.message)


# DELETE /api/ranges/<name>/draft: throw the draft away (operators), back to the saved spec UwU *crumples the draft*
@router.delete("/ranges/{name}/draft")
def discard(name: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    drafts.discard(cfg, _name(name))
    s.audit("range.draft.discard", target=name)
    return view(cfg, name)
