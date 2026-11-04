# *puts on a tiny doctor's coat* OwO valor/web/status.py checks VALOR's health! ^w^
# It powers the Health page: can VALOR reach Proxmox? Are the OS templates built? Is the range network bridge
# VLAN-aware? Is the TLS certificate about to expire? Is the disk full? Are there pending security updates?
# Each answer becomes a "check" with status ok / problem / unknown *wags tail*
"""Health page data: can VALOR do its job right now?"""

# Lazy type hints UwU
from __future__ import annotations

# datetime for the certificate's expiry date; os/shutil for disk space and the host name; subprocess runs
# Ubuntu's update checker; time for caching; Path reads the certificate file >w<
import datetime as dt
import os
import shutil
import subprocess
import time
from pathlib import Path

# FastAPI pieces: the router, Depends (to require a signed-in viewer) and Request (to reach the app's config) OwO
from fastapi import APIRouter, Depends, Request

# __version__ (shown on the page) and jobs (to count queued/running jobs); cluster_info gathers facts about the
# Proxmox node; ValorError/PVE talk to Proxmox; Session/require do the sign-in and role check ^w^
from .. import __version__, jobs
from ..cluster import cluster_info
from ..errors import ValorError
from ..pve import PVE
from .core import Session, require

# All routes under /api, grouped as "status". _updates_cache remembers the last update check and when it ran
# (t = unix time), because asking apt is slow UwU
router = APIRouter(prefix="/api", tags=["status"])
_updates_cache: dict = {"t": 0.0, "value": None}


# _check: build one check entry. ok=True -> "ok", ok=None -> "unknown", ok=False -> "problem". Extra keyword
# arguments (like a hint) are added to the entry as they are >w<
def _check(name: str, ok: bool | None, detail: str, **extra) -> dict:
    return {"name": name, "status": "ok" if ok else ("unknown" if ok is None else "problem"), "detail": detail, **extra}


# _cert: read the HTTPS certificate and report when it expires, how many days are left, which names it covers
# and who issued it. Any problem reading it = None (the page just leaves the certificate check out) OwO
def _cert(path: str) -> dict | None:
    try:
        # Imported here, not at the top, so this module loads even where `cryptography` is missing ^w^
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(Path(path).read_bytes())
        # The expiry time (in UTC) and the whole days left until then *counts on toe beans*
        not_after = cert.not_valid_after_utc
        days = (not_after - dt.datetime.now(dt.timezone.utc)).days
        try:
            # The Subject Alternative Names = the host names/addresses the certificate is valid for; a cert without that
            # extension just lists none UwU
            alt_names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            names = [str(name.value) for name in alt_names]
        except x509.ExtensionNotFound:
            names = []
        return {"expires": not_after.isoformat(), "days_left": days, "names": names,
                "issuer": cert.issuer.rfc4514_string()}
    except Exception:
        return None


# _updates: how many OS updates are waiting (and how many are security fixes), using Ubuntu's apt-check tool.
# Cached for an hour (3600 s) because apt-check is slow >w<
def _updates() -> dict | None:
    """Pending OS updates (Ubuntu's apt-check), cached for an hour."""
    # Checked less than an hour ago? Reuse that answer ^w^
    if time.time() - _updates_cache["t"] < 3600:
        return _updates_cache["value"]
    value = None
    tool = "/usr/lib/update-notifier/apt-check"
    # Only Ubuntu has this tool; elsewhere the answer is None ("unknown") OwO
    if os.path.exists(tool):
        try:
            # apt-check writes "total;security" (e.g. "12;3") to stderr; split and convert to numbers. If anything fails
            # (timeout, odd output), the answer is None UwU
            result = subprocess.run([tool], capture_output=True, text=True, timeout=60)
            total, security = (result.stderr.strip() or "0;0").split(";")[:2]       # apt-check prints "12;3"
            value = {"total": int(total), "security": int(security)}
        except Exception:
            value = None
    # Remember the answer and when we got it *tucks it into the cache*
    _updates_cache.update(t=time.time(), value=value)
    return value


# GET /api/health: a tiny "I'm alive" answer that needs no sign-in, for load balancers and monitoring. It says
# nothing else on purpose, so it leaks no details to strangers >w<
@router.get("/health")
def health() -> dict:
    """Unauthenticated liveness check (no details)."""
    return {"ok": True}


# GET /api/status: the full health report (any signed-in viewer may see it) ^w^
@router.get("/status")
def status(request: Request, s: Session = Depends(require("viewer"))) -> dict:
    # The engine config and the web config, set on the app at startup OwO
    cfg, wcfg = request.app.state.cfg, request.app.state.wcfg
    # checks = the list shown on the page; info = facts about the cluster (stays empty if Proxmox can't be reached) UwU
    checks, info = [], {}
    try:
        # Check 1: can we talk to the Proxmox API at all? Ask for its version *pokes the API*
        pve = PVE(cfg)
        version = pve.version()
        checks.append(_check("Proxmox API", True, f"Proxmox VE {version.get('version')} at {cfg.api_host}"))
        # Gather the cluster facts (node, templates, bridges, ...) for the checks below >w<
        info = cluster_info(pve)
        # Check 2: OS templates. It's a problem if the DEFAULT OS template isn't built; the detail lists missing ones ^w^
        built = info["templates"]
        missing = [name for name, template in built.items() if not template["present"]]
        checks.append(_check("Templates", built.get(cfg.default_os, {}).get("present", False),
                             f"{len(built) - len(missing)} of {len(built)} built"
                             + (f"; missing: {', '.join(missing)}" if missing else "")))
        # Check 3: the range network bridge must exist AND be VLAN-aware (every segment is a VLAN on it) OwO
        bridge = info["segment_bridge"]
        checks.append(_check("Range network", bridge["present"] and bridge["vlan_aware"],
                             f"{bridge['name']}: {'VLAN-aware' if bridge['vlan_aware'] else 'missing or not VLAN-aware'}"))
        # Check 4: the uplink bridge (where range routers get internet) must exist, with its VLAN shown if it has one UwU
        checks.append(_check("Uplink", info["uplink_bridge"]["present"], f"{cfg.uplink_bridge}"
                             + (f" (VLAN {cfg.uplink_vlan})" if cfg.uplink_vlan else "")))
        # Check 5 (only when the VALOR VM's id is known): least privilege! *ears perk up*
        if wcfg.appliance_vmid:
            # least privilege: the engine's token must not be able to read (or change) VALOR's own VM
            # If reading VALOR's own VM config SUCCEEDS, the API token has too many rights: that's a problem >w<
            try:
                pve.vm_config(wcfg.appliance_vmid)
                checks.append(_check("Token scope", False, "the API token can read the VALOR VM - it should not"))
            # A "403 Forbidden" error is what we WANT (the token can't see VALOR itself); any other error is shown as is ^w^
            except ValorError as e:
                fine = "403" in e.message
                checks.append(_check("Token scope", fine, "the API token cannot touch the VALOR VM itself"
                                     if fine else e.message))
    # Proxmox couldn't be reached at all: one failed check with the error and its hint OwO
    except ValorError as e:
        checks.append(_check("Proxmox API", False, e.message, hint=e.hint))
    # Check 6: the certificate. It's a problem when 14 days or fewer are left UwU
    cert = _cert(wcfg.tls_cert)
    if cert:
        checks.append(_check("Certificate", cert["days_left"] > 14, f"expires in {cert['days_left']} days", **cert))
    # Check 7: free disk space where VALOR keeps its state (or / if that folder doesn't exist yet); problem below 10% >w<
    usage = shutil.disk_usage(cfg.state_dir if os.path.exists(cfg.state_dir) else "/")
    free_pct = round(usage.free / usage.total * 100)
    checks.append(_check("VALOR VM disk", free_pct > 10, f"{free_pct}% free"))
    # Check 8: OS updates; pending SECURITY updates are a problem (they get installed automatically overnight) ^w^
    updates = _updates()
    if updates is not None:
        checks.append(_check("OS updates", updates["security"] == 0,
                             f"{updates['total']} pending, {updates['security']} security"
                             + (" (installed automatically overnight)" if updates["security"] else "")))
    # Count queued and running jobs among the 50 newest (True counts as 1 when summed) OwO
    recent = jobs.list_jobs(cfg, 50)
    queue = {"queued": sum(job["state"] == "queued" for job in recent),
             "running": sum(job["state"] == "running" for job in recent)}
    # Which cluster facts go to the page (only these keys, nothing extra) UwU
    cluster_fields = ("node", "cpu_threads", "memory_total_mib", "memory_free_mib", "storage", "segment_bridge",
                      "uplink_bridge", "reserved_networks", "templates", "ranges", "default_os")
    # The report: version, instance name, the VALOR VM's host name, the checks, the job counts and the cluster facts
    # (None when Proxmox couldn't be reached) *wags tail happily*
    return {"version": __version__, "instance": wcfg.instance_name, "hostname": os.uname().nodename,
            "checks": checks, "jobs": queue,
            "cluster": {key: info.get(key) for key in cluster_fields} if info else None}
