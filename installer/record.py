# *keeps a careful little notebook* OwO installer/record.py: the INSTALL RECORD! ^w^
# Everything an installation created (pools, users, bridges, VMs, templates...) is written down in
# /etc/pve/priv/valor/<id>.json (cluster-wide, root-only). Upgrade and uninstall read it, so they only ever touch what
# VALOR made; a reused object that already existed is never deleted. It's saved after EVERY step, so a failed install
# can be resumed or cleanly removed *wags tail*
"""The install record: everything an installation created, kept cluster-wide and root-only in /etc/pve/priv.

Upgrade and uninstall read it, so they touch only what VALOR made (a reused, pre-existing object is never
deleted). It is written after every step, so a failed install can be resumed or cleanly removed.
"""

# Lazy type hints UwU
from __future__ import annotations

# fcntl (file locking), json, time, contextmanager, asdict (answers -> dict), Path >w<
import fcntl
import json
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

# Where records live, and the installer's version OwO
from . import RECORD_DIR, VERSION

# LOCK_DIR: where the lock files go (a tmpfs) ^w^
LOCK_DIR = Path("/run/lock")


# Record: one installation's record UwU
class Record:
    def __init__(self, instance: str, existing: dict | None = None):
        # A new record starts with the instance id, a creation time, and empty objects/steps. _dirty remembers what THIS run
        # changed, _added/_retired the templates it built or removed (for merging with other runs) >w<
        self.instance = instance
        self.data = existing or {"instance": instance, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                 "objects": {}, "steps": {}}
        self._dirty: set[tuple[str, str]] = set()          # what this run changed: ("objects"|"steps"|"", key)
        self._added: dict[int, dict] = {}                   # templates this run built
        self._retired: set[int] = set()                     # template VMIDs this run removed

    # path: <record dir>/<instance>.json OwO
    @property
    def path(self):
        return RECORD_DIR / f"{self.instance}.json"

    # objects: what was created ^w^
    @property
    def objects(self) -> dict:
        return self.data.setdefault("objects", {})

    # done(): has a step finished? mark(): record a step's state. set(): record an object. Each saves at once UwU
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

    # add_template() / remove_template(): record a built or retired template, under the lock, merged with what's on disk >w<
    def add_template(self, entry: dict) -> None:
        with self._locked():
            self._added[entry["vmid"]] = entry
            if not self.path.exists():
                self.objects.setdefault("templates", []).append(entry)
            self._merge_disk()
            self._write()

    def remove_template(self, vmid: int) -> None:
        with self._locked():
            self._retired.add(vmid)
            self._merge_disk()
            self._write()

    # save(): optionally record the answers, then merge with the disk and write, under the lock OwO
    def save(self, answers=None) -> None:
        if answers is not None:
            self.data["answers"] = asdict(answers)
            self._dirty.add(("", "answers"))
        with self._locked():
            self._merge_disk()
            self._write()

    # (the comment below explains why records are merged, not overwritten) ^w^
    # Two installer runs may share the record (e.g. template builds in two shells): every write merges what the
    # other run saved meanwhile instead of overwriting it.
    # _locked(): an exclusive file lock for this instance (another installer run waits) UwU
    @contextmanager
    def _locked(self):
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOCK_DIR / f"valor-installer-{self.instance}.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield

    # _merge_disk(): combine with the record on disk: the disk's newer values win, except what this run changed itself;
    # template lists are united (minus retired ones) >w<
    def _merge_disk(self) -> None:
        """Newer values on disk win, except for what this run changed itself; template lists are united."""
        try:
            disk = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return

        # merged(): one section merged that way OwO
        def merged(section: str) -> dict:
            mine, theirs = self.data.get(section, {}), disk.get(section, {})
            out = {**mine, **theirs}
            out.update({key: value for key, value in mine.items() if (section, key) in self._dirty})
            return out

        objects = merged("objects")
        templates = {t["vmid"]: t for t in disk.get("objects", {}).get("templates", [])}     # the disk knows best,
        templates.update(self._added)                                                        # plus this run's
        templates = {v: t for v, t in templates.items() if v not in self._retired}
        if templates or "templates" in objects:
            objects["templates"] = sorted(templates.values(), key=lambda t: t["vmid"])
        # The answers: ours if we changed them, else the disk's ^w^
        answers = self.data.get("answers") if ("", "answers") in self._dirty else disk.get("answers", self.data.get("answers"))
        self.data = {**self.data, **disk, "objects": objects, "steps": merged("steps")}
        if answers is not None:
            self.data["answers"] = answers

    # _write(): stamp the version and time, then write atomically (temp file + rename) UwU
    def _write(self) -> None:
        self.data.update(version=VERSION, updated=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{id(self)}.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    # delete(): remove the record (uninstall) >w< *closes the notebook*
    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
