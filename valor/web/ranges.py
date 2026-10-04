"""Ranges, specs, plans, approved builds and jobs. Every change to the cluster goes through an engine job."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

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

router = APIRouter(prefix="/api", tags=["ranges"])
NAME_RE = re.compile(NAME)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpecIn(_In):
    yaml: str = Field(min_length=1, max_length=MAX_SPEC_BYTES)


class ApplyIn(_In):
    plan_hash: str = Field(min_length=16, max_length=64)
    draft: bool = False             # build the range's draft (map edits / chat) instead of its saved spec


class PlanIn(_In):
    draft: bool = False


class DestroyIn(_In):
    confirm: str = Field(max_length=64)


class PowerIn(_In):
    action: str = Field(pattern="^(start|shutdown|stop|reboot)$")
    hosts: list[str] | None = Field(default=None, max_length=50)


class SnapshotIn(_In):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,39}$")
    description: str = Field(default="", max_length=200)


class RollbackIn(_In):
    confirm: str = Field(max_length=64)
    delete_newer: bool = False


def role_list(cfg) -> list[dict]:
    """Every role (Linux role.sh and/or Windows role.ps1) with the OS families it supports."""
    roles = []
    for d in sorted(cfg.roles_dir.iterdir()):
        if (d / "role.sh").is_file() or (d / "role.ps1").is_file():
            role = load_role(cfg.roles_dir, d.name)
            meta = role.meta
            roles.append({"name": d.name, "description": meta.get("description", ""), "ports": meta.get("ports", []),
                          "params": meta.get("params", {}), "families": role.families})
    return roles


def _cfg(request: Request):
    return request.app.state.cfg


def _name(name: str) -> str:
    if not NAME_RE.fullmatch(name):
        raise ApiError(400, "invalid_name", "Range names are 2-15 lowercase letters, digits or dashes.")
    return name


def _spec_path(cfg, name: str) -> Path:
    return cfg.ranges_dir / f"{_name(name)}.yaml"


def _load(cfg, name: str):
    p = _spec_path(cfg, name)
    if not p.is_file():
        raise ApiError(404, "no_spec", f"There is no spec for range '{name}'.")
    text = p.read_text()
    return normalize(parse_spec(text), cfg.default_os), text


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


def _plan_hash(spec, plan: dict) -> str:
    key = {"spec": spec_hash(spec), "actions": [(a["host"], a["action"], a.get("vmid")) for a in plan["actions"]]}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:32]


def _summary(cfg, name: str) -> dict:
    ap = state.load(cfg, name, "apply") or {}
    vr = state.load(cfg, name, "verify") or {}
    return {
        "last_apply": {k: ap.get(k) for k in ("ok", "started", "seconds", "changed", "error", "message", "job")} if ap else None,
        "last_verify": {"ok": vr.get("ok"), "finished": vr.get("finished"), "summary": vr.get("summary"),
                        "job": vr.get("job")} if vr else None,
    }


# ---------------------------------------------------------------------- catalog
@router.get("/catalog")
def catalog(request: Request, s: Session = Depends(require("viewer"))) -> dict:
    cfg = _cfg(request)
    cat = load_catalog(cfg)
    try:
        present = templates(PVE(cfg), cat)
    except ValorError:
        present = {}
    roles = role_list(cfg)
    baselines = []
    for p in sorted(cfg.baselines_dir.glob("*.yaml")):
        b = load_baseline(cfg.baselines_dir, p.stem)
        baselines.append({"name": p.stem, "controls": len(b.controls) if b else 0})
    return {"default_os": cfg.default_os,
            "os": [{"name": k, "family": v.get("family"), "description": v.get("description", ""),
                    "template": bool(present.get(k, {}).get("present"))} for k, v in cat.items()],
            "roles": roles, "baselines": baselines,
            "limits": {"vlan_min": cfg.vlan_min, "vlan_max": cfg.vlan_max,
                       "reserved_networks": list(cfg.reserved_networks)}}


# ---------------------------------------------------------------------- ranges
@router.get("/ranges")
def list_ranges(request: Request, s: Session = Depends(require("viewer"))) -> dict:
    cfg = _cfg(request)
    out: dict[str, dict] = {}
    for p in sorted(cfg.ranges_dir.glob("*.yaml")) if cfg.ranges_dir.exists() else []:
        item = {"name": p.stem, "has_spec": True, "vms": 0, "running": 0}
        try:
            spec = normalize(parse_spec(p.read_text()), cfg.default_os)
            item.update(description=spec.description, version=spec_hash(spec)[:12], segments=len(spec.segments),
                        hosts=len(spec.hosts))
        except ValorError as e:
            item.update(error=e.message)
        out[p.stem] = item
    cluster_error = None
    try:
        for name, r in ranges_overview(PVE(cfg)).items():
            item = out.setdefault(name, {"name": name, "has_spec": False})
            item.update(vms=r["vms"], running=r["running"], deployed_version=r["spec"])
    except ValorError as e:
        cluster_error = e.message
    for name, item in out.items():
        item.update(_summary(cfg, name))
    return {"ranges": sorted(out.values(), key=lambda r: r["name"]), "cluster_error": cluster_error}


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


@router.post("/specs/check")
def check_spec(body: SpecIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    """Schema and cluster checks for a spec that is not saved yet, plus its map."""
    cfg = _cfg(request)
    try:
        spec = normalize(parse_spec(body.yaml), cfg.default_os)
    except ValorError as e:
        return {"ok": False, "errors": [e.to_dict()], "topology": None}
    res = {"ok": True, "errors": [], "warnings": [], "name": spec.name, "version": spec_hash(spec)[:12],
           "topology": topology.build(spec)}
    try:
        v = validate_cluster(PVE(cfg), spec)
        res.update(ok=v["ok"], errors=v["errors"], warnings=v.get("warnings", []))
    except ValorError as e:
        res["warnings"].append({"location": "cluster", "message": f"cluster checks skipped: {e.message}"})
    return res


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


@router.delete("/ranges/{name}/spec")
def delete_spec(name: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    path = _spec_path(cfg, name)
    if not path.is_file():
        raise ApiError(404, "no_spec", "No such spec.")
    if range_vms(PVE(cfg), name, with_config=False):
        raise ApiError(409, "range_exists", "Destroy the range's VMs before deleting its spec.")
    path.unlink()
    s.audit("range.spec.delete", target=name)
    return {"ok": True}


def _target(cfg, name: str, draft: bool):
    """The spec to plan: the saved one, or the range's draft."""
    if not draft:
        return _load(cfg, name)
    _load(cfg, name)
    d = drafts.load(cfg, _name(name))
    if d is None:
        raise ApiError(404, "no_draft", "This range has no draft changes.")
    return normalize(parse_spec(d[0]), cfg.default_os), d[0]


@router.post("/ranges/{name}/plan")
def plan(name: str, request: Request, body: PlanIn | None = None, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    spec, _ = _target(cfg, name, bool(body and body.draft))
    pve = PVE(cfg)
    v = validate_cluster(pve, spec)
    if not v["ok"]:
        return {"ok": False, "errors": v["errors"], "warnings": v.get("warnings", []),
                "topology": topology.build(spec, _vm_states(pve, name))}
    p = make_plan(pve, spec)
    return {"ok": True, "warnings": v.get("warnings", []), "plan": p, "plan_hash": _plan_hash(spec, p),
            "version": spec_hash(spec)[:12], "topology": topology.build(spec, _vm_states(pve, name), p)}


@router.post("/ranges/{name}/apply")
def apply(name: str, body: ApplyIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    """Runs only the plan the user approved: the plan is computed again and must hash the same."""
    cfg = _cfg(request)
    spec, text = _target(cfg, name, body.draft)
    pve = PVE(cfg)
    current = make_plan(pve, spec)
    if _plan_hash(spec, current) != body.plan_hash:
        s.audit("range.apply", "fail", target=name, detail="plan changed since it was approved")
        raise ApiError(409, "plan_changed", "The plan changed since you reviewed it. Review the new plan.")
    if body.draft:                          # the approved draft becomes the range's spec
        path = _spec_path(cfg, name)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
        drafts.discard(cfg, name)
        s.audit("range.spec.save", target=name, detail={"version": spec_hash(spec)[:12], "from": "draft"})
    job = ops.start(cfg, "apply", {"spec": f"{name}.yaml", "verify": True}, f"web:{s.username}", background=True)
    s.audit("range.apply", target=name, detail={"job": job["job"], "plan": body.plan_hash,
                                                "changes": current["summary"]})
    return job


@router.post("/ranges/{name}/verify")
def verify(name: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    _load(cfg, name)
    job = ops.start(cfg, "verify", {"spec": f"{name}.yaml"}, f"web:{s.username}", background=True)
    s.audit("range.verify", target=name, detail={"job": job["job"]})
    return job


@router.post("/ranges/{name}/destroy")
def destroy(name: str, body: DestroyIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    if body.confirm != _name(name):
        raise ApiError(400, "confirm", "Type the range name to confirm.")
    job = ops.start(cfg, "destroy", {"range": name}, f"web:{s.username}", background=True)
    s.audit("range.destroy", target=name, detail={"job": job["job"]})
    return job


# ---------------------------------------------------------------------- logins, power, snapshots
def _job(s: Session, cfg, kind: str, target: dict, action: str, detail: dict | None = None) -> dict:
    job = ops.start(cfg, kind, target, f"web:{s.username}", background=True)
    s.audit(action, target=target.get("range", ""), detail={"job": job["job"], **(detail or {})})
    return job


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
        accts = credentials.accounts(spec, load_catalog(cfg), login["username"])
    except (ApiError, ValorError, OSError):
        accts = [{"username": login["username"], "hosts": [], "kind": "linux"}]
    return {"username": login["username"], "password": login["password"], "version": login["version"],
            "created": login["created"], "accounts": accts}


@router.post("/ranges/{name}/credentials/rotate")
def rotate_credentials(name: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    _load(cfg, name)
    if not credentials.load(cfg, name):
        raise ApiError(404, "no_login", "Build the range first.")
    return _job(s, cfg, "rotate", {"range": name, "spec": f"{name}.yaml"}, "range.credentials.rotate")


@router.post("/ranges/{name}/power")
def power(name: str, body: PowerIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    _name(name)
    return _job(s, cfg, "power", {"range": name, "action": body.action, "hosts": body.hosts}, "range.power",
                {"action": body.action, "hosts": body.hosts or "all"})


@router.get("/ranges/{name}/snapshots")
def snapshots(name: str, request: Request, s: Session = Depends(require("viewer"))) -> dict:
    try:
        return {"snapshots": lifecycle.list_snapshots(PVE(_cfg(request)), _name(name))}
    except ValorError as e:
        if e.code == "range_not_built":
            return {"snapshots": []}
        raise


@router.post("/ranges/{name}/snapshots")
def take_snapshot(name: str, body: SnapshotIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    if body.name == lifecycle.CLEAN:
        raise ApiError(400, "reserved", f"'{lifecycle.CLEAN}' is taken automatically after every verified build.")
    return _job(s, _cfg(request), "snapshot", {"range": _name(name), "name": body.name,
                                               "description": body.description}, "range.snapshot", {"name": body.name})


@router.post("/ranges/{name}/snapshots/{snap}/rollback")
def rollback(name: str, snap: str, body: RollbackIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    if body.confirm != _name(name):
        raise ApiError(400, "confirm", "Type the range name to confirm the reset.")
    return _job(s, _cfg(request), "rollback", {"range": name, "name": snap, "delete_newer": body.delete_newer},
                "range.reset", {"snapshot": snap, "delete_newer": body.delete_newer})


@router.delete("/ranges/{name}/snapshots/{snap}")
def delete_snapshot(name: str, snap: str, request: Request, s: Session = Depends(require("operator"))) -> dict:
    return _job(s, _cfg(request), "snapshot_delete", {"range": _name(name), "name": snap}, "range.snapshot.delete",
                {"name": snap})


# ---------------------------------------------------------------------- jobs
@router.get("/jobs")
def list_jobs(request: Request, s: Session = Depends(require("viewer")),
              limit: int = Query(default=30, ge=1, le=200)) -> dict:
    return {"jobs": jobs.list_jobs(_cfg(request), limit)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, s: Session = Depends(require("viewer")),
            tail: int = Query(default=200, ge=1, le=2000)) -> dict:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", job_id):
        raise ApiError(400, "invalid_job", "Invalid job id.")
    return jobs.status(_cfg(request), job_id, tail=tail)
