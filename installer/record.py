"""The install record: everything an installation created, kept cluster-wide and root-only in /etc/pve/priv.

Upgrade and uninstall read it, so they touch only what VALOR made (a reused, pre-existing object is never
deleted). It is written after every step, so a failed install can be resumed or cleanly removed.
"""

from __future__ import annotations

import fcntl
import json
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from . import RECORD_DIR, VERSION

LOCK_DIR = Path("/run/lock")


class Record:
    def __init__(self, instance: str, existing: dict | None = None):
        self.instance = instance
        self.data = existing or {"instance": instance, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                 "objects": {}, "steps": {}}
        self._dirty: set[tuple[str, str]] = set()          # what this run changed: ("objects"|"steps"|"", key)

    @property
    def path(self):
        return RECORD_DIR / f"{self.instance}.json"

    @property
    def objects(self) -> dict:
        return self.data.setdefault("objects", {})

    def done(self, step: str) -> bool:
        return self.data.get("steps", {}).get(step) == "done"

    def mark(self, step: str, state: str = "done") -> None:
        self.data.setdefault("steps", {})[step] = state
        self._dirty.add(("steps", step))
        self.save()

    def set(self, key: str, value) -> None:
        self.objects[key] = value
        self._dirty.add(("objects", key))
        self.save()

    def add_template(self, entry: dict) -> None:
        with self._locked():
            self._merge_disk()
            templates = [t for t in self.objects.get("templates", []) if t["vmid"] != entry["vmid"]]
            self.objects["templates"] = templates + [entry]
            self._write()

    def save(self, answers=None) -> None:
        if answers is not None:
            self.data["answers"] = asdict(answers)
            self._dirty.add(("", "answers"))
        with self._locked():
            self._merge_disk()
            self._write()

    # Two installer runs may share the record (e.g. template builds in two shells): every write merges what the
    # other run saved meanwhile instead of overwriting it.
    @contextmanager
    def _locked(self):
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOCK_DIR / f"valor-installer-{self.instance}.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield

    def _merge_disk(self) -> None:
        """Newer values on disk win, except for what this run changed itself; template lists are united."""
        try:
            disk = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return

        def merged(section: str) -> dict:
            mine, theirs = self.data.get(section, {}), disk.get(section, {})
            out = {**mine, **theirs}
            out.update({k: v for k, v in mine.items() if (section, k) in self._dirty})
            return out

        objects = merged("objects")
        templates = {t["vmid"]: t for t in disk.get("objects", {}).get("templates", [])}
        templates.update({t["vmid"]: t for t in self.objects.get("templates", [])})
        if templates:
            objects["templates"] = sorted(templates.values(), key=lambda t: t["vmid"])
        answers = self.data.get("answers") if ("", "answers") in self._dirty else disk.get("answers", self.data.get("answers"))
        self.data = {**self.data, **disk, "objects": objects, "steps": merged("steps")}
        if answers is not None:
            self.data["answers"] = answers

    def _write(self) -> None:
        self.data.update(version=VERSION, updated=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{id(self)}.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
