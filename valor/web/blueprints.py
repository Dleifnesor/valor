"""Range blueprints in the web UI: keep a spec as a blueprint, preview copies (names, VLANs, networks, plans) and
deploy them. Deploying runs exactly the previewed copies: the preview's hash must come back unchanged."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import blueprints as bp
from .. import ops
from ..cluster import vlans_in_use
from ..errors import ValorError
from ..plan import make_plan
from ..pve import PVE
from ..spec import MAX_SPEC_BYTES, normalize
from ..validate import validate_cluster
from . import topology
from .core import ApiError, Session, require
from .ranges import _cfg, _load

router = APIRouter(prefix="/api")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlueprintIn(_In):
    yaml: str = Field(max_length=MAX_SPEC_BYTES)


class FromRangeIn(_In):
    range: str = Field(max_length=15)
    id: str = Field(pattern=bp.BP_ID)


class CopyIn(_In):
    name: str = Field(max_length=15)
    student: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,30}$")


class CopiesIn(_In):
    copies: list[CopyIn] = Field(min_length=1, max_length=bp.MAX_COPIES)


class DeployIn(CopiesIn):
    deploy_hash: str = Field(pattern=r"^[0-9a-f]{32}$")
    build: bool = True


def _err(e: ValorError, status: int = 400) -> ApiError:
    return ApiError(status, e.code, e.message, **({"details": e.details} if e.details else {}))


def _preview(cfg, bp_id: str, copies: list[CopyIn]) -> dict:
    spec, _ = bp.load(cfg, bp_id)
    pve = None
    cluster_error = None
    try:
        pve = PVE(cfg)
        live = vlans_in_use(pve)
    except ValorError as e:
        live, cluster_error = None, e.message
    out = bp.plan_copies(cfg, spec, [c.model_dump() for c in copies], live, bp_id)
    extra_memory = 0
    node = None
    for c in out:
        c["ok"], c["errors"], c["plan_summary"] = True, [], None
        if pve is None:
            continue
        full = normalize(c["spec"], cfg.default_os)
        v = validate_cluster(pve, full)
        if not v["ok"]:
            c["ok"], c["errors"] = False, v["errors"]
            continue
        plan = make_plan(pve, full)
        c["plan_summary"] = plan["summary"]
        extra_memory += plan["node"]["additional_memory_mib"]
        node = plan["node"]
    capacity = None
    if node:
        used = node["memory_total_mib"] - node["memory_free_mib"]
        capacity = {"additional_memory_mib": extra_memory, "memory_total_mib": node["memory_total_mib"],
                    "within_limit": used + extra_memory <= node["memory_total_mib"] * node["limit_fraction"]}
    return {"copies": out, "cluster_error": cluster_error, "capacity": capacity,
            "ok": cluster_error is None and all(c["ok"] for c in out) and bool(capacity and capacity["within_limit"]),
            "deploy_hash": bp.deploy_hash(out)}


def _public(p: dict) -> dict:
    return {**p, "copies": [{k: v for k, v in c.items() if k != "spec"} for c in p["copies"]]}


@router.get("/blueprints")
def list_blueprints(request: Request, s: Session = Depends(require("viewer"))) -> dict:
    return {"blueprints": bp.list_all(_cfg(request))}


@router.get("/blueprints/{bp_id}")
def get_blueprint(bp_id: str, request: Request, s: Session = Depends(require("viewer"))) -> dict:
    cfg = _cfg(request)
    try:
        spec, text = bp.load(cfg, bp_id)
    except ValorError as e:
        raise _err(e, 404)
    return {"id": bp_id, "yaml": text, "description": spec.description, "copies": bp.copies_of(cfg, bp_id),
            "topology": topology.build(normalize(spec, cfg.default_os))}


@router.put("/blueprints/{bp_id}")
def save_blueprint(bp_id: str, body: BlueprintIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    try:
        spec = bp.save(_cfg(request), bp_id, body.yaml)
    except ValorError as e:
        raise _err(e)
    s.audit("blueprint.save", target=bp_id, detail={"hosts": len(spec.hosts), "segments": len(spec.segments)})
    return {"ok": True, "id": bp_id}


@router.post("/blueprints/from-range")
def blueprint_from_range(body: FromRangeIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    _, text = _load(cfg, body.range)
    lines = [l for l in text.splitlines(keepends=True) if not l.startswith("# valor-blueprint:")]
    try:
        bp.save(cfg, body.id, "".join(lines))
    except ValorError as e:
        raise _err(e)
    s.audit("blueprint.save", target=body.id, detail={"from_range": body.range})
    return {"ok": True, "id": body.id}


@router.delete("/blueprints/{bp_id}")
def delete_blueprint(bp_id: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    try:
        bp.delete(_cfg(request), bp_id)
    except ValorError as e:
        raise _err(e, 404)
    s.audit("blueprint.delete", target=bp_id)
    return {"ok": True}


@router.post("/blueprints/{bp_id}/preview")
def preview(bp_id: str, body: CopiesIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    try:
        return _public(_preview(_cfg(request), bp_id, body.copies))
    except ValorError as e:
        raise _err(e)


@router.post("/blueprints/{bp_id}/deploy")
def deploy(bp_id: str, body: DeployIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    try:
        p = _preview(cfg, bp_id, body.copies)
    except ValorError as e:
        raise _err(e)
    if p["deploy_hash"] != body.deploy_hash:
        raise ApiError(409, "preview_changed", "The copies changed since you previewed them. Preview again.")
    if body.build and not p["ok"]:
        raise ApiError(409, "not_deployable", p["cluster_error"] or "Some copies can't be built; see the preview.")
    written, jobs = [], []
    for c in p["copies"]:
        path = cfg.ranges_dir / f"{c['name']}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x") as fh:                         # never overwrite a range that appeared meanwhile
            fh.write(c["yaml"])
        written.append(c["name"])
    for name in written if body.build else []:
        jobs.append({"range": name, **ops.start(cfg, "apply", {"spec": f"{name}.yaml", "verify": True},
                                                 f"web:{s.username}", background=True)})
    s.audit("blueprint.deploy", target=bp_id, detail={"ranges": written, "build": body.build,
                                                     "jobs": [j["job"] for j in jobs]})
    return {"ok": True, "ranges": written, "jobs": jobs}
