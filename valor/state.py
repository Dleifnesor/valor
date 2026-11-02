"""Per-range records kept by the engine: the last apply / verify result and a history of every run.

Layout under the state directory:

    state/ranges/<range>/last-apply.json     newest result of that kind (apply, verify, destroy, ...)
    state/ranges/<range>/history.jsonl       one summary line per run, oldest first

Other modules keep their own files in the same per-range folder (credentials, WireGuard keys, the draft).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

# The fields of a result that are worth keeping in the history line (the full result is in last-<kind>.json).
HISTORY_FIELDS = ("job", "started", "ok", "changed", "seconds", "spec", "error", "message", "summary")


def range_dir(cfg, name: str) -> Path:
    """The range's state folder (created on first use). The name check keeps paths inside the state directory."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,14}[a-z0-9]", name):
        raise ValueError("invalid range name")
    folder = Path(cfg.state_dir) / "state" / "ranges" / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def forget(cfg, name: str) -> None:
    """Drop everything kept for a deleted range (results, history, draft, login, WireGuard keys), so a new range of
    the same name starts clean."""
    shutil.rmtree(range_dir(cfg, name), ignore_errors=True)


def save(cfg, name: str, kind: str, data: dict) -> None:
    folder = range_dir(cfg, name)
    (folder / f"last-{kind}.json").write_text(json.dumps(data, indent=2, default=str))
    summary = {"kind": kind, **{field: data.get(field) for field in HISTORY_FIELDS}}
    with (folder / "history.jsonl").open("a") as fh:
        fh.write(json.dumps(summary, default=str) + "\n")


def load(cfg, name: str, kind: str) -> dict | None:
    path = range_dir(cfg, name) / f"last-{kind}.json"
    return json.loads(path.read_text()) if path.exists() else None


def history(cfg, name: str) -> list[dict]:
    path = range_dir(cfg, name) / "history.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
