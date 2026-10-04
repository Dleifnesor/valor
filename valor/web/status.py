"""Health page data: can VALOR do its job right now?"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from .. import __version__, jobs
from ..cluster import cluster_info
from ..errors import ValorError
from ..pve import PVE
from .core import Session, require

router = APIRouter(prefix="/api", tags=["status"])
_updates_cache: dict = {"t": 0.0, "value": None}


def _check(name: str, ok: bool | None, detail: str, **extra) -> dict:
    return {"name": name, "status": "ok" if ok else ("unknown" if ok is None else "problem"), "detail": detail, **extra}


def _cert(path: str) -> dict | None:
    try:
        from cryptography import x509
        c = x509.load_pem_x509_certificate(Path(path).read_bytes())
        not_after = c.not_valid_after_utc
        days = (not_after - dt.datetime.now(dt.timezone.utc)).days
        try:
            san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            names = [str(n.value) for n in san]
        except x509.ExtensionNotFound:
            names = []
        return {"expires": not_after.isoformat(), "days_left": days, "names": names,
                "issuer": c.issuer.rfc4514_string()}
    except Exception:
        return None


def _updates() -> dict | None:
    """Pending OS updates (Ubuntu's apt-check), cached for an hour."""
    if time.time() - _updates_cache["t"] < 3600:
        return _updates_cache["value"]
    value = None
    tool = "/usr/lib/update-notifier/apt-check"
    if os.path.exists(tool):
        try:
            res = subprocess.run([tool], capture_output=True, text=True, timeout=60)
            total, security = (res.stderr.strip() or "0;0").split(";")[:2]
            value = {"total": int(total), "security": int(security)}
        except Exception:
            value = None
    _updates_cache.update(t=time.time(), value=value)
    return value


@router.get("/health")
def health() -> dict:
    """Unauthenticated liveness check (no details)."""
    return {"ok": True}


@router.get("/status")
def status(request: Request, s: Session = Depends(require("viewer"))) -> dict:
    cfg, wcfg = request.app.state.cfg, request.app.state.wcfg
    checks, info = [], {}
    try:
        pve = PVE(cfg)
        ver = pve.version()
        checks.append(_check("Proxmox API", True, f"Proxmox VE {ver.get('version')} at {cfg.api_host}"))
        info = cluster_info(pve)
        tpl = info["templates"]
        missing = [k for k, v in tpl.items() if not v["present"]]
        checks.append(_check("Templates", tpl.get(cfg.default_os, {}).get("present", False),
                             f"{len(tpl) - len(missing)} of {len(tpl)} built"
                             + (f"; missing: {', '.join(missing)}" if missing else "")))
        seg = info["segment_bridge"]
        checks.append(_check("Range network", seg["present"] and seg["vlan_aware"],
                             f"{seg['name']}: {'VLAN-aware' if seg['vlan_aware'] else 'missing or not VLAN-aware'}"))
        checks.append(_check("Uplink", info["uplink_bridge"]["present"], f"{cfg.uplink_bridge}"
                             + (f" (VLAN {cfg.uplink_vlan})" if cfg.uplink_vlan else "")))
        if wcfg.appliance_vmid:
            try:
                pve.vm_config(wcfg.appliance_vmid)
                checks.append(_check("Token scope", False, "the API token can read the VALOR VM - it should not"))
            except ValorError as e:
                fine = "403" in e.message
                checks.append(_check("Token scope", fine, "the API token cannot touch the VALOR VM itself"
                                     if fine else e.message))
    except ValorError as e:
        checks.append(_check("Proxmox API", False, e.message, hint=e.hint))
    cert = _cert(wcfg.tls_cert)
    if cert:
        checks.append(_check("Certificate", cert["days_left"] > 14, f"expires in {cert['days_left']} days", **cert))
    usage = shutil.disk_usage(cfg.state_dir if os.path.exists(cfg.state_dir) else "/")
    free_pct = round(usage.free / usage.total * 100)
    checks.append(_check("VALOR VM disk", free_pct > 10, f"{free_pct}% free"))
    upd = _updates()
    if upd is not None:
        checks.append(_check("OS updates", upd["security"] == 0,
                             f"{upd['total']} pending, {upd['security']} security"
                             + (" (installed automatically overnight)" if upd["security"] else "")))
    recent = jobs.list_jobs(cfg, 50)
    queue = {"queued": sum(j["state"] == "queued" for j in recent),
             "running": sum(j["state"] == "running" for j in recent)}
    return {"version": __version__, "instance": wcfg.instance_name, "hostname": os.uname().nodename,
            "checks": checks, "jobs": queue,
            "cluster": {k: info.get(k) for k in ("node", "cpu_threads", "memory_total_mib", "memory_free_mib",
                                                  "storage", "segment_bridge", "uplink_bridge", "reserved_networks",
                                                  "templates", "ranges", "default_os")} if info else None}
