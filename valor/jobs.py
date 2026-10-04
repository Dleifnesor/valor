"""Background jobs with persistent logs; one infrastructure job at a time (NFR Reliability/Responsiveness)."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import LockBusy, ValorError

KINDS = ("apply", "verify", "destroy")


def _dir(cfg, job_id: str) -> Path:
    if not all(c.isalnum() or c == "-" for c in job_id):
        raise ValorError("job_invalid", "invalid job id")
    return Path(cfg.jobs_dir) / job_id


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def create(cfg, kind: str, target: dict, origin: str) -> str:
    job_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    d = _dir(cfg, job_id)
    d.mkdir(parents=True)
    _write(d / "job.json", {"id": job_id, "kind": kind, "target": target, "origin": origin, "state": "queued",
                            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    return job_id


def spawn(cfg, job_id: str) -> None:
    d = _dir(cfg, job_id)
    log = (d / "runner.log").open("a")
    subprocess.Popen([sys.executable, "-m", "valor.cli", "_run-job", job_id], stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True, cwd="/")


@contextmanager
def engine_lock(cfg, holder: str):
    path = Path(cfg.state_dir) / "engine.lock"
    fh = path.open("a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        raise LockBusy(fh.read().strip() or "unknown")
    fh.seek(0)
    fh.truncate()
    fh.write(holder)
    fh.flush()
    try:
        yield
    finally:
        fh.seek(0)
        fh.truncate()
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


class Events:
    def __init__(self, cfg, job_id: str, echo=None):
        self.path = _dir(cfg, job_id) / "events.jsonl"
        self.echo = echo

    def __call__(self, event: str, **fields) -> None:
        rec = {"t": time.strftime("%H:%M:%S"), "event": event, **fields}
        with self.path.open("a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        if self.echo:
            self.echo(rec)


def update(cfg, job_id: str, **fields) -> dict:
    p = _dir(cfg, job_id) / "job.json"
    data = json.loads(p.read_text())
    data.update(fields)
    _write(p, data)
    return data


def finish(cfg, job_id: str, result: dict) -> None:
    _write(_dir(cfg, job_id) / "result.json", result)


def status(cfg, job_id: str, tail: int = 25) -> dict:
    d = _dir(cfg, job_id)
    if not d.exists():
        raise ValorError("job_unknown", f"no job {job_id}")
    job = json.loads((d / "job.json").read_text())
    events = []
    if (d / "events.jsonl").exists():
        events = [json.loads(l) for l in (d / "events.jsonl").read_text().splitlines() if l.strip()]
    job["progress"] = {
        "steps_done": sum(e["event"] == "step_done" for e in events),
        "current": next((f"{e.get('step')}{' [' + e['host'] + ']' if e.get('host') else ''}"
                         for e in reversed(events) if e["event"] == "step_start"), None),
    }
    job["recent_events"] = events[-tail:]
    if (d / "result.json").exists():
        job["result"] = json.loads((d / "result.json").read_text())
    if job.get("state") == "running" and job.get("pid") and not Path(f"/proc/{job['pid']}").exists():
        job["state"] = "failed"
        job["result"] = {"error": "runner_died", "message": "the job process ended unexpectedly; see runner.log"}
    return job


def list_jobs(cfg, limit: int = 20) -> list[dict]:
    root = Path(cfg.jobs_dir)
    out = []
    for d in sorted(root.iterdir(), reverse=True)[:limit] if root.exists() else []:
        try:
            j = json.loads((d / "job.json").read_text())
            out.append({k: j.get(k) for k in ("id", "kind", "state", "target", "created", "finished")})
        except Exception:
            continue
    return out


def next_queued(cfg) -> str | None:
    """Oldest job still waiting to run (used by the worker service)."""
    root = Path(cfg.jobs_dir)
    for d in sorted(root.iterdir()) if root.exists() else []:
        try:
            if json.loads((d / "job.json").read_text()).get("state") == "queued":
                return d.name
        except Exception:
            continue
    return None


def mark_running(cfg, job_id: str) -> None:
    update(cfg, job_id, state="running", pid=os.getpid(), started=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
