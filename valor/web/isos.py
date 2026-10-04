"""ISO library API: list media, one-click catalog downloads, URL downloads, browser uploads, delete.

Downloads run as Proxmox tasks on the node (Proxmox checks the checksum); uploads stream through the VALOR VM.
Every change is audited. Operators may add media; only admins delete it.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import isos
from ..errors import ValorError
from ..pve import PVE
from . import db
from .core import ApiError, Session, require

router = APIRouter(prefix="/api/isos", tags=["isos"])
UPLOAD_DIR = "uploads"                 # under the VALOR state dir
MAX_TASKS = 30


class DownloadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog: str | None = Field(default=None, max_length=64)
    url: str | None = Field(default=None, max_length=2000)
    filename: str | None = Field(default=None, max_length=200)
    checksum: str | None = Field(default=None, max_length=128, pattern="^[0-9a-fA-F]+$")
    algorithm: str | None = Field(default=None, pattern="^(md5|sha1|sha224|sha256|sha384|sha512)$")


def _cfg(request: Request):
    return request.app.state.cfg


def _tasks(s: Session) -> list:
    return db.get_setting(s.conn, "iso_tasks", [])


def _remember(s: Session, entry: dict) -> None:
    tasks = [entry] + _tasks(s)
    db.put_setting(s.conn, "iso_tasks", tasks[:MAX_TASKS], by=s.username)


def _progress(log: list[str]) -> float | None:
    for line in reversed(log):
        m = re.search(r"(\d+(?:\.\d+)?)%", line)
        if m:
            return min(100.0, float(m.group(1)))
    return None


@router.get("")
def library(request: Request, s: Session = Depends(require("viewer"))) -> dict:
    cfg = _cfg(request)
    try:
        items, error = isos.list_isos(PVE(cfg)), None
    except ValorError as e:
        items, error = [], e.message
    return {"target": cfg.iso_storage, "storages": list(cfg.iso_storages), "isos": items, "error": error}


@router.get("/catalog")
def catalog(request: Request, s: Session = Depends(require("viewer"))) -> dict:
    cfg = _cfg(request)
    try:
        names = {i["name"] for i in isos.list_isos(PVE(cfg))}
    except ValorError:
        names = set()
    out = []
    for key, e in isos.load_catalog(cfg).items():
        present = sorted(n for n in names if (re.fullmatch(e["pattern"], n) if "pattern" in e else n == e.get("filename")))
        out.append({"id": key, "title": e["title"], "kind": e["kind"], "family": e.get("family"),
                    "install": e.get("install"), "signed": bool(e.get("checksums_sig_url")),
                    "checksum": "checksums_url" in e or "sha256" in e, "note": e.get("note", ""), "present": present})
    return {"entries": out}


@router.post("/download")
def download(body: DownloadIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    if body.catalog:
        r = isos.resolve(cfg, body.catalog)
        url, filename, checksum, algorithm, verified = r["url"], r["filename"], r["checksum"], r["algorithm"], r["verified"]
    else:
        if not body.url or not body.filename:
            raise ApiError(400, "invalid", "Give a catalog entry, or a URL and a file name.")
        if bool(body.checksum) != bool(body.algorithm):
            raise ApiError(400, "invalid", "Give both a checksum and its algorithm, or neither.")
        url, filename, checksum, algorithm = body.url, body.filename, body.checksum, body.algorithm
        verified = "checksum" if checksum else "https only"
    upid = isos.download(PVE(cfg), url, filename, checksum, algorithm)
    _remember(s, {"upid": upid, "kind": "download", "filename": filename, "user": s.username,
                  "started": time.time(), "verified": verified})
    s.audit("iso.download", target=filename, detail={"url": url, "verified": verified})
    return {"upid": upid, "filename": filename, "verified": verified}


@router.get("/tasks")
def tasks(request: Request, s: Session = Depends(require("viewer"))) -> dict:
    pve = PVE(_cfg(request))
    out = []
    for t in _tasks(s)[:10]:
        try:
            st = pve.task(t["upid"])
        except ValorError:
            continue
        done = st.get("status") == "stopped"
        out.append({**t, "running": not done, "ok": done and st.get("exitstatus") == "OK",
                    "exitstatus": st.get("exitstatus"), "progress": 100.0 if done else _progress(st["log"]),
                    "last": (st["log"] or [""])[-1][:200]})
    return {"tasks": out}


@router.post("/upload")
async def upload(request: Request, filename: str = Query(max_length=200),
                 checksum: str | None = Query(default=None, max_length=128, pattern="^[0-9a-fA-F]+$"),
                 algorithm: str | None = Query(default=None, pattern="^(sha256|sha512)$"),
                 s: Session = Depends(require("operator"))) -> dict:
    """The request body is the raw ISO (streamed to disk, hashed on the way, then streamed to Proxmox)."""
    cfg = _cfg(request)
    isos.check_filename(filename)
    if not cfg.iso_storage:
        raise ApiError(400, "no_iso_storage", "No ISO storage is configured.")
    size = int(request.headers.get("content-length") or 0)
    tmpdir = Path(cfg.state_dir) / UPLOAD_DIR
    tmpdir.mkdir(parents=True, exist_ok=True)
    if size and shutil.disk_usage(tmpdir).free < size + 2 * 2**30:
        raise ApiError(507, "no_space", "Not enough free space in the VALOR VM for this upload.")
    h256, h512 = hashlib.sha256(), hashlib.sha512()
    fd, path = tempfile.mkstemp(dir=tmpdir, suffix=".iso")
    try:
        with os.fdopen(fd, "wb") as fh:
            async for chunk in request.stream():
                fh.write(chunk)
                h256.update(chunk)
                h512.update(chunk)
        if checksum:
            got = (h256 if algorithm == "sha256" else h512).hexdigest()
            if got.lower() != checksum.lower():
                raise ApiError(400, "checksum_mismatch", "The uploaded file does not match the checksum you gave.")
        pve = PVE(cfg)
        upid = await asyncio.to_thread(pve.upload_iso, cfg.iso_storage, path, filename, h256.hexdigest(), "sha256")
        await asyncio.to_thread(pve.wait_task, upid, f"import {filename}", 3600)
    finally:
        Path(path).unlink(missing_ok=True)
    _remember(s, {"upid": upid, "kind": "upload", "filename": filename, "user": s.username, "started": time.time(),
                  "verified": "checksum" if checksum else "uploaded", "sha256": h256.hexdigest()})
    s.audit("iso.upload", target=filename, detail={"sha256": h256.hexdigest(), "bytes": size})
    return {"ok": True, "filename": filename, "sha256": h256.hexdigest()}


@router.delete("/{volid:path}")
def delete(volid: str, request: Request, s: Session = Depends(require("admin"))) -> dict:
    isos.delete(PVE(_cfg(request)), volid)
    s.audit("iso.delete", target=volid)
    return {"ok": True}
