"""Structured errors returned to the agent and to CI (FR-10)."""

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
        d = {"error": self.code, "message": self.message}
        for k in ("step", "host", "details", "hint"):
            v = getattr(self, k)
            if v not in (None, "", []):
                d[k] = v
        d["completed_steps"] = self.completed_steps
        return d


class SpecError(ValorError):
    def __init__(self, message: str, errors: list[dict] | None = None, hint: str | None = None):
        super().__init__("spec_invalid", message, details=errors or [], hint=hint)


class LockBusy(ValorError):
    def __init__(self, holder: str):
        super().__init__("engine_busy", f"another infrastructure job is running ({holder})",
                         hint="Wait for it to finish (job_status) and try again; only one job runs at a time.")
