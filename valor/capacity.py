# *counts the RAM sticks very carefully* OwO valor/capacity.py decides whether a range FITS on the node! ^w^
# Three numbers matter: what the node really uses now (VMs, ZFS cache, the host itself - but not the page cache,
# which Linux hands back on demand), what VALOR's limit allows (70% by default; an admin can change it in
# Datacenter > Resources), and what the hardware can hold at all (the RAM minus a reserve for the host). The limit
# can never be set above the hardware, and a change that adds no memory is never refused *wags tail*
"""Node memory for builds: what is in use, VALOR's limit, and what the hardware can hold.

    in use     the node's used memory without the page cache (Proxmox's "available"), plus what VALOR's running
               VMs may still claim (memory they were given but have not touched yet: they will)
    limit      a share of the node's RAM that builds may fill: the config's max_memory_fraction (0.7), or what an
               admin saved in Datacenter > Resources (kept in <state_dir>/memory-limit.json, so upgrades keep it)
    hardware   the RAM minus a reserve the Proxmox host keeps for itself; no limit can go above it

A range that adds memory must stay under the limit (and so under the hardware); one that adds none always passes.
"""

# Lazy type hints UwU
from __future__ import annotations

# json/time for the saved limit, Path for its file >w<
import json
import time
from pathlib import Path

# The host's own reserve: 5% of its RAM, at least 2 GiB (kernel, Proxmox services, the QEMU processes' overhead).
# MIN_FRACTION: the lowest limit an admin may set OwO
HOST_RESERVE_SHARE = 0.05
HOST_RESERVE_MIN_MIB = 2048
MIN_FRACTION = 0.10


# limit_file: where an admin's limit is saved (in the state directory: `install.sh --upgrade` rewrites the config
# file but never the state) ^w^
def limit_file(cfg) -> Path:
    return Path(cfg.state_dir) / "memory-limit.json"


# saved_limit: the admin's saved limit ({"fraction", "by", "at"}), or None when none was saved UwU
def saved_limit(cfg) -> dict | None:
    try:
        saved = json.loads(limit_file(cfg).read_text())
        return saved if isinstance(saved.get("fraction"), (int, float)) else None
    except (OSError, ValueError):
        return None


# limit_fraction: the limit in force: the admin's, else the config's >w<
def limit_fraction(cfg) -> float:
    saved = saved_limit(cfg)
    return float(saved["fraction"]) if saved else cfg.max_memory_fraction


# save_limit: save an admin's limit (None = back to the config's default). Written to a temp file and renamed, so
# a reader never sees half a file OwO
def save_limit(cfg, fraction: float | None, by: str) -> None:
    path = limit_file(cfg)
    if fraction is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"fraction": round(fraction, 2), "by": by,
                               "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}))
    tmp.replace(path)


# hardware_fraction: the share of a node's RAM VMs may use at most (all of it minus the host's reserve), rounded
# down to whole percent ^w^
def hardware_fraction(total_mib: int) -> float:
    reserve = max(HOST_RESERVE_MIN_MIB, total_mib * HOST_RESERVE_SHARE)
    return max(0.0, int((total_mib - reserve) / total_mib * 100) / 100) if total_mib else 0.0


# measure: the node's memory now. Proxmox's "available" leaves out the page cache (Linux frees it on demand); ZFS's
# ARC still counts as used, which errs on the safe side. Running VMs that have not yet touched all the memory they
# were given will still claim it ("growth"); for VMs VALOR may see that is counted too UwU
def measure(pve) -> dict:
    memory = pve.node_status()["memory"]
    total = memory["total"] // 2**20
    available = memory.get("available", memory["free"]) // 2**20      # older Proxmox: only "free"
    growth = 0
    for vm in pve.resources():
        if vm.get("node") == pve.cfg.node and vm.get("status") == "running" and vm.get("type", "qemu") == "qemu":
            growth += max(0, int(vm.get("maxmem") or 0) - int(vm.get("memhost", vm.get("mem")) or 0)) // 2**20
    in_use = total - available + growth
    limit = min(limit_fraction(pve.cfg), hardware_fraction(total))
    return {"name": pve.cfg.node, "memory_total_mib": total, "memory_free_mib": available,
            "memory_in_use_mib": in_use, "memory_growth_mib": growth,
            "limit_fraction": limit, "limit_mib": int(total * limit),
            "hardware_fraction": hardware_fraction(total), "hardware_mib": int(total * hardware_fraction(total))}


# judge: does `extra` MiB more fit? Adding nothing always fits: it cannot make anything worse. needed_fraction is
# the limit that would let it through (for the hint) >w<
def judge(node: dict, extra: int) -> dict:
    extra = max(int(extra), 0)
    after = node["memory_in_use_mib"] + extra
    total = node["memory_total_mib"] or 1
    return {**node, "additional_memory_mib": extra,
            "within_limit": extra == 0 or after <= node["limit_mib"],
            "fits_hardware": extra == 0 or after <= node["hardware_mib"],
            "needed_fraction": -(-after * 100 // total) / 100}       # rounded up to whole percent


# refusal: the error message and hint when a range does not fit OwO
def refusal(node: dict) -> tuple[str, str]:
    total, extra = node["memory_total_mib"], node["additional_memory_mib"]
    in_use = node["memory_in_use_mib"]
    percent = lambda fraction: f"{round(fraction * 100)}%"
    message = (f"the range needs {extra} MiB more memory: {node['name']} would use {in_use + extra} of {total} MiB "
               f"({percent((in_use + extra) / total)}); the limit is {percent(node['limit_fraction'])}")
    if node["fits_hardware"]:
        hint = (f"An administrator can raise the memory limit in Datacenter > Resources to "
                f"{percent(node['needed_fraction'])} or more (the hardware allows up to "
                f"{percent(node['hardware_fraction'])}), or make room by shutting down other ranges.")
    else:
        hint = (f"The node's RAM cannot hold this: VMs may use at most {percent(node['hardware_fraction'])} of it. "
                "Shut down or destroy other ranges, or give this range's VMs less memory.")
    return message, hint
