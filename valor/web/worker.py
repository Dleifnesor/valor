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
    for folder in sorted(root.iterdir()) if root.exists() else []:
        try:
            job = json.loads((folder / "job.json").read_text())
        except Exception:
            continue
        if job.get("state") == "running" and not Path(f"/proc/{job.get('pid', 0)}").exists():
            jobs.finish(cfg, folder.name, {"ok": False, "error": "interrupted",
                                           "message": "the worker stopped while this job ran (reboot or crash); "
                                                      "run it again"})
            jobs.update(cfg, folder.name, state="failed")
            out.append(folder.name)
    return out


DONE = {"power": "Range {name}: {action} done",
        "snapshot": "Range {name}: snapshot {snap} taken",
        "rollback": "Range {name} reset to {snap}",
        "snapshot_delete": "Range {name}: snapshot {snap} deleted",
        "rotate": "Range {name}: new login password set",
        "wg_rotate": "Range {name}: new WireGuard keys for {peers}"}


def _describe(job: dict, result: dict) -> tuple[str, str, str]:
    """(level, title, body) of the notification sent when a job ends."""
    kind, target = job["kind"], job["target"]
    name = target.get("range") or str(target.get("spec", "")).removesuffix(".yaml")
    if result.get("ok"):
        if kind == "apply":
            summary = (result.get("verify") or {}).get("summary") or {}
            extra = (f" Verified: tests {summary.get('tests_passed')}/{summary.get('tests_total')}, baseline "
                     f"{summary.get('baseline_passed')}/{summary.get('baseline_total')}." if summary else "")
            return "success", f"Range {name} built", f"Finished in {result.get('seconds')} s.{extra}"
        if kind == "verify":
            summary = result.get("summary", {})
            return "success", f"Range {name} verified", (
                f"Tests {summary.get('tests_passed')}/{summary.get('tests_total')}, "
                f"baseline {summary.get('baseline_passed')}/{summary.get('baseline_total')}.")
        if kind == "destroy":
            return "success", f"Range {name} destroyed", f"{len(result.get('removed', []))} VMs deleted."
    if result.get("ok") is not False and kind in DONE:
        done = DONE[kind].format(name=name, action=target.get("action"), snap=target.get("name"),
                                 peers=", ".join(target.get("peers") or []))
        extra = ""
        if kind == "rollback" and result.get("verify"):
            summary = result["verify"]["summary"]
            extra = f"Verified after the reset: tests {summary.get('tests_passed')}/{summary.get('tests_total')}."
        return "success", done, extra
    what = {"apply": "Build", "verify": "Verification", "destroy": "Teardown", "power": "Power action",
            "snapshot": "Snapshot", "rollback": "Reset", "snapshot_delete": "Snapshot deletion",
            "rotate": "Password rotation", "wg_rotate": "WireGuard key rotation"}.get(kind, kind)
    hint = f"\nHint: {result['hint']}" if result.get("hint") else ""
    return "error", f"{what} of range {name} failed", f"{result.get('error', 'error')}: {result.get('message', '')}{hint}"


def run_forever(poll: float = 2.0) -> None:
    """The worker's main loop: take the oldest queued job, run it, audit and notify; stop between jobs on SIGTERM."""
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
