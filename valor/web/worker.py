"""valor-worker: runs queued engine jobs one at a time and reports their outcome.

A separate service from the web API, so restarting the web service never interrupts a build.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from pathlib import Path

from .. import jobs, ops
from ..config import load_config
from . import db, notify
from .config import load_web_config
from .security import Box

log = logging.getLogger("valor.worker")
_stop = False


def _on_term(signum, frame):
    global _stop
    _stop = True
    log.info("stop requested; finishing the current job first")


def recover_interrupted(cfg) -> list[str]:
    """Jobs left 'running' by a previous worker that died (reboot, crash) are marked failed."""
    out = []
    root = Path(cfg.jobs_dir)
    for d in sorted(root.iterdir()) if root.exists() else []:
        try:
            j = json.loads((d / "job.json").read_text())
        except Exception:
            continue
        if j.get("state") == "running" and not Path(f"/proc/{j.get('pid', 0)}").exists():
            jobs.finish(cfg, d.name, {"ok": False, "error": "interrupted",
                                      "message": "the worker stopped while this job ran (reboot or crash); run it again"})
            jobs.update(cfg, d.name, state="failed")
            out.append(d.name)
    return out


def _describe(job: dict, result: dict) -> tuple[str, str, str]:
    kind, target = job["kind"], job["target"]
    name = target.get("range") or str(target.get("spec", "")).removesuffix(".yaml")
    if result.get("ok"):
        if kind == "apply":
            v = (result.get("verify") or {}).get("summary") or {}
            extra = f" Verified: tests {v.get('tests_passed')}/{v.get('tests_total')}, baseline " \
                    f"{v.get('baseline_passed')}/{v.get('baseline_total')}." if v else ""
            return "success", f"Range {name} built", f"Finished in {result.get('seconds')} s.{extra}"
        if kind == "verify":
            sm = result.get("summary", {})
            return "success", f"Range {name} verified", \
                f"Tests {sm.get('tests_passed')}/{sm.get('tests_total')}, baseline {sm.get('baseline_passed')}/{sm.get('baseline_total')}."
        return "success", f"Range {name} destroyed", f"{len(result.get('removed', []))} VMs deleted."
    what = {"apply": "Build", "verify": "Verification", "destroy": "Teardown"}.get(kind, kind)
    return "error", f"{what} of range {name} failed", \
        f"{result.get('error', 'error')}: {result.get('message', '')}" + (f"\nHint: {result['hint']}" if result.get("hint") else "")


def run_forever(poll: float = 2.0) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    cfg, wcfg = load_config(), load_web_config()
    db.migrate(wcfg.db)
    box = Box.from_file(wcfg.secret_key_file) if Path(wcfg.secret_key_file).exists() else None
    for job_id in recover_interrupted(cfg):
        log.warning("job %s was interrupted by a previous shutdown", job_id)
    log.info("worker ready (jobs in %s)", cfg.jobs_dir)
    while not _stop:
        job_id = jobs.next_queued(cfg)
        if not job_id:
            time.sleep(poll)
            continue
        job = jobs.status(cfg, job_id)
        log.info("running job %s (%s)", job_id, job["kind"])
        result = ops.execute(cfg, job_id)
        level, title, body = _describe(job, result)
        conn = db.connect(wcfg.db)
        try:
            db.audit(conn, f"job.{job['kind']}", "ok" if result.get("ok") else "fail",
                     username=str(job.get("origin", "")).removeprefix("web:"), target=job_id,
                     detail={"range": job["target"], "error": result.get("error")})
            link = f"{wcfg.public_url.rstrip('/')}/#/jobs/{job_id}" if wcfg.public_url else ""
            notify.emit(conn, box, level, f"job.{job['kind']}", title, body, link, wait=True)
        finally:
            conn.close()
        log.info("job %s finished: %s", job_id, "ok" if result.get("ok") else result.get("error"))
