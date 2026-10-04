"""Operations shared by the CLI and the MCP server: lock, job record, engine call, state, journal."""

from __future__ import annotations

import time

from . import credentials, jobs, journal, lifecycle, state, wireguard
from .apply import apply as do_apply
from .destroy import destroy as do_destroy
from .errors import ValorError
from .pve import PVE
from .spec import load_spec, spec_hash
from .verify import verify as do_verify


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def execute(cfg, job_id: str, echo=None) -> dict:
    """Run a job (apply / verify / destroy) to completion. Never raises; returns a result dict with 'ok'."""
    job = jobs.status(cfg, job_id)
    kind, target = job["kind"], job["target"]
    events = jobs.Events(cfg, job_id, echo)
    jobs.mark_running(cfg, job_id)
    spec = path = None
    started = _stamp()
    result: dict
    try:
        with jobs.engine_lock(cfg, f"{kind} job {job_id}"):
            pve = PVE(cfg)
            if kind in ("apply", "verify"):
                spec, path = load_spec(target["spec"], cfg.ranges_dir, cfg.default_os)
                events("spec", range=spec.name, version=spec_hash(spec)[:12])
            if kind == "apply":
                result = do_apply(pve, spec, events)
                result.update(ok=True, job=job_id, started=started)
                state.save(cfg, spec.name, "apply", result)
                if target.get("verify"):
                    v = do_verify(pve, spec, events)
                    v.update(job=job_id, started=started, finished=_stamp())
                    state.save(cfg, spec.name, "verify", v)
                    result["verify"] = {"ok": v["ok"], "summary": v["summary"]}
                    result["ok"] = v["ok"]
                    if v["ok"] and (result.get("changed") or not _has_clean(pve, spec.name)):
                        result["snapshot"] = _clean_snapshot(pve, spec.name, events)
            elif kind == "verify":
                result = do_verify(pve, spec, events)
                result.update(job=job_id, started=started, finished=_stamp())
                state.save(cfg, spec.name, "verify", result)
            elif kind == "destroy":
                result = do_destroy(pve, target["range"], events)
                result.update(ok=True, job=job_id, started=started)
                state.save(cfg, target["range"], "destroy", result)
                credentials.forget(cfg, target["range"])      # a rebuilt range gets a new password
                wireguard.forget(cfg, target["range"])        # ... and new WireGuard keys
            elif kind == "power":
                result = lifecycle.power(pve, target["range"], target["action"], target.get("hosts"), events)
                result.update(ok=True, job=job_id, started=started)
            elif kind == "snapshot":
                result = lifecycle.snapshot(pve, target["range"], target["name"], target.get("description", ""),
                                            emit=events)
                result.update(ok=True, job=job_id, started=started)
            elif kind == "rollback":
                result = lifecycle.rollback(pve, target["range"], target["name"], target.get("delete_newer", False),
                                            emit=events)
                result.update(ok=True, job=job_id, started=started)
                spec, path = load_spec(f"{target['range']}.yaml", cfg.ranges_dir, cfg.default_os)
                for vm in lifecycle._vms(pve, spec.name):
                    pve.wait_agent(vm.vmid, 300)
                v = do_verify(pve, spec, events)
                v.update(job=job_id, started=started, finished=_stamp())
                state.save(cfg, spec.name, "verify", v)
                result["verify"] = {"ok": v["ok"], "summary": v["summary"]}
                result["ok"] = v["ok"]
            elif kind == "snapshot_delete":
                result = lifecycle.delete_snapshot(pve, target["range"], target["name"], emit=events)
                result.update(ok=True, job=job_id, started=started)
            elif kind == "rotate":
                spec, path = load_spec(f"{target['range']}.yaml", cfg.ranges_dir, cfg.default_os)
                credentials.rotate(cfg, spec.name)
                result = do_apply(pve, spec, events)         # the new password version re-converges every VM
                result.update(ok=True, job=job_id, started=started, rotated=True)
                state.save(cfg, spec.name, "apply", result)
            elif kind == "wg_rotate":
                spec, path = load_spec(f"{target['range']}.yaml", cfg.ranges_dir, cfg.default_os)
                if not (spec.access and spec.access.wireguard):
                    raise ValorError("wireguard_not_configured", f"range '{spec.name}' has no WireGuard access")
                wireguard.ensure(cfg, spec, rotate=tuple(target["peers"]))
                result = do_apply(pve, spec, events)         # new keys: the router converges
                result.update(ok=True, job=job_id, started=started, rotated=target["peers"])
                state.save(cfg, spec.name, "apply", result)
            else:
                raise ValorError("job_invalid", f"unknown job kind {kind}")
            if spec is not None:
                try:
                    result["journal"] = str(journal.write(cfg, pve, spec, path))
                except Exception as e:  # the journal must never fail a build
                    result["journal_error"] = str(e)
    except ValorError as e:
        result = {**e.to_dict(), "ok": False, "job": job_id, "started": started}
        if spec is not None:
            state.save(cfg, spec.name, kind, result)
            try:
                journal.write(cfg, PVE(cfg), spec, path)
            except Exception:
                pass
    except Exception as e:
        result = {"ok": False, "error": "internal_error", "message": f"{e.__class__.__name__}: {e}", "job": job_id}
    jobs.finish(cfg, job_id, result)
    jobs.update(cfg, job_id, state="succeeded" if result.get("ok") else "failed", finished=_stamp())
    return result


def _has_clean(pve: PVE, name: str) -> bool:
    try:
        return any(s["name"] == lifecycle.CLEAN for s in lifecycle.list_snapshots(pve, name))
    except ValorError:
        return False


def _clean_snapshot(pve: PVE, name: str, events) -> dict:
    """The automatic 'clean' snapshot after a verified build. A failure here never fails the build."""
    try:
        lifecycle.snapshot(pve, name, lifecycle.CLEAN, f"verified build {_stamp()}", replace=True, emit=events)
        return {"ok": True, "name": lifecycle.CLEAN}
    except ValorError as e:
        events("snapshot_failed", error=e.code, message=e.message)
        return {"ok": False, "name": lifecycle.CLEAN, "error": e.code, "message": e.message}


def start(cfg, kind: str, target: dict, origin: str, background: bool, echo=None) -> dict:
    job_id = jobs.create(cfg, kind, target, origin)
    if background:
        if cfg.job_runner == "worker":    # the valor-worker service picks queued jobs up in order
            return {"job": job_id, "state": "queued", "hint": "poll job_status(job) until state is succeeded/failed"}
        jobs.spawn(cfg, job_id)
        return {"job": job_id, "state": "started", "hint": "poll job_status(job) until state is succeeded/failed"}
    return execute(cfg, job_id, echo)
