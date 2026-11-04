# *carries a stack of shiny CDs* OwO valor/web/isos.py is the ISO library API! ^w^
# List the installer images on Proxmox, download one from VALOR's catalog with one click (checksum-verified), from
# any URL, or upload one from your browser; admins can delete. Downloads run as Proxmox tasks on the node (Proxmox
# checks the checksum); uploads stream through the VALOR VM. Every change is audited *wags tail*
"""ISO library API: list media, one-click catalog downloads, URL downloads, browser uploads, delete.

Downloads run as Proxmox tasks on the node (Proxmox checks the checksum); uploads stream through the VALOR VM.
Every change is audited. Operators may add media; only admins delete it.
"""

# Lazy type hints UwU
from __future__ import annotations

# asyncio (run blocking uploads in a thread), hashlib (checksums), os/shutil/tempfile (temp files and free space),
# re (progress and names), time, Path >w<
import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

# FastAPI: router, Depends, Query (URL parameters), Request; pydantic for the download body OwO
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

# isos = the engine's ISO module (catalog, names, Proxmox calls); ValorError; PVE; db (task list); core helpers ^w^
from .. import isos
from ..errors import ValorError
from ..pve import PVE
from . import db
from .core import ApiError, Session, require

# All routes under /api/isos. Uploads are staged in <state>/uploads. MAX_TASKS: how many recent downloads/uploads
# are remembered for the progress list UwU
router = APIRouter(prefix="/api/isos", tags=["isos"])
UPLOAD_DIR = "uploads"                 # under the VALOR state dir
MAX_TASKS = 30


# DownloadIn: either a catalog entry id, OR a URL + file name with an optional checksum (hex digits only) and its
# algorithm (one of the hashes Proxmox supports) >w<
class DownloadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog: str | None = Field(default=None, max_length=64)
    url: str | None = Field(default=None, max_length=2000)
    filename: str | None = Field(default=None, max_length=200)
    checksum: str | None = Field(default=None, max_length=128, pattern="^[0-9a-fA-F]+$")
    algorithm: str | None = Field(default=None, pattern="^(md5|sha1|sha224|sha256|sha384|sha512)$")


# _cfg: the engine config from the app OwO
def _cfg(request: Request):
    return request.app.state.cfg


# _tasks / _remember: the recent ISO tasks, kept as a setting in the database, newest first, at most MAX_TASKS ^w^
def _tasks(s: Session) -> list:
    return db.get_setting(s.conn, "iso_tasks", [])


def _remember(s: Session, entry: dict) -> None:
    tasks = [entry] + _tasks(s)
    db.put_setting(s.conn, "iso_tasks", tasks[:MAX_TASKS], by=s.username)


# _progress: the newest "NN.N%" in a download task's log (capped at 100), or None UwU
def _progress(log: list[str]) -> float | None:
    """The newest percentage in a Proxmox download task's log, if any."""
    for line in reversed(log):
        percent = re.search(r"(\d+(?:\.\d+)?)%", line)
        if percent:
            return min(100.0, float(percent.group(1)))
    return None


# GET /api/isos: the library: where new media go, which storages are listed, the ISOs, and an error if Proxmox
# couldn't be asked >w<
@router.get("")
def library(request: Request, s: Session = Depends(require("viewer"))) -> dict:
    cfg = _cfg(request)
    try:
        items, error = isos.list_isos(PVE(cfg)), None
    except ValorError as e:
        items, error = [], e.message
    return {"target": cfg.iso_storage, "storages": list(cfg.iso_storages), "isos": items, "error": error}


# GET /api/isos/catalog: VALOR's catalog of known media, with which files of each are already present (by file
# name, or a pattern for entries whose name changes with each release) OwO
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


# POST /api/isos/download: start a download on the Proxmox node ^w^
@router.post("/download")
def download(body: DownloadIn, request: Request, s: Session = Depends(require("operator"))) -> dict:
    cfg = _cfg(request)
    # From the catalog: VALOR resolves the URL, file name and checksum itself (e.g. from a signed checksum list) UwU
    if body.catalog:
        entry = isos.resolve(cfg, body.catalog)
        url, filename, checksum = entry["url"], entry["filename"], entry["checksum"]
        algorithm, verified = entry["algorithm"], entry["verified"]
    # From a URL: a file name is needed, and a checksum only together with its algorithm >w<
    else:
        if not body.url or not body.filename:
            raise ApiError(400, "invalid", "Give a catalog entry, or a URL and a file name.")
        if bool(body.checksum) != bool(body.algorithm):
            raise ApiError(400, "invalid", "Give both a checksum and its algorithm, or neither.")
        url, filename, checksum, algorithm = body.url, body.filename, body.checksum, body.algorithm
        # "verified" records how much we know about the file: checked by checksum, or only fetched over HTTPS OwO
        verified = "checksum" if checksum else "https only"
    # Start the Proxmox download task, remember it, audit it, and return its task id (UPID) ^w^
    upid = isos.download(PVE(cfg), url, filename, checksum, algorithm)
    _remember(s, {"upid": upid, "kind": "download", "filename": filename, "user": s.username,
                  "started": time.time(), "verified": verified})
    s.audit("iso.download", target=filename, detail={"url": url, "verified": verified})
    return {"upid": upid, "filename": filename, "verified": verified}


# GET /api/isos/tasks: progress of the 10 newest remembered tasks UwU
@router.get("/tasks")
def tasks(request: Request, s: Session = Depends(require("viewer"))) -> dict:
    pve = PVE(_cfg(request))
    out = []
    for remembered in _tasks(s)[:10]:
        try:
            # Tasks Proxmox no longer knows are skipped >w<
            task = pve.task(remembered["upid"])
        except ValorError:
            continue
        # A task is done when Proxmox says "stopped"; ok when it exited with "OK"; plus progress and the last log line OwO
        done = task.get("status") == "stopped"
        out.append({**remembered, "running": not done, "ok": done and task.get("exitstatus") == "OK",
                    "exitstatus": task.get("exitstatus"), "progress": 100.0 if done else _progress(task["log"]),
                    "last": (task["log"] or [""])[-1][:200]})
    return {"tasks": out}


# POST /api/isos/upload: the request BODY is the raw ISO; the file name and optional checksum come as URL
# parameters ^w^
@router.post("/upload")
async def upload(request: Request, filename: str = Query(max_length=200),
                 checksum: str | None = Query(default=None, max_length=128, pattern="^[0-9a-fA-F]+$"),
                 algorithm: str | None = Query(default=None, pattern="^(md5|sha1|sha224|sha256|sha384|sha512)$"),
                 s: Session = Depends(require("operator"))) -> dict:
    """The request body is the raw ISO (streamed to disk, hashed on the way, then streamed to Proxmox)."""
    cfg = _cfg(request)
    # Check the file name, and that there's an ISO storage to put it in UwU
    isos.check_filename(filename)
    if not cfg.iso_storage:
        raise ApiError(400, "no_iso_storage", "No ISO storage is configured.")
    # Check the checksum's length fits its algorithm BEFORE receiving gigabytes >w<
    if checksum:                            # before receiving gigabytes: does the checksum fit its algorithm?
        try:
            algorithm = isos.check_checksum(checksum, algorithm)
        except ValorError as e:
            raise ApiError(400, e.code, e.message)
    # Need the file's size + 2 GiB to spare on the VALOR VM's disk, or 507 "Insufficient Storage" OwO
    size = int(request.headers.get("content-length") or 0)
    tmpdir = Path(cfg.state_dir) / UPLOAD_DIR
    tmpdir.mkdir(parents=True, exist_ok=True)
    if size and shutil.disk_usage(tmpdir).free < size + 2 * 2**30:
        raise ApiError(507, "no_space", "Not enough free space in the VALOR VM for this upload.")
    # Two hashes on the way: SHA-256 (Proxmox verifies the import with it) and, if the person's checksum uses another
    # algorithm, that one too ^w^
    h256 = hashlib.sha256()                  # Proxmox gets the SHA-256; the checksum may use another algorithm
    other_hash = hashlib.new(algorithm) if checksum and algorithm != "sha256" else None
    # A temp file in the uploads folder UwU
    fd, path = tempfile.mkstemp(dir=tmpdir, suffix=".iso")
    try:
        with os.fdopen(fd, "wb") as fh:
            # Stream the body to disk chunk by chunk, hashing as we go (never the whole ISO in memory!) >w<
            async for chunk in request.stream():
                fh.write(chunk)
                h256.update(chunk)
                if other_hash is not None:
                    other_hash.update(chunk)
        # Compare with the person's checksum (ignoring upper/lower case) OwO
        if checksum:
            got = (other_hash or h256).hexdigest()
            if got.lower() != checksum.lower():
                raise ApiError(400, "checksum_mismatch", "The uploaded file does not match the checksum you gave.")
        # Hand the file to Proxmox and wait for its import task (up to an hour), in a thread so the server stays responsive ^w^
        pve = PVE(cfg)
        upid = await asyncio.to_thread(pve.upload_iso, cfg.iso_storage, path, filename, h256.hexdigest(), "sha256")
        await asyncio.to_thread(pve.wait_task, upid, f"import {filename}", 3600)
    # Always delete the temp file, even on errors UwU
    finally:
        Path(path).unlink(missing_ok=True)
    # Remember, audit (with the SHA-256 and size) and answer >w<
    _remember(s, {"upid": upid, "kind": "upload", "filename": filename, "user": s.username, "started": time.time(),
                  "verified": f"checksum ({algorithm})" if checksum else "uploaded", "sha256": h256.hexdigest()})
    s.audit("iso.upload", target=filename, detail={"sha256": h256.hexdigest(), "bytes": size})
    return {"ok": True, "filename": filename, "sha256": h256.hexdigest()}


# DELETE /api/isos/<volid>: delete an ISO. Admins only OwO *gently puts the CD away*
@router.delete("/{volid:path}")
def delete(volid: str, request: Request, s: Session = Depends(require("admin"))) -> dict:
    isos.delete(PVE(_cfg(request)), volid)
    s.audit("iso.delete", target=volid)
    return {"ok": True}
