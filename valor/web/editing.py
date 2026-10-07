"""Editing a built range on the map: its draft (pending changes) and the structured edits the map's controls send.
Nothing here touches the cluster; the draft is built through plan -> approve (ranges.plan/apply with draft=true)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

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

router = APIRouter(prefix="/api")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleIn(_In):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,40}$")
    params: dict[str, Any] = Field(default_factory=dict)


class Op(_In):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    op: Literal["add_host", "update_host", "remove_host", "add_role", "remove_role",
                "add_segment", "update_segment", "remove_segment", "add_rule", "remove_rule"]
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
    vlan: int | None = Field(default=None, ge=2, le=4094)
    cidr: str | None = Field(default=None, max_length=18)
    internet: bool | None = None
    vpn_reach: bool | None = None
    with_hosts: bool = False
    # traffic rules
    from_: str | None = Field(default=None, alias="from", max_length=15)
    to: str | None = Field(default=None, max_length=15)
    proto: Literal["tcp", "udp", "icmp", "any"] | None = None
    ports: list[int | str] = Field(default_factory=list, max_length=20)
    index: int | None = Field(default=None, ge=0, le=500)


class OpsIn(_In):
    ops: list[Op] = Field(min_length=1, max_length=20)


class DraftIn(_In):
    yaml: str = Field(max_length=MAX_SPEC_BYTES)


def _alloc(cfg) -> dict:
    """VLANs and networks taken by every range, the cluster and live VMs - for new segments."""
    try:
        live = vlans_in_use(PVE(cfg))
    except ValorError:
        live = {}
    vlans, nets = _taken(cfg, live)
    return {"vlans": set(vlans), "nets": [n for n, _ in nets], "vlan_range": (cfg.vlan_min, cfg.vlan_max)}


def _vms(cfg, name: str) -> dict:
    try:
        return {vm.host: {"vmid": vm.vmid, "status": vm.status} for vm in range_vms(PVE(cfg), name) if vm.host}
    except ValorError:
        return {}


def view(cfg, name: str) -> dict:
    """The range as the map should show it: with its draft (and what the draft changes) when there is one."""
    current, current_text = _load(cfg, name)
    d = drafts.load(cfg, name)
    if d is None:
        return {"draft": None, "topology": topology.build(current, _vms(cfg, name))}
    text, meta = d
    try:
        draft = normalize(parse_spec(text), cfg.default_os)
    except ValorError as e:
        return {"draft": {"yaml": text, "meta": meta, "valid": False, "error": e.message, "errors": e.details},
                "topology": topology.build(current, _vms(cfg, name))}
    ch = drafts.changes(current, draft)
    return {"draft": {"yaml": text, "meta": meta, "valid": True, "summary": ch["summary"], "actions": ch["actions"],
                      "diff": drafts.unified_diff(drafts.spec_yaml(current), drafts.spec_yaml(draft), name)},
            "topology": topology.build(draft, _vms(cfg, name), ch)}


@router.get("/ranges/{name}/draft")
def get_draft(name: str, request: Request, s: Session = Depends(require("viewer"))) -> dict:
    return view(_cfg(request), _name(name))


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


@router.post("/ranges/{name}/draft/ops")
def edit(name: str, body: OpsIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    """Map controls: add/remove VMs and services. Applied to the draft (started from the saved spec)."""
    cfg = _cfg(request)
    _, saved = _load(cfg, _name(name))
    d = drafts.load(cfg, name)
    base = parse_spec(d[0] if d else saved)             # unnormalized: hosts without an OS keep the default
    loaded = {}
    for o in body.ops:
        for r in [o.role, *(x.name for x in o.roles)]:
            if r and r not in loaded:
                try:
                    loaded[r] = load_role(cfg.roles_dir, r)
                except ValorError:
                    raise ApiError(400, "unknown_role", f"There is no service (role) '{r}'.")

    def ports(role: str, params: dict) -> list[int]:
        return role_ports(loaded[role], params) if role in loaded else []
    try:
        segment_ops = any(o.op in ("add_segment", "update_segment") for o in body.ops)
        data = drafts.apply_ops(base, [o.model_dump(exclude_none=True, by_alias=True) for o in body.ops], ports,
                                _alloc(cfg) if segment_ops else None)
        text = drafts.to_yaml(data)
        drafts.save(cfg, name, text, s.username, "map")
    except ValorError as e:
        raise ApiError(400, e.code, e.message, **({"details": e.details} if e.details else {}))
    s.audit("range.draft.edit", target=name, detail={"ops": [o.op for o in body.ops]})
    return view(cfg, name)


@router.get("/ranges/{name}/draft/suggest-segment")
def suggest_segment(name: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    """A free VLAN and private network for the Add-segment form (editable there)."""
    cfg = _cfg(request)
    _, saved = _load(cfg, _name(name))
    d = drafts.load(cfg, name)
    data = parse_spec(d[0] if d else saved).model_dump(mode="json", by_alias=True, exclude_none=True)
    try:
        return {**drafts.suggest_segment(data, _alloc(cfg)), "vlan_range": [cfg.vlan_min, cfg.vlan_max]}
    except ValorError as e:
        raise ApiError(409, e.code, e.message)


@router.delete("/ranges/{name}/draft")
def discard(name: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    drafts.discard(cfg, _name(name))
    s.audit("range.draft.discard", target=name)
    return view(cfg, name)
