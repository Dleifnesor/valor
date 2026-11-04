# *stacks job folders neatly with paws* OwO valor/jobs.py is VALOR's job queue! ^w^
# Long tasks (builds, verifies, teardowns, snapshots...) run as background JOBS, so the web page never has to wait.
# Each job is just a folder on disk (layout in the docstring): job.json says what and who, events.jsonl collects
# progress as it happens (the UI's live log), and result.json holds the outcome. The engine LOCK makes sure only
# one job changes the cluster at a time, even if someone also uses the CLI *wags tail*
"""Background jobs with persistent logs; one infrastructure job at a time (NFR Reliability/Responsiveness).

A job is a folder under the state directory:

    jobs/<id>/job.json        kind, target, who started it, state (queued -> running -> succeeded | failed)
    jobs/<id>/events.jsonl    progress events as they happen (the UI's live task log)
    jobs/<id>/result.json     the final result

Job IDs start with the creation time, so sorting the folder names sorts the jobs. The engine lock (a file lock)
makes sure only one job changes the cluster at a time, even with the CLI and the worker running side by side.
"""

# Lazy type hints UwU
from __future__ import annotations

# fcntl does file locking; json/os/time handle job files, process ids and timestamps; secrets makes the random
# part of job ids; contextmanager turns engine_lock into a `with` block; Path builds paths >w<
import fcntl
import json
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

# LockBusy = "another job holds the lock"; ValorError for bad/unknown job ids OwO
from .errors import LockBusy, ValorError


# _dir: a job's folder. Job ids arrive in URLs, so they're checked first ^w^
def _dir(cfg, job_id: str) -> Path:
    # job IDs come from URLs: only letters, digits and dashes, so a path can't leave the jobs folder
    if not all(char.isalnum() or char == "-" for char in job_id):
        raise ValorError("job_invalid", "invalid job id")
    return Path(cfg.jobs_dir) / job_id


# _write: save JSON safely: write to a .tmp file, then rename it over the real one (a rename is atomic, so a
# reader sees either the old file or the new one, never half of one) UwU
def _write(path: Path, data: dict) -> None:
    """Write through a temp file, so a reader never sees half a file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


# create: queue a new job and return its id >w<
#   kind    what to do ("apply", "verify", "destroy", "power", ...)
#   target  what to do it to (range, spec, hosts, options...)
#   origin  who asked (e.g. "web:alice" or "cli")
def create(cfg, kind: str, target: dict, origin: str) -> str:
    # The id is "YYYYmmdd-HHMMSS-" + 6 random hex chars, so sorting ids sorts jobs by creation time OwO
    job_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    folder = _dir(cfg, job_id)
    # mkdir without exist_ok: if the folder somehow exists already, fail instead of mixing two jobs ^w^
    folder.mkdir(parents=True)
    # The job starts out "queued", with its creation time UwU
    _write(folder / "job.json", {"id": job_id, "kind": kind, "target": target, "origin": origin, "state": "queued",
                            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    return job_id


# engine_lock: hold THE engine lock while a job changes the cluster. Use it as `with engine_lock(cfg, "apply x"):`
# If another process holds it, LockBusy is raised at once (no waiting) and names who has it >w<
@contextmanager
def engine_lock(cfg, holder: str):
    """Hold the engine lock for one job; raises LockBusy (naming the holder) if another job has it."""
    # The lock file lives in the state folder; "a+" opens it for reading and writing without wiping it OwO
    path = Path(cfg.state_dir) / "engine.lock"
    fh = path.open("a+")
    try:
        # flock: LOCK_EX = exclusive (only one holder), LOCK_NB = don't wait. The OS drops the lock by itself if the
        # process dies, so a crash can never leave a stuck lock ^w^ *nods wisely*
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Someone else has it: read their description from the file for the error message UwU
        fh.seek(0)
        raise LockBusy(fh.read().strip() or "unknown")
    # We have it! Replace the file's text with our own description so others can see who's busy >w<
    fh.seek(0)
    fh.truncate()
    fh.write(holder)
    fh.flush()
    # Run the job's code (the body of the `with` block)... OwO
    try:
        yield
    # ...and ALWAYS clean up afterwards, even on errors: clear the text, release the lock, close the file ^w^
    finally:
        fh.seek(0)
        fh.truncate()
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


# Events: the `emit` function the engine calls to report progress. Calling it appends one JSON line to the job's
# events.jsonl, and optionally passes the event to `echo` too (the CLI prints events to the terminal) UwU
class Events:
    """The `emit` callback the engine reports progress to: appends one JSON line per event (and echoes it to the
    CLI's terminal when asked)."""

    def __init__(self, cfg, job_id: str, echo=None):
        self.path = _dir(cfg, job_id) / "events.jsonl"
        self.echo = echo

    # Each event gets the time (HH:MM:SS), its name ("step_start", "step_done", "log"...) and its extra fields >w<
    def __call__(self, event: str, **fields) -> None:
        record = {"t": time.strftime("%H:%M:%S"), "event": event, **fields}
        with self.path.open("a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        if self.echo:
            self.echo(record)


# update: change some fields of job.json (e.g. the state) and return the whole updated job OwO
def update(cfg, job_id: str, **fields) -> dict:
    path = _dir(cfg, job_id) / "job.json"
    data = json.loads(path.read_text())
    data.update(fields)
    _write(path, data)
    return data


# finish: save the job's final result ^w^
def finish(cfg, job_id: str, result: dict) -> None:
    _write(_dir(cfg, job_id) / "result.json", result)


# status: the full picture of one job for the UI: job.json plus progress, the newest events and the result UwU
def status(cfg, job_id: str, tail: int = 25) -> dict:
    """The job with its progress (steps done, current step), its newest events and, once finished, its result."""
    folder = _dir(cfg, job_id)
    # Unknown job id = a clear error >w<
    if not folder.exists():
        raise ValorError("job_unknown", f"no job {job_id}")
    job = json.loads((folder / "job.json").read_text())
    # Read every event so far (skipping blank lines) OwO
    events = []
    if (folder / "events.jsonl").exists():
        events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines() if line.strip()]
    # Progress: how many steps finished, and the current step = the newest step_start, written like
    # "clone [web]" when it's about one host ^w^
    job["progress"] = {
        "steps_done": sum(event["event"] == "step_done" for event in events),
        "current": next((f"{event.get('step')}{' [' + event['host'] + ']' if event.get('host') else ''}"
                         for event in reversed(events) if event["event"] == "step_start"), None),
    }
    # Only the last `tail` events go to the UI (a long build has thousands) UwU
    job["recent_events"] = events[-tail:]
    if (folder / "result.json").exists():
        job["result"] = json.loads((folder / "result.json").read_text())
    # Safety net: "running" but the process is gone means it was killed; report that instead of "running" forever >w<
    # a "running" job whose process is gone was killed (e.g. the worker restarted): report it as failed
    if job.get("state") == "running" and job.get("pid") and not Path(f"/proc/{job['pid']}").exists():
        job["state"] = "failed"
        job["result"] = {"error": "runner_died", "message": "the job process ended unexpectedly; see the valor-worker log"}
    return job


# list_jobs: the newest `limit` jobs (summaries only), newest first OwO
def list_jobs(cfg, limit: int = 20) -> list[dict]:
    root = Path(cfg.jobs_dir)
    out = []
    for folder in sorted(root.iterdir(), reverse=True)[:limit] if root.exists() else []:
        # Reverse-sorted folder names = newest first, thanks to the time-based ids ^w^
        try:
            job = json.loads((folder / "job.json").read_text())
            # Just the summary fields; failed jobs also get their error code for the list UwU
            item = {key: job.get(key) for key in ("id", "kind", "state", "target", "created", "finished", "origin")}
            if item["state"] == "failed" and (folder / "result.json").exists():
                item["error"] = json.loads((folder / "result.json").read_text()).get("error")
            out.append(item)
        except Exception:               # a half-written or foreign folder: skip it
            continue
    return out


# next_queued: the OLDEST job still waiting (sorted oldest-first), or None. The worker calls this in its loop >w<
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


# mark_running: the job has started: set its state, remember this process's id (so a dead process can be
# noticed later) and the start time OwO *zooms off to work*
def mark_running(cfg, job_id: str) -> None:
    update(cfg, job_id, state="running", pid=os.getpid(), started=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
