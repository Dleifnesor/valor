# *rolls up sleeves* OwO valor/ops.py RUNS a job from start to finish! ^w^
# The web API and the CLI never call apply/verify/destroy directly: they create a job, and execute() runs it
# (inside the valor-worker service for the web UI, or right away in the CLI). It takes the engine lock, calls the
# right handler for the job's kind, and ALWAYS ends with a result and a final state, even when things go wrong.
# Builds and verifies also update the range's records and its build journal *wags tail*
"""Running a job: the engine lock, the job record, the engine call, the range's state and its journal.

The web API and the CLI never call apply/verify/destroy directly: they create a job (jobs.create), and
`execute` runs it - in the valor-worker service for the web UI, in the same process for the CLI. Every job kind
has a handler below. Whatever happens, a job ends with a result.json and the state "succeeded" or "failed", and
an apply or verify also updates the range's state record and its build journal.
"""

# Lazy type hints UwU
from __future__ import annotations

# time for timestamps >w<
import time

# The modules jobs use: credentials (range login), jobs (the queue), journal (what a build did), lifecycle
# (power/snapshots), state (per-range records), wireguard (VPN keys); then the three big engine functions, renamed
# do_apply / do_destroy / do_verify so they don't clash with the handler method names below OwO
from . import credentials, jobs, journal, lifecycle, state, wireguard
from .apply import apply as do_apply
from .destroy import destroy as do_destroy
from .errors import ValorError
from .pve import PVE
from .spec import load_spec, spec_hash
from .verify import verify as do_verify


# _stamp: the current time as "2026-11-03T22:33:00+0000" ^w^
def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# execute: run a job to completion. It NEVER raises: whatever happens, it returns a result with "ok", saves it,
# and marks the job succeeded or failed UwU
def execute(cfg, job_id: str, echo=None) -> dict:
    """Run a job (apply / verify / destroy) to completion. Never raises; returns a result dict with 'ok'."""
    run = _JobRun(cfg, job_id, echo)
    result = run.execute()
    jobs.finish(cfg, job_id, result)
    jobs.update(cfg, job_id, state="succeeded" if result.get("ok") else "failed", finished=_stamp())
    return result


# _JobRun: one job being executed. Handlers set self.spec / self.path once they know the range's spec, so the
# journal and the state record can be written even when the job fails half way >w<
class _JobRun:
    """One job being executed. Handlers set `spec` / `path` once they know the range's spec, so the journal
    and the state record are written even when the job fails half way."""

    # Read the job, set up the event log, mark the job running (records this process id), and remember the start OwO
    def __init__(self, cfg, job_id: str, echo=None):
        self.cfg, self.job_id = cfg, job_id
        job = jobs.status(cfg, job_id)
        self.kind, self.target = job["kind"], job["target"]
        self.events = jobs.Events(cfg, job_id, echo)
        jobs.mark_running(cfg, job_id)
        self.spec = self.path = None
        self.started = _stamp()
        self.pve: PVE | None = None

    # execute: the main flow of one job ^w^
    def execute(self) -> dict:
        cfg = self.cfg
        # Which method handles which job kind UwU
        handlers = {
            "apply": self.apply, "verify": self.verify, "destroy": self.destroy, "power": self.power,
            "snapshot": self.snapshot, "rollback": self.rollback, "snapshot_delete": self.snapshot_delete,
            "rotate": self.rotate_login, "wg_rotate": self.rotate_wireguard,
        }
        try:
            # Hold the engine lock for the whole job (only one job may change the cluster at a time) >w<
            with jobs.engine_lock(cfg, f"{self.kind} job {self.job_id}"):
                self.pve = PVE(cfg)
                # Builds and verifies load the spec first and log which range and spec version this is OwO
                if self.kind in ("apply", "verify"):
                    self.load_spec(self.target["spec"])
                    self.events("spec", range=self.spec.name, version=spec_hash(self.spec)[:12])
                # Unknown kinds are an error ^w^
                handler = handlers.get(self.kind)
                if handler is None:
                    raise ValorError("job_invalid", f"unknown job kind {self.kind}")
                # Run the handler UwU
                result = handler()
                # Write the build journal; a journal problem is recorded but never fails the job >w<
                if self.spec is not None:
                    try:
                        result["journal"] = str(journal.write(cfg, self.pve, self.spec, self.path))
                    except Exception as e:  # the journal must never fail a build
                        result["journal_error"] = str(e)
            return result
        # An engine error: build a failed result from it OwO
        except ValorError as e:
            result = {**e.to_dict(), "ok": False, "job": self.job_id, "started": self.started}
            # Still save the failed result to the range's records and write the journal (best effort), so the person can see
            # what happened ^w^
            if self.spec is not None:
                state.save(cfg, self.spec.name, self.kind, result)
                try:
                    journal.write(cfg, PVE(cfg), self.spec, self.path)
                except Exception:
                    pass
            return result
        # Anything unexpected becomes an "internal_error" result instead of crashing the worker UwU
        except Exception as e:
            return {"ok": False, "error": "internal_error", "message": f"{e.__class__.__name__}: {e}",
                    "job": self.job_id}

    # ---- helpers ----

    # load_spec: load the job's spec file (normalized, with defaults filled in) >w<
    def load_spec(self, name: str) -> None:
        self.spec, self.path = load_spec(name, self.cfg.ranges_dir, self.cfg.default_os)

    # done: mark a handler's result as successful, with the job id, start time and any extra fields OwO
    def done(self, result: dict, **extra) -> dict:
        result.update(ok=True, job=self.job_id, started=self.started, **extra)
        return result

    # run_verify: verify the range and save the result as its latest verification ^w^
    def run_verify(self) -> dict:
        """Verify the range and keep the result as the range's latest verification."""
        verification = do_verify(self.pve, self.spec, self.events)
        verification.update(job=self.job_id, started=self.started, finished=_stamp())
        state.save(self.cfg, self.spec.name, "verify", verification)
        return verification

    # ---- one handler per job kind ----

    # apply: build the range, save the result, then (if asked) verify it UwU
    def apply(self) -> dict:
        """Build; with target.verify (the web UI always asks) verify right after and, when the range passed and
        changed (or has no clean snapshot yet), take the automatic 'clean' snapshot."""
        result = self.done(do_apply(self.pve, self.spec, self.events))
        state.save(self.cfg, self.spec.name, "apply", result)
        # A failed verification makes the whole job not ok >w<
        if self.target.get("verify"):
            verification = self.run_verify()
            result["verify"] = {"ok": verification["ok"], "summary": verification["summary"]}
            result["ok"] = verification["ok"]
            # Passed AND changed something (or there's no clean snapshot yet)? Take the automatic valor-clean snapshot OwO
            if verification["ok"] and (result.get("changed") or not _has_clean(self.pve, self.spec.name)):
                result["snapshot"] = _clean_snapshot(self.pve, self.spec.name, self.events)
        return result

    # verify: just verify ^w^
    def verify(self) -> dict:
        return self.run_verify()

    # destroy: tear the range down and forget its login and WireGuard keys (a rebuilt range gets new ones) UwU
    def destroy(self) -> dict:
        name = self.target["range"]
        result = self.done(do_destroy(self.pve, name, self.events))
        state.save(self.cfg, name, "destroy", result)
        credentials.forget(self.cfg, name)      # a rebuilt range gets a new password
        wireguard.forget(self.cfg, name)        # ... and new WireGuard keys
        return result

    # power: start/shutdown/stop/reboot the range (or some hosts) >w<
    def power(self) -> dict:
        target = self.target
        return self.done(lifecycle.power(self.pve, target["range"], target["action"], target.get("hosts"),
                                         self.events))

    # snapshot: take a named snapshot of every VM OwO
    def snapshot(self) -> dict:
        target = self.target
        return self.done(lifecycle.snapshot(self.pve, target["range"], target["name"], target.get("description", ""),
                                            emit=self.events))

    # rollback: reset to a snapshot, wait until every VM's guest agent answers (up to 5 min each), then VERIFY: a reset
    # range must prove it works ^w^
    def rollback(self) -> dict:
        """Roll back, wait for the guests, then verify: a reset range must prove it works again."""
        target = self.target
        result = self.done(lifecycle.rollback(self.pve, target["range"], target["name"],
                                              target.get("delete_newer", False), emit=self.events))
        self.load_spec(f"{target['range']}.yaml")
        for vm in lifecycle._vms(self.pve, self.spec.name):
            self.pve.wait_agent(vm.vmid, 300)
        verification = self.run_verify()
        result["verify"] = {"ok": verification["ok"], "summary": verification["summary"]}
        result["ok"] = verification["ok"]
        return result

    # snapshot_delete: delete a snapshot from every VM UwU
    def snapshot_delete(self) -> dict:
        target = self.target
        return self.done(lifecycle.delete_snapshot(self.pve, target["range"], target["name"], emit=self.events))

    # rotate_login: make a new range password, then apply: the new password version makes every VM converge, which
    # sets the new password inside it >w<
    def rotate_login(self) -> dict:
        self.load_spec(f"{self.target['range']}.yaml")
        credentials.rotate(self.cfg, self.spec.name)
        result = do_apply(self.pve, self.spec, self.events)     # the new password version re-converges every VM
        self.done(result, rotated=True)
        state.save(self.cfg, self.spec.name, "apply", result)
        return result

    # rotate_wireguard: new keys for some peers, then apply so the router gets them OwO
    def rotate_wireguard(self) -> dict:
        self.load_spec(f"{self.target['range']}.yaml")
        spec = self.spec
        if not (spec.access and spec.access.wireguard):
            raise ValorError("wireguard_not_configured", f"range '{spec.name}' has no WireGuard access")
        wireguard.ensure(self.cfg, spec, rotate=tuple(self.target["peers"]))
        result = do_apply(self.pve, spec, self.events)          # new keys: the router converges
        self.done(result, rotated=self.target["peers"])
        state.save(self.cfg, spec.name, "apply", result)
        return result


# _has_clean: does the range already have a valor-clean snapshot? (False if Proxmox can't be asked) ^w^
def _has_clean(pve: PVE, name: str) -> bool:
    try:
        return any(snap["name"] == lifecycle.CLEAN for snap in lifecycle.list_snapshots(pve, name))
    except ValorError:
        return False


# _clean_snapshot: take (or replace) the valor-clean snapshot. A failure is reported as an event and in the result,
# but never fails the build UwU
def _clean_snapshot(pve: PVE, name: str, events) -> dict:
    """The automatic 'clean' snapshot after a verified build. A failure here never fails the build."""
    try:
        lifecycle.snapshot(pve, name, lifecycle.CLEAN, f"verified build {_stamp()}", replace=True, emit=events)
        return {"ok": True, "name": lifecycle.CLEAN}
    except ValorError as e:
        events("snapshot_failed", error=e.code, message=e.message)
        return {"ok": False, "name": lifecycle.CLEAN, "error": e.code, "message": e.message}


# start: create a job and run it now (CLI, background=False) or leave it queued for the worker (web UI) >w<
def start(cfg, kind: str, target: dict, origin: str, background: bool, echo=None) -> dict:
    """Create a job; run it now (CLI) or leave it queued for the valor-worker service (web UI)."""
    job_id = jobs.create(cfg, kind, target, origin)
    if background:                        # the valor-worker service picks queued jobs up in order
        return {"job": job_id, "state": "queued", "hint": "follow it with 'valor job status <job>' or in the web UI"}
    return execute(cfg, job_id, echo)
