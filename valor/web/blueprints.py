# *rolls out a big blueprint* OwO valor/web/blueprints.py is the web API for range BLUEPRINTS! ^w^
# A blueprint is a range spec kept as a template, e.g. "the lab for week 3". You can preview copies of it (one per
# student: names, VLANs, networks and what each plan would build), then deploy them all at once. Safety trick:
# the deploy must send back the preview's hash unchanged, so exactly what you previewed is what gets built *wags tail*
"""Range blueprints in the web UI: keep a spec as a blueprint, preview copies (names, VLANs, networks, plans) and
deploy them. Deploying runs exactly the previewed copies: the preview's hash must come back unchanged."""

# Lazy type hints UwU
from __future__ import annotations

# FastAPI router + Depends + Request; pydantic for request bodies >w<
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

# The engine's blueprints module (as bp), ops (starts jobs), vlans_in_use, ValorError, make_plan (what a build
# would do), PVE, the spec size limit and normalize, validate_cluster (does the cluster have what a spec needs),
# topology (the map), core helpers and the range loader OwO
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

# All routes under /api ^w^
router = APIRouter(prefix="/api")


# _In: request bodies refuse unknown fields UwU
class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# BlueprintIn: a blueprint as YAML (size-limited) >w<
class BlueprintIn(_In):
    yaml: str = Field(max_length=MAX_SPEC_BYTES)


# FromRangeIn: make a blueprint from an existing range: the range's name and the new blueprint's id OwO
class FromRangeIn(_In):
    range: str = Field(max_length=15)
    id: str = Field(pattern=bp.BP_ID)


# CopyIn: one copy: the new range's name and, optionally, the student it's for ^w^
class CopyIn(_In):
    name: str = Field(max_length=15)
    student: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,30}$")


# CopiesIn: 1 to MAX_COPIES copies UwU
class CopiesIn(_In):
    copies: list[CopyIn] = Field(min_length=1, max_length=bp.MAX_COPIES)


# DeployIn: the copies + the preview's hash (32 hex chars) + whether to build them right away >w<
class DeployIn(CopiesIn):
    deploy_hash: str = Field(pattern=r"^[0-9a-f]{32}$")
    build: bool = True


# _err: turn an engine error into an API error (with its details, when it has some) OwO
def _err(e: ValorError, status: int = 400) -> ApiError:
    return ApiError(status, e.code, e.message, **({"details": e.details} if e.details else {}))


# _preview: the heart of it *sniffs every copy* ^w^
def _preview(cfg, bp_id: str, copies: list[CopyIn]) -> dict:
    """The copies with their specs, whether each passes validation and what its plan would build, plus whether the
    node has room for all of them. The deploy_hash pins exactly this preview for the deploy that follows."""
    # Load the blueprint; ask the cluster which VLANs are in use (if it can't be asked, remember why) UwU
    spec, _ = bp.load(cfg, bp_id)
    pve = None
    cluster_error = None
    try:
        pve = PVE(cfg)
        live = vlans_in_use(pve)
    except ValorError as e:
        live, cluster_error = None, e.message
    # Plan the copies: names, VLANs and networks that don't clash with each other or anything else >w<
    out = bp.plan_copies(cfg, spec, [copy.model_dump() for copy in copies], live, bp_id)
    extra_memory = 0
    node = None
    # For each copy: start as ok; if the cluster is reachable, validate it against the cluster and plan it OwO
    for copy in out:
        copy["ok"], copy["errors"], copy["plan_summary"] = True, [], None
        if pve is None:
            continue
        full = normalize(copy["spec"], cfg.default_os)
        checks = validate_cluster(pve, full)
        if not checks["ok"]:
            copy["ok"], copy["errors"] = False, checks["errors"]
            continue
        # Its plan's summary, and add up the extra memory all copies would need ^w^
        plan = make_plan(pve, full)
        copy["plan_summary"] = plan["summary"]
        extra_memory += plan["node"]["additional_memory_mib"]
        node = plan["node"]
    # Capacity: would ALL copies together stay under the node's memory limit? UwU
    capacity = None
    if node:
        used = node["memory_total_mib"] - node["memory_free_mib"]
        capacity = {"additional_memory_mib": extra_memory, "memory_total_mib": node["memory_total_mib"],
                    "within_limit": used + extra_memory <= node["memory_total_mib"] * node["limit_fraction"]}
    # The preview: the copies, a cluster error (if any), the capacity, whether everything is deployable, and the
    # deploy_hash that pins exactly this preview >w<
    return {"copies": out, "cluster_error": cluster_error, "capacity": capacity,
            "ok": cluster_error is None and all(copy["ok"] for copy in out)
                  and bool(capacity and capacity["within_limit"]),
            "deploy_hash": bp.deploy_hash(out)}


# _public: the preview without the parsed spec objects (they can't be turned into JSON) OwO
def _public(preview_result: dict) -> dict:
    """The preview without the parsed spec objects (not JSON)."""
    return {**preview_result, "copies": [{key: value for key, value in copy.items() if key != "spec"}
                                         for copy in preview_result["copies"]]}


# GET /api/blueprints: every blueprint ^w^
@router.get("/blueprints")
def list_blueprints(request: Request, s: Session = Depends(require("viewer"))) -> dict:
    return {"blueprints": bp.list_all(_cfg(request))}


# GET /api/blueprints/<id>: one blueprint: its YAML, description, the ranges made from it, and its map UwU
@router.get("/blueprints/{bp_id}")
def get_blueprint(bp_id: str, request: Request, s: Session = Depends(require("viewer"))) -> dict:
    cfg = _cfg(request)
    try:
        spec, text = bp.load(cfg, bp_id)
    except ValorError as e:
        raise _err(e, 404)
    return {"id": bp_id, "yaml": text, "description": spec.description, "copies": bp.copies_of(cfg, bp_id),
            "topology": topology.build(normalize(spec, cfg.default_os))}


# PUT /api/blueprints/<id>: save (create or replace) a blueprint from YAML; audited with its size >w<
@router.put("/blueprints/{bp_id}")
def save_blueprint(bp_id: str, body: BlueprintIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    try:
        spec = bp.save(_cfg(request), bp_id, body.yaml)
    except ValorError as e:
        raise _err(e)
    s.audit("blueprint.save", target=bp_id, detail={"hosts": len(spec.hosts), "segments": len(spec.segments)})
    return {"ok": True, "id": bp_id}


# POST /api/blueprints/from-range: save an existing range's spec as a new blueprint OwO
@router.post("/blueprints/from-range")
def blueprint_from_range(body: FromRangeIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    _, text = _load(cfg, body.range)
    # a copy's marker line would make the new blueprint look like a copy of the old one
    # Drop the "# valor-blueprint:" marker line a copy carries, so the new blueprint doesn't look like a copy ^w^
    lines = [line for line in text.splitlines(keepends=True) if not line.startswith("# valor-blueprint:")]
    try:
        bp.save(cfg, body.id, "".join(lines))
    except ValorError as e:
        raise _err(e)
    s.audit("blueprint.save", target=body.id, detail={"from_range": body.range})
    return {"ok": True, "id": body.id}


# DELETE /api/blueprints/<id>: delete a blueprint (audited) UwU
@router.delete("/blueprints/{bp_id}")
def delete_blueprint(bp_id: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    try:
        bp.delete(_cfg(request), bp_id)
    except ValorError as e:
        raise _err(e, 404)
    s.audit("blueprint.delete", target=bp_id)
    return {"ok": True}


# POST /api/blueprints/<id>/preview: preview copies >w<
@router.post("/blueprints/{bp_id}/preview")
def preview(bp_id: str, body: CopiesIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    try:
        return _public(_preview(_cfg(request), bp_id, body.copies))
    except ValorError as e:
        raise _err(e)


# POST /api/blueprints/<id>/deploy: deploy the previewed copies OwO
@router.post("/blueprints/{bp_id}/deploy")
def deploy(bp_id: str, body: DeployIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    try:
        # Preview again RIGHT NOW and compare hashes: if anything changed since the person looked (another range took a
        # VLAN, the blueprint was edited...), refuse with 409 and ask them to preview again ^w^ *guards with tail*
        current = _preview(cfg, bp_id, body.copies)
    except ValorError as e:
        raise _err(e)
    if current["deploy_hash"] != body.deploy_hash:
        raise ApiError(409, "preview_changed", "The copies changed since you previewed them. Preview again.")
    # Building them needs every copy to be deployable UwU
    if body.build and not current["ok"]:
        raise ApiError(409, "not_deployable",
                       current["cluster_error"] or "Some copies can't be built; see the preview.")
    written, jobs = [], []
    # Write each copy's spec file. open(path, "x") = create, but FAIL if the file exists, so a range that appeared
    # in the meantime is never overwritten >w<
    for copy in current["copies"]:
        path = cfg.ranges_dir / f"{copy['name']}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x") as fh:                         # never overwrite a range that appeared meanwhile
            fh.write(copy["yaml"])
        written.append(copy["name"])
    # Then start one build job per copy (with verification) when asked to build OwO
    for name in written if body.build else []:
        jobs.append({"range": name, **ops.start(cfg, "apply", {"spec": f"{name}.yaml", "verify": True},
                                                 f"web:{s.username}", background=True)})
    # Audit the deploy with the ranges and jobs, and return them ^w^
    s.audit("blueprint.deploy", target=bp_id, detail={"ranges": written, "build": body.build,
                                                     "jobs": [job["job"] for job in jobs]})
    return {"ok": True, "ranges": written, "jobs": jobs}
