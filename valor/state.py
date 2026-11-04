# *curls up next to the state folder* OwO hewwo! valor/state.py keeps the engine's per-range records ^w^
# After every apply/verify/destroy, the full result is saved as last-<kind>.json, and a short summary line is
# appended to history.jsonl (JSON Lines: one JSON object per line). The layout is drawn in the docstring below.
# Other modules (credentials, WireGuard, drafts) keep their files in the same per-range folder *wags tail*
"""Per-range records kept by the engine: the last apply / verify result and a history of every run.

Layout under the state directory:

    state/ranges/<range>/last-apply.json     newest result of that kind (apply, verify, destroy, ...)
    state/ranges/<range>/history.jsonl       one summary line per run, oldest first

Other modules keep their own files in the same per-range folder (credentials, WireGuard keys, the draft).
"""

# Lazy type hints UwU
from __future__ import annotations

# json writes/reads the records; re checks range names; shutil deletes a whole folder; Path builds paths >w<
import json
import re
import shutil
from pathlib import Path

# HISTORY_FIELDS: which keys of a result go into the history line. The full result can be big (every step of
# every host), so the history only keeps the interesting summary bits ^w^ *purrs*
# The fields of a result that are worth keeping in the history line (the full result is in last-<kind>.json).
HISTORY_FIELDS = ("job", "started", "ok", "changed", "seconds", "spec", "error", "message", "summary")


# range_dir: the folder for one range's records, created if it's missing. `cfg` is the engine Config (it knows
# state_dir), `name` the range's name OwO
def range_dir(cfg, name: str) -> Path:
    """The range's state folder (created on first use). The name check keeps paths inside the state directory."""
    # SAFETY *ears go flat*: the name must look like a real range name (lowercase letter first, then letters, digits
    # or dashes, 2-16 chars, no dash at the end). That means a name like "../../etc" can never escape the state
    # folder and touch files elsewhere >w<
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,14}[a-z0-9]", name):
        raise ValueError("invalid range name")
    # <state_dir>/state/ranges/<name>, made with parents=True (create missing parent folders) and exist_ok=True
    # (don't complain if it's already there) ^w^
    folder = Path(cfg.state_dir) / "state" / "ranges" / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# forget: delete EVERYTHING kept for a range (results, history, draft, login, WireGuard keys). Called when a range
# is deleted, so a new range that reuses the name doesn't inherit old secrets *sweeps it away with tail*
def forget(cfg, name: str) -> None:
    """Drop everything kept for a deleted range (results, history, draft, login, WireGuard keys), so a new range of
    the same name starts clean."""
    # ignore_errors=True: if the folder is already gone (or a file vanishes mid-delete), that's fine UwU
    shutil.rmtree(range_dir(cfg, name), ignore_errors=True)


# save: record one result. `kind` is "apply", "verify", "destroy", ...; `data` is the full result dict OwO
def save(cfg, name: str, kind: str, data: dict) -> None:
    folder = range_dir(cfg, name)
    # Overwrite last-<kind>.json with the full result, pretty-printed (indent=2). default=str turns anything JSON
    # can't handle natively (dates, Paths) into strings instead of crashing >w<
    (folder / f"last-{kind}.json").write_text(json.dumps(data, indent=2, default=str))
    # Build the short history entry: the kind first, then just the HISTORY_FIELDS (missing ones become None) ^w^
    summary = {"kind": kind, **{field: data.get(field) for field in HISTORY_FIELDS}}
    # Open history.jsonl in append mode ("a") so older lines stay, and add one line for this run *wags tail*
    with (folder / "history.jsonl").open("a") as fh:
        fh.write(json.dumps(summary, default=str) + "\n")


# load: the newest full result of a kind, or None when that kind never ran for this range UwU
def load(cfg, name: str, kind: str) -> dict | None:
    path = range_dir(cfg, name) / f"last-{kind}.json"
    return json.loads(path.read_text()) if path.exists() else None


# history: every summary line, oldest first, as a list of dicts OwO
def history(cfg, name: str) -> list[dict]:
    path = range_dir(cfg, name) / "history.jsonl"
    # No history file yet (a brand-new range) = an empty list, not an error ^w^
    if not path.exists():
        return []
    # Parse each non-empty line as JSON (skipping blank lines keeps a stray newline from breaking the parse) *purrs*
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
