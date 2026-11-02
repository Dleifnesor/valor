"""Structured errors returned to the agent and to CI (FR-10).

Every failure the engine reports is a ValorError: a short machine-readable `code` (what the UI, the CLI's exit
codes and the AI match on), a sentence for people, and where it happened. `to_dict()` is the JSON shape the API,
the CLI (`--json`) and the job results all use.
"""

from __future__ import annotations

from typing import Any


class ValorError(Exception):
    """An error with a machine-readable code, the failing step and the steps completed so far."""

    def __init__(self, code: str, message: str, *, step: str | None = None, host: str | None = None,
                 details: Any = None, hint: str | None = None, completed_steps: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.step = step
        self.host = host
        self.details = details
        self.hint = hint
        self.completed_steps = completed_steps or []

    def to_dict(self) -> dict:
        out = {"error": self.code, "message": self.message}
        # Optional fields are left out when empty, so the JSON stays short for the common case.
        for field in ("step", "host", "details", "hint"):
            value = getattr(self, field)
            if value not in (None, "", []):
                out[field] = value
        out["completed_steps"] = self.completed_steps
        return out


class SpecError(ValorError):
    """The range spec itself is wrong; `details` lists each problem (field path + message)."""

    def __init__(self, message: str, errors: list[dict] | None = None, hint: str | None = None):
        super().__init__("spec_invalid", message, details=errors or [], hint=hint)


class LockBusy(ValorError):
    """Another build/destroy holds the engine lock (only one infrastructure job runs at a time)."""

    def __init__(self, holder: str):
        super().__init__("engine_busy", f"another infrastructure job is running ({holder})",
                         hint="Wait for it to finish (job_status) and try again; only one job runs at a time.")
