# *rolls out the range map* OwO valor/web/ranges.py is the API for RANGES! ^w^
# Specs, plans, approved builds, verification, teardown, logins, power, snapshots and jobs. Every change to the
# cluster goes through an engine job. The big safety rule lives here: a build runs ONLY the plan the person
# reviewed. The plan comes with a plan_hash; apply recomputes the plan and refuses (409) if anything changed in the
# meantime *guards the approve button with fluffy tail*
"""Ranges, specs, plans, approved builds and jobs. Every change to the cluster goes through an engine job.

The approval rule lives here: a build runs only the plan the person reviewed. The plan page shows the plan and its
`plan_hash`; apply recomputes the plan and refuses (409 plan_changed) if anything differs, e.g. because someone
edited the spec or a VM changed in the meantime.
"""

# Lazy type hints UwU
from __future__ import annotations

# hashlib/json (the plan hash), os (atomic replace), re (names, job ids), Path >w<
import hashlib
import json
import os
import re
from pathlib import Path

# FastAPI and pydantic OwO
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

# Engine modules: credentials, drafts, jobs, lifecycle, ops, state; cluster helpers; ValorError; baselines;
# make_plan; PVE; load_role; spec helpers; validate_cluster; then topology and core helpers ^w^
from .. import credentials, drafts, jobs, lifecycle, ops, state
from ..cluster import load_catalog, range_vms, ranges_overview, templates
from ..errors import ValorError
from ..baseline import load_baseline
from ..plan import make_plan
from ..pve import PVE
from ..roles import load_role
from ..spec import MAX_SPEC_BYTES, NAME, effective_tests, normalize, parse_spec, spec_hash
from ..validate import validate_cluster
from . import topology
from .core import ApiError, Session, require

# All routes under /api; NAME_RE checks range names UwU
router = APIRouter(prefix="/api", tags=["ranges"])
NAME_RE = re.compile(NAME)


# _In: request bodies refuse unknown fields >w<
class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# SpecIn: a spec's YAML (size-limited) OwO
class SpecIn(_In):
    yaml: str = Field(min_length=1, max_length=MAX_SPEC_BYTES)


# ApplyIn: the plan_hash the person approved, and whether to build the draft instead of the saved spec ^w^
class ApplyIn(_In):
    plan_hash: str = Field(min_length=16, max_length=64)
    draft: bool = False             # build the range's draft (map edits / chat) instead of its saved spec


# PlanIn: plan the draft? UwU
class PlanIn(_In):
    draft: bool = False


# DestroyIn / RollbackIn: the range's name typed again to confirm (and for rollback: may newer snapshots be
# deleted?) >w<
class DestroyIn(_In):
    confirm: str = Field(max_length=64)


# PowerIn: one of four actions, optionally for some hosts OwO
class PowerIn(_In):
    action: str = Field(pattern="^(start|shutdown|stop|reboot)$")
    hosts: list[str] | None = Field(default=None, max_length=50)


# SnapshotIn: a snapshot name and description ^w^
class SnapshotIn(_In):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,39}$")
    description: str = Field(default="", max_length=200)


class RollbackIn(_In):
    confirm: str = Field(max_length=64)
    delete_newer: bool = False


# role_list: every role (Linux and/or Windows) with its description, ports, params, families and category UwU
def role_list(cfg) -> list[dict]:
    """Every role (Linux role.sh and/or Windows role.ps1) with the OS families it supports."""
    roles = []
    for folder in sorted(cfg.roles_dir.iterdir()):
        if (folder / "role.sh").is_file() or (folder / "role.ps1").is_file():
            role = load_role(cfg.roles_dir, folder.name)
            meta = role.meta
            roles.append({"name": folder.name, "description": meta.get("description", ""),
                          "ports": meta.get("ports", []), "ports_param": meta.get("ports_param"),
                          "params": meta.get("params", {}), "families": role.families,
                          "category": meta.get("category", "service")})
    return roles


# _cfg: the engine config >w<
def _cfg(request: Request):
    return request.app.state.cfg


# _name: check a range name from the URL BEFORE it's used in a file path OwO
def _name(name: str) -> str:
    """A range name from the URL, checked before it becomes part of a file path."""
    if not NAME_RE.fullmatch(name):
        raise ApiError(400, "invalid_name", "Range names are 2-15 lowercase letters, digits or dashes.")
    return name


# _spec_path: ranges/<name>.yaml ^w^
def _spec_path(cfg, name: str) -> Path:
    return cfg.ranges_dir / f"{_name(name)}.yaml"


# _load: (normalized spec, YAML text) or 404 UwU
def _load(cfg, name: str):
    """(the range's normalized spec, its YAML text); 404 when the range has no spec."""
    path = _spec_path(cfg, name)
    if not path.is_file():
        raise ApiError(404, "no_spec", f"There is no spec for range '{name}'.")
    text = path.read_text()
    return normalize(parse_spec(text), cfg.default_os), text


# _vm_states: live status per VM, asked per VM (the cluster-wide list can lag several seconds behind power
# changes) >w<
def _vm_states(pve: PVE, name: str) -> dict[str, dict]:
    """Live status per VM (the cluster-wide resource list can lag behind power changes by several seconds)."""
    out = {}
    for vm in range_vms(pve, name):
        if not vm.host:
            continue
        try:
            status = pve.vm_status(vm.vmid).get("status", vm.status)
        except ValorError:
            status = vm.status
        out[vm.host] = {"vmid": vm.vmid, "status": status, "name": vm.name, "converged": bool(vm.meta.get("conv")),
                        "spec": (vm.meta.get("spec") or "")[:12]}
    return out


# _plan_hash: what the person approves: the spec's hash + (host, action, vmid) for every action, hashed OwO
def _plan_hash(spec, plan: dict) -> str:
    """What the person approves: the spec version and, per VM, the action and the VMID it applies to."""
    key = {"spec": spec_hash(spec),
           "actions": [(action["host"], action["action"], action.get("vmid")) for action in plan["actions"]]}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:32]


# _summary: the last build and last verification, briefly ^w^
def _summary(cfg, name: str) -> dict:
    last_apply = state.load(cfg, name, "apply") or {}
    last_verify = state.load(cfg, name, "verify") or {}
    apply_fields = ("ok", "started", "seconds", "changed", "error", "message", "job")
    return {
        "last_apply": {key: last_apply.get(key) for key in apply_fields} if last_apply else None,
        "last_verify": {"ok": last_verify.get("ok"), "finished": last_verify.get("finished"),
                        "summary": last_verify.get("summary"), "job": last_verify.get("job")} if last_verify else None,
    }


# ---- Catalog -----------------------------------------------------------------------------------------------------

# GET /api/catalog: what specs can use here: OSes (and whether each has a template), roles, baselines (with
# control counts) and the VLAN/network limits UwU
@router.get("/catalog")
def catalog(request: Request, s: Session = Depends(require("viewer"))) -> dict:
    """What specs can use on this cluster: OSes (with or without a built template), roles, baselines, limits."""
    cfg = _cfg(request)
    os_catalog = load_catalog(cfg)
    try:
        present = templates(PVE(cfg), os_catalog)
    except ValorError:
        present = {}
    roles = role_list(cfg)
    baselines = []
    for path in sorted(cfg.baselines_dir.glob("*.yaml")):
        baseline = load_baseline(cfg.baselines_dir, path.stem)
        baselines.append({"name": path.stem, "controls": len(baseline.controls) if baseline else 0})
    return {"default_os": cfg.default_os,
            "os": [{"name": name, "family": entry.get("family"), "description": entry.get("description", ""),
                    "template": bool(present.get(name, {}).get("present"))} for name, entry in os_catalog.items()],
            "roles": roles, "baselines": baselines,
            "limits": {"vlan_min": cfg.vlan_min, "vlan_max": cfg.vlan_max,
                       "reserved_networks": list(cfg.reserved_networks)}}


# ---- Ranges ------------------------------------------------------------------------------------------------------

# GET /api/ranges: every range: those with a spec file AND those that only exist as VMs >w<
@router.get("/ranges")
def list_ranges(request: Request, s: Session = Depends(require("viewer"))) -> dict:
    """Every range VALOR knows: those with a spec file and those that only exist as VMs on the cluster."""
    cfg = _cfg(request)
    out: dict[str, dict] = {}
    # From spec files: name, description, version and size (or the parse error) OwO
    for path in sorted(cfg.ranges_dir.glob("*.yaml")) if cfg.ranges_dir.exists() else []:
        item = {"name": path.stem, "has_spec": True, "vms": 0, "running": 0}
        try:
            spec = normalize(parse_spec(path.read_text()), cfg.default_os)
            item.update(description=spec.description, version=spec_hash(spec)[:12], segments=len(spec.segments),
                        hosts=len(spec.hosts))
        except ValorError as e:
            item.update(error=e.message)
        out[path.stem] = item
    cluster_error = None
    # From the cluster: VM counts and versions (if the cluster can't be asked, the list still shows the specs) ^w^
    try:
        for name, deployed in ranges_overview(PVE(cfg)).items():
            item = out.setdefault(name, {"name": name, "has_spec": False})
            item.update(vms=deployed["vms"], running=deployed["running"], deployed_version=deployed["spec"],
                        machines=deployed["hosts"])
    except ValorError as e:
        cluster_error = e.message               # the list still shows the specs
    # Add the last build/verify summary to each, sorted by name UwU
    for name, item in out.items():
        item.update(_summary(cfg, name))
    return {"ranges": sorted(out.values(), key=lambda item: item["name"]), "cluster_error": cluster_error}


# GET /api/ranges/<name>: everything the range page needs: YAML, parsed spec, version, VMs, map, tests, history,
# journal, last verification, whether logins exist >w<
@router.get("/ranges/{name}")
def get_range(name: str, request: Request, s: Session = Depends(require("viewer"))) -> dict:
    cfg = _cfg(request)
    spec, text = _load(cfg, name)
    try:
        vms, cluster_error = _vm_states(PVE(cfg), name), None
    except ValorError as e:
        vms, cluster_error = {}, e.message
    journal = cfg.journals_dir / f"{name}.md"
    return {"name": name, "yaml": text, "spec": spec.model_dump(mode="json", by_alias=True),
            "version": spec_hash(spec)[:12], "vms": vms, "cluster_error": cluster_error,
            "topology": topology.build(spec, vms), "tests": effective_tests(spec, cfg.probe),
            "history": state.history(cfg, name)[-50:], "journal": journal.read_text() if journal.is_file() else None,
            "verify": state.load(cfg, name, "verify"), "logins": credentials.enabled(cfg),
            **_summary(cfg, name)}


# POST /api/specs/check: check a spec that isn't saved yet (schema, then cluster), with its map OwO
@router.post("/specs/check")
def check_spec(body: SpecIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    """Schema and cluster checks for a spec that is not saved yet, plus its map."""
    cfg = _cfg(request)
    try:
        spec = normalize(parse_spec(body.yaml), cfg.default_os)
    except ValorError as e:
        return {"ok": False, "errors": [e.to_dict()], "topology": None}
    result = {"ok": True, "errors": [], "warnings": [], "name": spec.name, "version": spec_hash(spec)[:12],
              "topology": topology.build(spec)}
    try:
        checks = validate_cluster(PVE(cfg), spec)
        result.update(ok=checks["ok"], errors=checks["errors"], warnings=checks.get("warnings", []))
    except ValorError as e:
        result["warnings"].append({"location": "cluster", "message": f"cluster checks skipped: {e.message}"})
    return result


# PUT /api/ranges/<name>/spec: save a spec (it must be valid and carry this name); atomic write; audited ^w^
@router.put("/ranges/{name}/spec")
def save_spec(name: str, body: SpecIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    spec = normalize(parse_spec(body.yaml), cfg.default_os)       # schema errors -> 400
    if spec.name != _name(name):
        raise ApiError(400, "name_mismatch", f"The spec's name is '{spec.name}', not '{name}'.")
    path = _spec_path(cfg, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(body.yaml if body.yaml.endswith("\n") else body.yaml + "\n")
    os.replace(tmp, path)
    s.audit("range.spec.save", target=name, detail={"version": spec_hash(spec)[:12]})
    return {"ok": True, "name": name, "version": spec_hash(spec)[:12]}


# DELETE /api/ranges/<name>/spec: delete a spec, only after its VMs are destroyed; also forgets its state and
# journal UwU
@router.delete("/ranges/{name}/spec")
def delete_spec(name: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    path = _spec_path(cfg, name)
    if not path.is_file():
        raise ApiError(404, "no_spec", "No such spec.")
    if range_vms(PVE(cfg), name, with_config=False):
        raise ApiError(409, "range_exists", "Destroy the range's VMs before deleting its spec.")
    path.unlink()
    state.forget(cfg, name)
    for journal in (cfg.journals_dir / f"{name}.md", cfg.journals_dir / f"{name}.notes.md"):
        journal.unlink(missing_ok=True)
    s.audit("range.spec.delete", target=name)
    return {"ok": True}


# _target: the spec to plan/build: the saved one, or the draft >w<
def _target(cfg, name: str, draft: bool):
    """The spec to plan: the saved one, or the range's draft."""
    if not draft:
        return _load(cfg, name)
    _load(cfg, name)                    # the range itself must exist
    draft = drafts.load(cfg, _name(name))
    if draft is None:
        raise ApiError(404, "no_draft", "This range has no draft changes.")
    return normalize(parse_spec(draft[0]), cfg.default_os), draft[0]


# POST /api/ranges/<name>/plan: validate, then plan; returns the plan, its hash, the map with change colors, and
# any custom scripts the plan would run OwO
@router.post("/ranges/{name}/plan")
def plan(name: str, request: Request, body: PlanIn | None = None, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    spec, _ = _target(cfg, name, bool(body and body.draft))
    pve = PVE(cfg)
    checks = validate_cluster(pve, spec)
    if not checks["ok"]:
        return {"ok": False, "errors": checks["errors"], "warnings": checks.get("warnings", []),
                "topology": topology.build(spec, _vm_states(pve, name))}
    plan = make_plan(pve, spec)
    return {"ok": True, "warnings": checks.get("warnings", []), "plan": plan, "plan_hash": _plan_hash(spec, plan),
            "version": spec_hash(spec)[:12], "topology": topology.build(spec, _vm_states(pve, name), plan),
            "scripts": custom_scripts(spec, plan)}


# custom_scripts: the custom-script roles on hosts the plan touches, so the approver sees EXACTLY what will run
# as root/SYSTEM ^w^
def custom_scripts(spec, plan: dict) -> list[dict]:
    """The custom-script roles this plan runs (as root / SYSTEM), so the person approving sees them in full."""
    runs = {action["host"] for action in plan.get("actions", [])
            if action["action"] not in ("keep", "restamp", "remove")}
    return [{"host": host.name, "os": host.os, "script": ref.params.get("script", ""),
             "ports": ref.params.get("ports", "")}
            for host in spec.hosts if host.name in runs for ref in host.roles if ref.name == "custom-script"]


# POST /api/ranges/<name>/apply: build the APPROVED plan UwU
@router.post("/ranges/{name}/apply")
def apply(name: str, body: ApplyIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    """Runs only the plan the user approved: the plan is computed again and must hash the same."""
    cfg = _cfg(request)
    spec, text = _target(cfg, name, body.draft)
    pve = PVE(cfg)
    # Plan again right now; a different hash = refuse (and audit the refusal) >w<
    current = make_plan(pve, spec)
    if _plan_hash(spec, current) != body.plan_hash:
        s.audit("range.apply", "fail", target=name, detail="plan changed since it was approved")
        raise ApiError(409, "plan_changed", "The plan changed since you reviewed it. Review the new plan.")
    # An approved draft becomes the range's spec (atomic replace), and the draft is discarded OwO
    if body.draft:                          # the approved draft becomes the range's spec
        path = _spec_path(cfg, name)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
        drafts.discard(cfg, name)
        s.audit("range.spec.save", target=name, detail={"version": spec_hash(spec)[:12], "from": "draft"})
    # Queue the build (with verification) and audit it with the plan hash and changes ^w^
    job = ops.start(cfg, "apply", {"spec": f"{name}.yaml", "verify": True}, f"web:{s.username}", background=True)
    s.audit("range.apply", target=name, detail={"job": job["job"], "plan": body.plan_hash,
                                                "changes": current["summary"]})
    return job


# POST .../verify: queue a verification UwU
@router.post("/ranges/{name}/verify")
def verify(name: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    _load(cfg, name)
    job = ops.start(cfg, "verify", {"spec": f"{name}.yaml"}, f"web:{s.username}", background=True)
    s.audit("range.verify", target=name, detail={"job": job["job"]})
    return job


# POST .../destroy: queue a teardown (the name must be typed to confirm) >w<
@router.post("/ranges/{name}/destroy")
def destroy(name: str, body: DestroyIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    if body.confirm != _name(name):
        raise ApiError(400, "confirm", "Type the range name to confirm.")
    job = ops.start(cfg, "destroy", {"range": name}, f"web:{s.username}", background=True)
    s.audit("range.destroy", target=name, detail={"job": job["job"]})
    return job


# ---- Logins, power, snapshots -------------------------------------------------------------------------------------

# _job: queue an engine job and audit who asked OwO
def _job(s: Session, cfg, kind: str, target: dict, action: str, detail: dict | None = None) -> dict:
    """Queue an engine job for the worker and audit who asked for it."""
    job = ops.start(cfg, kind, target, f"web:{s.username}", background=True)
    s.audit(action, target=target.get("range", ""), detail={"job": job["job"], **(detail or {})})
    return job


# GET .../credentials: the range login (operators; EVERY view is audited) with where it signs in ^w^
@router.get("/ranges/{name}/credentials")
def get_credentials(name: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    if not credentials.enabled(cfg):
        raise ApiError(501, "logins_disabled", "Range logins need VALOR's secret key; this engine has none.")
    login = credentials.load(cfg, _name(name))
    if not login:
        raise ApiError(404, "no_login", "This range has no login yet: it is created at the first build.")
    s.audit("range.credentials.view", target=name)
    try:
        spec, _ = _load(cfg, name)
        accounts = credentials.accounts(spec, load_catalog(cfg), login["username"])
    except (ApiError, ValorError, OSError):
        accounts = [{"username": login["username"], "hosts": [], "kind": "linux"}]
    return {"username": login["username"], "password": login["password"], "version": login["version"],
            "created": login["created"], "accounts": accounts}


# POST .../credentials/rotate: a new password, set on every VM by a job UwU
@router.post("/ranges/{name}/credentials/rotate")
def rotate_credentials(name: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    _load(cfg, name)
    if not credentials.load(cfg, name):
        raise ApiError(404, "no_login", "Build the range first.")
    return _job(s, cfg, "rotate", {"range": name, "spec": f"{name}.yaml"}, "range.credentials.rotate")


# POST .../power: queue a power action >w<
@router.post("/ranges/{name}/power")
def power(name: str, body: PowerIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    _name(name)
    return _job(s, cfg, "power", {"range": name, "action": body.action, "hosts": body.hosts}, "range.power",
                {"action": body.action, "hosts": body.hosts or "all"})


# GET .../snapshots: the snapshots (none for a range that isn't built) OwO
@router.get("/ranges/{name}/snapshots")
def snapshots(name: str, request: Request, s: Session = Depends(require("viewer"))) -> dict:
    try:
        return {"snapshots": lifecycle.list_snapshots(PVE(_cfg(request)), _name(name))}
    except ValorError as e:
        if e.code == "range_not_built":
            return {"snapshots": []}
        raise


# POST .../snapshots: take one (the valor-clean name is reserved for the automatic snapshot) ^w^
@router.post("/ranges/{name}/snapshots")
def take_snapshot(name: str, body: SnapshotIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    if body.name == lifecycle.CLEAN:
        raise ApiError(400, "reserved", f"'{lifecycle.CLEAN}' is taken automatically after every verified build.")
    return _job(s, _cfg(request), "snapshot", {"range": _name(name), "name": body.name,
                                               "description": body.description}, "range.snapshot", {"name": body.name})


# POST .../snapshots/<snap>/rollback: reset to a snapshot (name typed to confirm) UwU
@router.post("/ranges/{name}/snapshots/{snap}/rollback")
def rollback(name: str, snap: str, body: RollbackIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    if body.confirm != _name(name):
        raise ApiError(400, "confirm", "Type the range name to confirm the reset.")
    return _job(s, _cfg(request), "rollback", {"range": name, "name": snap, "delete_newer": body.delete_newer},
                "range.reset", {"snapshot": snap, "delete_newer": body.delete_newer})


# DELETE .../snapshots/<snap>: delete a snapshot >w<
@router.delete("/ranges/{name}/snapshots/{snap}")
def delete_snapshot(name: str, snap: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    return _job(s, _cfg(request), "snapshot_delete", {"range": _name(name), "name": snap}, "range.snapshot.delete",
                {"name": snap})


# ---- Jobs --------------------------------------------------------------------------------------------------------

# GET /api/jobs: the newest jobs OwO
@router.get("/jobs")
def list_jobs(request: Request, s: Session = Depends(require("viewer")),
              limit: int = Query(default=30, ge=1, le=200)) -> dict:
    return {"jobs": jobs.list_jobs(_cfg(request), limit)}


# GET /api/jobs/<id>: one job with up to `tail` events (the id format is checked first) ^w^ *peeks at the job log*
@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, s: Session = Depends(require("viewer")),
            tail: int = Query(default=200, ge=1, le=2000)) -> dict:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", job_id):
        raise ApiError(400, "invalid_job", "Invalid job id.")
    return jobs.status(_cfg(request), job_id, tail=tail)
