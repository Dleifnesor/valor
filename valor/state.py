"""Per-range records kept by the engine (last apply / verify results and history)."""

from __future__ import annotations

import json
import re
from pathlib import Path


def range_dir(cfg, name: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,14}[a-z0-9]", name):
        raise ValueError("invalid range name")
    d = Path(cfg.state_dir) / "state" / "ranges" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(cfg, name: str, kind: str, data: dict) -> None:
    d = range_dir(cfg, name)
    (d / f"last-{kind}.json").write_text(json.dumps(data, indent=2, default=str))
    with (d / "history.jsonl").open("a") as fh:
        fh.write(json.dumps({"kind": kind, **{k: data.get(k) for k in
                 ("job", "started", "ok", "changed", "seconds", "spec", "error", "message", "summary")}}, default=str) + "\n")


def load(cfg, name: str, kind: str) -> dict | None:
    p = range_dir(cfg, name) / f"last-{kind}.json"
    return json.loads(p.read_text()) if p.exists() else None


def history(cfg, name: str) -> list[dict]:
    p = range_dir(cfg, name) / "history.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
