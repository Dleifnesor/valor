# OwO *sniffs at the errors file* hewwo! This is valor/errors.py, where VALOR's ewwows are born >w<
# Every problem the engine can report is a ValorError (or a subclass), so every caller (the CLI, the web API,
# the job runner and the AI) gets the same shape: a short `code` to match on, a human sentence, and optional
# extra info about where it happened. *wags tail* The docstring below says the same thing in short.
"""Structured errors returned to the agent and to CI (FR-10).

Every failure the engine reports is a ValorError: a short machine-readable `code` (what the UI, the CLI's exit
codes and the AI match on), a sentence for people, and where it happened. `to_dict()` is the JSON shape the API,
the CLI (`--json`) and the job results all use.
"""

# Lazy type hints (so `str | None` works and costs nothing at import) UwU
from __future__ import annotations

# Any = "any type at all", used for `details`, which can be a list, a dict or a string ^w^
from typing import Any


# ValorError: the base class of every VALOR error. It IS a normal Python Exception, so `raise` and `except` work
# as usual, but it carries structured fields too *pounces on the exception*
class ValorError(Exception):
    """An error with a machine-readable code, the failing step and the steps completed so far."""

    # The constructor. `code` and `message` are required; everything after the `*` must be passed by name
    # (keyword-only), which keeps call sites readable: ValorError("x", "y", host="web", hint="...") OwO
    #   code             machine-readable id like "spec_invalid" or "engine_busy" (the UI and CLI exit codes use it)
    #   message          one sentence for a human
    #   step             which step failed ("clone", "start VM", ...)
    #   host             which VM it was about, if any
    #   details          extra data (e.g. a list of spec problems)
    #   hint             what the person can do about it
    #   completed_steps  what already finished before the failure (so a retry knows where things stand) ^w^
    def __init__(self, code: str, message: str, *, step: str | None = None, host: str | None = None,
                 details: Any = None, hint: str | None = None, completed_steps: list[str] | None = None):
        # Exception's own init gets the message, so str(error) and tracebacks show the human sentence >w<
        super().__init__(message)
        # Store every field on the instance so code can read error.code, error.hint, ... later UwU
        self.code = code
        self.message = message
        self.step = step
        self.host = host
        self.details = details
        self.hint = hint
        # `or []` gives each error its own fresh empty list (a shared mutable default would be a sneaky bug) *nuzzles*
        self.completed_steps = completed_steps or []

    # to_dict: the error as JSON-ready data. The web API, the CLI's --json mode and saved job results all use exactly
    # this shape, e.g. {"error": "engine_busy", "message": "...", "hint": "...", "completed_steps": []} ^w^
    def to_dict(self) -> dict:
        # Always present: the code (under the key "error") and the message *wags tail*
        out = {"error": self.code, "message": self.message}
        # Optional fields are left out when empty, so the JSON stays short for the common case.
        for field in ("step", "host", "details", "hint"):
            value = getattr(self, field)
            if value not in (None, "", []):
                out[field] = value
        # completed_steps is always included (even when empty) so readers can rely on the key existing OwO
        out["completed_steps"] = self.completed_steps
        return out


# SpecError: the range spec (the YAML describing a range) is invalid. Its code is always "spec_invalid", and
# `details` holds one entry per problem, each with the field path and what's wrong, so the editor and the AI can
# point right at the broken line >w< *paws at the YAML*
class SpecError(ValorError):
    """The range spec itself is wrong; `details` lists each problem (field path + message)."""

    # errors = the list of problems (may be None, then an empty list is stored); hint = optional advice UwU
    def __init__(self, message: str, errors: list[dict] | None = None, hint: str | None = None):
        super().__init__("spec_invalid", message, details=errors or [], hint=hint)


# LockBusy: raised when another build or destroy already holds the engine lock. Only ONE infrastructure job may
# change Proxmox at a time (so two jobs never fight over the same VMs) *guards the lock with fluffy tail*
class LockBusy(ValorError):
    """Another build/destroy holds the engine lock (only one infrastructure job runs at a time)."""

    # holder = a description of the job holding the lock, put into the message so the person knows what to wait for ^w^
    def __init__(self, holder: str):
        super().__init__("engine_busy", f"another infrastructure job is running ({holder})",
                         hint="Wait for it to finish (job_status) and try again; only one job runs at a time.")
