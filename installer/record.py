"""The install record: everything an installation created, kept cluster-wide and root-only in /etc/pve/priv.

Upgrade and uninstall read it, so they touch only what VALOR made (a reused, pre-existing object is never
deleted). It is written after every step, so a failed install can be resumed or cleanly removed.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict

from . import RECORD_DIR, VERSION


class Record:
    def __init__(self, instance: str, existing: dict | None = None):
        self.instance = instance
        self.data = existing or {"instance": instance, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                 "objects": {}, "steps": {}}

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
        self.save()

    def set(self, key: str, value) -> None:
        self.objects[key] = value
        self.save()

    def save(self, answers=None) -> None:
        if answers is not None:
            self.data["answers"] = asdict(answers)
        self.data.update(version=VERSION, updated=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
