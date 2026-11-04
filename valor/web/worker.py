# *hard hat on, tail wagging* OwO valor/web/worker.py is valor-worker, the service that actually RUNS jobs! ^w^
# The web API only QUEUES jobs (build, verify, destroy, power, snapshots...). This separate service picks them up
# one at a time, runs them through the engine, writes an audit entry and sends a notification. Because it's a
# separate service, restarting the web UI never interrupts a build in progress *wags tail*
"""valor-worker: runs queued engine jobs one at a time and reports their outcome.

A separate service from the web API, so restarting the web service never interrupts a build.
"""

# Lazy type hints UwU
from __future__ import annotations

# json reads job files; logging writes to the journal; signal catches stop requests; time sleeps between polls;
# Path walks the jobs folder >w<
import json
import logging
import signal
import time
from pathlib import Path

# jobs = the job queue on disk; ops = runs a job by its kind; load_config = engine settings; db/notify = audit
# and notifications; load_web_config = web settings; Box = the encryption box OwO
from .. import jobs, ops
from ..config import load_config
from . import db, notify
from .config import load_web_config
from .security import Box

# The logger, and the "please stop" flag set by the signal handler ^w^
log = logging.getLogger("valor.worker")
_stop = False


# _on_term: called on SIGTERM (systemctl stop) or SIGINT (Ctrl+C). It only sets the flag; the main loop stops
# AFTER the current job, so a build is never cut off halfway UwU *patiently waits*
def _on_term(signum, frame):
    global _stop
    _stop = True
    log.info("stop requested; finishing the current job first")


# recover_interrupted: at startup, find jobs still marked "running" whose process is gone (the VM rebooted, the
# worker crashed) and mark them failed with a clear message. Returns their ids >w<
def recover_interrupted(cfg) -> list[str]:
    """Jobs left 'running' by a previous worker that died (reboot, crash) are marked failed."""
    out = []
    root = Path(cfg.jobs_dir)
    # Each job is a folder with a job.json; skip unreadable ones OwO
    for folder in sorted(root.iterdir()) if root.exists() else []:
        try:
            job = json.loads((folder / "job.json").read_text())
        except Exception:
            continue
        # "running", but /proc/<pid> doesn't exist = that process is dead ^w^
        if job.get("state") == "running" and not Path(f"/proc/{job.get('pid', 0)}").exists():
            # Save a failed result telling the person to run it again, then set the state to failed UwU
            jobs.finish(cfg, folder.name, {"ok": False, "error": "interrupted",
                                           "message": "the worker stopped while this job ran (reboot or crash); "
                                                      "run it again"})
            jobs.update(cfg, folder.name, state="failed")
            out.append(folder.name)
    return out


# DONE: the notification title for each simple job kind when it succeeds. {name}, {action}, {snap} and {peers}
# are filled in by _describe() >w<
DONE = {"power": "Range {name}: {action} done",
        "snapshot": "Range {name}: snapshot {snap} taken",
        "rollback": "Range {name} reset to {snap}",
        "snapshot_delete": "Range {name}: snapshot {snap} deleted",
        "rotate": "Range {name}: new login password set",
        "wg_rotate": "Range {name}: new WireGuard keys for {peers}"}


# _describe: decide a finished job's notification: (level, title, body) OwO
def _describe(job: dict, result: dict) -> tuple[str, str, str]:
    """(level, title, body) of the notification sent when a job ends."""
    # The range's name: from the target's "range", or from its spec file name without ".yaml" ^w^
    kind, target = job["kind"], job["target"]
    name = target.get("range") or str(target.get("spec", "")).removesuffix(".yaml")
    if result.get("ok"):
        # Successful builds report how long they took and, if verification ran, the test and baseline scores UwU
        if kind == "apply":
            summary = (result.get("verify") or {}).get("summary") or {}
            extra = (f" Verified: tests {summary.get('tests_passed')}/{summary.get('tests_total')}, baseline "
                     f"{summary.get('baseline_passed')}/{summary.get('baseline_total')}." if summary else "")
            return "success", f"Range {name} built", f"Finished in {result.get('seconds')} s.{extra}"
        # Successful verifications report the scores >w<
        if kind == "verify":
            summary = result.get("summary", {})
            return "success", f"Range {name} verified", (
                f"Tests {summary.get('tests_passed')}/{summary.get('tests_total')}, "
                f"baseline {summary.get('baseline_passed')}/{summary.get('baseline_total')}.")
        # Successful teardowns report how many VMs were deleted OwO
        if kind == "destroy":
            return "success", f"Range {name} destroyed", f"{len(result.get('removed', []))} VMs deleted."
    # Other successful kinds use the DONE titles (a rollback also mentions the tests after the reset) ^w^
    if result.get("ok") is not False and kind in DONE:
        done = DONE[kind].format(name=name, action=target.get("action"), snap=target.get("name"),
                                 peers=", ".join(target.get("peers") or []))
        extra = ""
        if kind == "rollback" and result.get("verify"):
            summary = result["verify"]["summary"]
            extra = f"Verified after the reset: tests {summary.get('tests_passed')}/{summary.get('tests_total')}."
        return "success", done, extra
    # Anything else failed: "<What> of range <name> failed", with the error code, message and hint as the body UwU
    what = {"apply": "Build", "verify": "Verification", "destroy": "Teardown", "power": "Power action",
            "snapshot": "Snapshot", "rollback": "Reset", "snapshot_delete": "Snapshot deletion",
            "rotate": "Password rotation", "wg_rotate": "WireGuard key rotation"}.get(kind, kind)
    hint = f"\nHint: {result['hint']}" if result.get("hint") else ""
    return "error", f"{what} of range {name} failed", f"{result.get('error', 'error')}: {result.get('message', '')}{hint}"


# run_forever: the worker's main loop *settles in for a long shift*
#   poll = seconds to wait between checks when the queue is empty >w<
def run_forever(poll: float = 2.0) -> None:
    """The worker's main loop: take the oldest queued job, run it, audit and notify; stop between jobs on SIGTERM."""
    # Log format: "INFO valor.worker: message" (systemd adds the time) OwO
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Install the polite stop handlers ^w^
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    # Load both configs, make sure the database schema is current, and open the encryption box (if there's a key) UwU
    cfg, wcfg = load_config(), load_web_config()
    db.migrate(wcfg.db)
    box = Box.from_file(wcfg.secret_key_file) if Path(wcfg.secret_key_file).exists() else None
    # Clean up after a previous crash first >w<
    for job_id in recover_interrupted(cfg):
        log.warning("job %s was interrupted by a previous shutdown", job_id)
    log.info("worker ready (jobs in %s)", cfg.jobs_dir)
    # Until asked to stop: take the oldest queued job, or sleep a bit when there is none OwO
    while not _stop:
        job_id = jobs.next_queued(cfg)
        if not job_id:
            time.sleep(poll)
            continue
        # Run the job (this can take many minutes for a build) ^w^
        job = jobs.status(cfg, job_id)
        log.info("running job %s (%s)", job_id, job["kind"])
        result = ops.execute(cfg, job_id)
        # Work out the notification, then audit + notify with a fresh database connection UwU
        level, title, body = _describe(job, result)
        conn = db.connect(wcfg.db)
        try:
            # The audit entry: "job.<kind>", ok/fail, who started it (the "web:" prefix removed from the origin), the job id
            # and the range and error >w<
            db.audit(conn, f"job.{job['kind']}", "ok" if result.get("ok") else "fail",
                     username=str(job.get("origin", "")).removeprefix("web:"), target=job_id,
                     detail={"range": job["target"], "error": result.get("error")})
            # A link straight to the job page, when the public URL is known OwO
            link = f"{wcfg.public_url.rstrip('/')}/#/jobs/{job_id}" if wcfg.public_url else ""
            # wait=True: the worker has time, so it sends right away and the errors get audited ^w^
            notify.emit(conn, box, level, f"job.{job['kind']}", title, body, link, wait=True)
        finally:
            conn.close()
        # One log line per finished job UwU *stretches and grabs the next job*
        log.info("job %s finished: %s", job_id, "ok" if result.get("ok") else result.get("error"))
