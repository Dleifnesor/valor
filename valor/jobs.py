"""Background jobs with persistent logs; one infrastructure job at a time (NFR Reliability/Responsiveness).

A job is a folder under the state directory:

    jobs/<id>/job.json        kind, target, who started it, state (queued -> running -> succeeded | failed)
    jobs/<id>/events.jsonl    progress events as they happen (the UI's live task log)
    jobs/<id>/result.json     the final result

Job IDs start with the creation time, so sorting the folder names sorts the jobs. The engine lock (a file lock)
makes sure only one job changes the cluster at a time, even with the CLI and the worker running side by side.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import LockBusy, ValorError


def _dir(cfg, job_id: str) -> Path:
    # job IDs come from URLs: only letters, digits and dashes, so a path can't leave the jobs folder
    if not all(char.isalnum() or char == "-" for char in job_id):
        raise ValorError("job_invalid", "invalid job id")
    return Path(cfg.jobs_dir) / job_id


def _write(path: Path, data: dict) -> None:
    """Write through a temp file, so a reader never sees half a file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def create(cfg, kind: str, target: dict, origin: str) -> str:
    job_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    folder = _dir(cfg, job_id)
    folder.mkdir(parents=True)
    _write(folder / "job.json", {"id": job_id, "kind": kind, "target": target, "origin": origin, "state": "queued",
                            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    return job_id


@contextmanager
def engine_lock(cfg, holder: str):
    """Hold the engine lock for one job; raises LockBusy (naming the holder) if another job has it."""
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
    """The `emit` callback the engine reports progress to: appends one JSON line per event (and echoes it to the
    CLI's terminal when asked)."""

    def __init__(self, cfg, job_id: str, echo=None):
        self.path = _dir(cfg, job_id) / "events.jsonl"
        self.echo = echo

    def __call__(self, event: str, **fields) -> None:
        record = {"t": time.strftime("%H:%M:%S"), "event": event, **fields}
        with self.path.open("a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        if self.echo:
            self.echo(record)


def update(cfg, job_id: str, **fields) -> dict:
    path = _dir(cfg, job_id) / "job.json"
    data = json.loads(path.read_text())
    data.update(fields)
    _write(path, data)
    return data


def finish(cfg, job_id: str, result: dict) -> None:
    _write(_dir(cfg, job_id) / "result.json", result)


def status(cfg, job_id: str, tail: int = 25) -> dict:
    """The job with its progress (steps done, current step), its newest events and, once finished, its result."""
    folder = _dir(cfg, job_id)
    if not folder.exists():
        raise ValorError("job_unknown", f"no job {job_id}")
    job = json.loads((folder / "job.json").read_text())
    events = []
    if (folder / "events.jsonl").exists():
        events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines() if line.strip()]
    job["progress"] = {
        "steps_done": sum(event["event"] == "step_done" for event in events),
        "current": next((f"{event.get('step')}{' [' + event['host'] + ']' if event.get('host') else ''}"
                         for event in reversed(events) if event["event"] == "step_start"), None),
    }
    job["recent_events"] = events[-tail:]
    if (folder / "result.json").exists():
        job["result"] = json.loads((folder / "result.json").read_text())
    # a "running" job whose process is gone was killed (e.g. the worker restarted): report it as failed
    if job.get("state") == "running" and job.get("pid") and not Path(f"/proc/{job['pid']}").exists():
        job["state"] = "failed"
        job["result"] = {"error": "runner_died", "message": "the job process ended unexpectedly; see the valor-worker log"}
    return job


def list_jobs(cfg, limit: int = 20) -> list[dict]:
    root = Path(cfg.jobs_dir)
    out = []
    for folder in sorted(root.iterdir(), reverse=True)[:limit] if root.exists() else []:
        try:
            job = json.loads((folder / "job.json").read_text())
            item = {key: job.get(key) for key in ("id", "kind", "state", "target", "created", "finished", "origin")}
            if item["state"] == "failed" and (folder / "result.json").exists():
                item["error"] = json.loads((folder / "result.json").read_text()).get("error")
            out.append(item)
        except Exception:               # a half-written or foreign folder: skip it
            continue
    return out


def next_queued(cfg) -> str | None:
    """Oldest job still waiting to run (used by the worker service)."""
    root = Path(cfg.jobs_dir)
    for folder in sorted(root.iterdir()) if root.exists() else []:
        try:
            if json.loads((folder / "job.json").read_text()).get("state") == "queued":
                return folder.name
        except Exception:
            continue
    return None


def mark_running(cfg, job_id: str) -> None:
    update(cfg, job_id, state="running", pid=os.getpid(), started=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
