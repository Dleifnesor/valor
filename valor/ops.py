"""Operations shared by the CLI and the MCP server: lock, job record, engine call, state, journal."""

from __future__ import annotations

import time

from . import jobs, journal, state
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
            elif kind == "verify":
                result = do_verify(pve, spec, events)
                result.update(job=job_id, started=started, finished=_stamp())
                state.save(cfg, spec.name, "verify", result)
            elif kind == "destroy":
                result = do_destroy(pve, target["range"], events)
                result.update(ok=True, job=job_id, started=started)
                state.save(cfg, target["range"], "destroy", result)
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


def start(cfg, kind: str, target: dict, origin: str, background: bool, echo=None) -> dict:
    job_id = jobs.create(cfg, kind, target, origin)
    if background:
        jobs.spawn(cfg, job_id)
        return {"job": job_id, "state": "started", "hint": "poll job_status(job) until state is succeeded/failed"}
    return execute(cfg, job_id, echo)
