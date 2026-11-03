"""Range lifecycle: power, snapshots and reset - always for the VMs of one range only.

Every action works on the range as a whole (or a few of its hosts) and keeps an order that keeps the range usable:
the router starts first and stops last, because the hosts need their gateway. Snapshots are disk-only (no RAM),
taken on every VM under the same name; `valor-clean` is taken automatically after each verified build, so a range
can always be reset to the state it had when it was last known good.
"""

from __future__ import annotations

import re
import time

from .cluster import VMState, range_vms
from .errors import ValorError
from .pve import PVE
from .spec import ROUTER

CLEAN = "valor-clean"                        # taken automatically after every successful, verified build
SNAP_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{1,39}$")
POWER_ACTIONS = ("start", "shutdown", "stop", "reboot")


def _vms(pve: PVE, range_name: str, hosts: list[str] | None = None) -> list[VMState]:
    """The range's VMs (only those VALOR stamped), or the named hosts among them."""
    vms = [vm for vm in range_vms(pve, range_name) if vm.host and f"valor-range-{range_name}" in vm.tags]
    if hosts:
        unknown = set(hosts) - {vm.host for vm in vms}
        if unknown:
            raise ValorError("unknown_host", f"no VM for {sorted(unknown)} in range {range_name}")
        vms = [vm for vm in vms if vm.host in hosts]
    if not vms:
        raise ValorError("range_not_built", f"range {range_name} has no VMs")
    return vms


def _ordered(vms: list[VMState], router_first: bool) -> list[VMState]:
    """The router comes up first (hosts need their gateway) and goes down last."""
    router = [vm for vm in vms if vm.host == ROUTER]
    hosts = [vm for vm in vms if vm.host != ROUTER]
    return router + hosts if router_first else hosts + router


def power(pve: PVE, range_name: str, action: str, hosts: list[str] | None = None, emit=lambda *a, **k: None) -> dict:
    if action not in POWER_ACTIONS:
        raise ValorError("invalid_action", f"power action must be one of {POWER_ACTIONS}")
    started = time.time()
    done = []
    for vm in _ordered(_vms(pve, range_name, hosts), router_first=action in ("start", "reboot")):
        status = pve.vm_status(vm.vmid).get("status")
        if (action == "start" and status == "running") or (action in ("shutdown", "stop") and status == "stopped"):
            done.append({"host": vm.host, "vmid": vm.vmid, "result": f"already {status}"})
            continue
        emit("step_start", step=action, host=vm.host)
        if action == "shutdown":            # clean shutdown; forced off after 3 minutes
            pve.power(vm.vmid, "shutdown", wait=240, timeout=180, forceStop=1)
        elif action == "reboot":
            # Not Proxmox's reboot: that fails when the guest ignores ACPI (e.g. still booting). Clean shutdown
            # with a forced fallback, then start (pending hardware changes apply too).
            if status == "running":
                pve.power(vm.vmid, "shutdown", wait=150, timeout=90, forceStop=1)
            pve.power(vm.vmid, "start")
        else:
            pve.power(vm.vmid, action)
        emit("step_done", step=action, host=vm.host)
        done.append({"host": vm.host, "vmid": vm.vmid, "result": "done"})
    return {"range": range_name, "action": action, "vms": done, "seconds": round(time.time() - started, 1)}


def list_snapshots(pve: PVE, range_name: str) -> list[dict]:
    """One entry per snapshot name across the range's VMs, newest first."""
    by_name: dict[str, dict] = {}
    for vm in _vms(pve, range_name):
        for snap in pve.snapshots(vm.vmid):
            entry = by_name.setdefault(snap["name"], {"name": snap["name"], "description": snap.get("description", ""),
                                                      "time": snap.get("snaptime", 0), "hosts": []})
            entry["hosts"].append(vm.host)
            entry["time"] = max(entry["time"], snap.get("snaptime", 0))
    total = len(_vms(pve, range_name))
    out = sorted(by_name.values(), key=lambda entry: entry["time"], reverse=True)
    for entry in out:
        entry["complete"] = len(entry["hosts"]) == total     # a VM added later has no older snapshots
        entry["automatic"] = entry["name"] == CLEAN
    return out


def snapshot(pve: PVE, range_name: str, name: str, description: str = "", replace: bool = False,
             emit=lambda *a, **k: None) -> dict:
    if not SNAP_NAME.match(name):
        raise ValorError("invalid_snapshot_name", "snapshot names: 2-40 letters, digits, - or _, starting with a letter")
    started = time.time()
    vms = _vms(pve, range_name)
    for vm in vms:
        have = {snap["name"] for snap in pve.snapshots(vm.vmid)}
        if name in have:
            if not replace:
                raise ValorError("snapshot_exists", f"snapshot {name} already exists on {vm.host}")
            emit("step_start", step=f"delete old snapshot {name}", host=vm.host)
            pve.delete_snapshot(vm.vmid, name)
            emit("step_done", step=f"delete old snapshot {name}", host=vm.host)
    for vm in vms:
        emit("step_start", step=f"snapshot {name}", host=vm.host)
        pve.snapshot(vm.vmid, name, description)
        emit("step_done", step=f"snapshot {name}", host=vm.host)
    return {"range": range_name, "snapshot": name, "vms": [vm.host for vm in vms],
            "seconds": round(time.time() - started, 1)}


def rollback(pve: PVE, range_name: str, name: str, delete_newer: bool = False, emit=lambda *a, **k: None) -> dict:
    """Roll every VM back to `name` and start it. Some storages (ZFS) can only roll back to the newest snapshot:
    with delete_newer, newer snapshots are deleted first; otherwise the reset stops with a clear error."""
    started = time.time()
    vms = _vms(pve, range_name)
    plans = []                          # (vm, its snapshots newer than the target, newest first)
    for vm in vms:
        snaps = pve.snapshots(vm.vmid)
        target = next((snap for snap in snaps if snap["name"] == name), None)
        if not target:
            raise ValorError("snapshot_missing", f"{vm.host} has no snapshot {name}")
        newer = sorted((snap for snap in snaps if snap.get("snaptime", 0) > target.get("snaptime", 0)),
                       key=lambda snap: snap.get("snaptime", 0), reverse=True)
        plans.append((vm, newer))
    blocked = {vm.host: [snap["name"] for snap in newer] for vm, newer in plans if newer}
    if blocked and not delete_newer:
        raise ValorError("newer_snapshots", f"newer snapshots exist: {blocked}",
                         hint="Storages like ZFS can only roll back to the newest snapshot. Delete the newer "
                              "snapshots, or reset with 'delete newer snapshots'.")
    for vm, newer in _ordered_pairs(plans):
        for snap in newer:
            emit("step_start", step=f"delete newer snapshot {snap['name']}", host=vm.host)
            pve.delete_snapshot(vm.vmid, snap["name"])
            emit("step_done", step=f"delete newer snapshot {snap['name']}", host=vm.host)
        emit("step_start", step=f"roll back to {name}", host=vm.host)
        pve.rollback(vm.vmid, name, start=True)
        emit("step_done", step=f"roll back to {name}", host=vm.host)
    return {"range": range_name, "snapshot": name, "vms": [vm.host for vm, _ in plans],
            "seconds": round(time.time() - started, 1)}


def _ordered_pairs(plans):
    """(vm, ...) pairs with the router's first: it rolls back and starts before the hosts."""
    router = [pair for pair in plans if pair[0].host == ROUTER]
    return router + [pair for pair in plans if pair[0].host != ROUTER]


def delete_snapshot(pve: PVE, range_name: str, name: str, emit=lambda *a, **k: None) -> dict:
    started = time.time()
    hosts = []
    for vm in _vms(pve, range_name):
        if any(snap["name"] == name for snap in pve.snapshots(vm.vmid)):
            emit("step_start", step=f"delete snapshot {name}", host=vm.host)
            pve.delete_snapshot(vm.vmid, name)
            emit("step_done", step=f"delete snapshot {name}", host=vm.host)
            hosts.append(vm.host)
    if not hosts:
        raise ValorError("snapshot_missing", f"no VM of {range_name} has a snapshot {name}")
    return {"range": range_name, "snapshot": name, "vms": hosts, "seconds": round(time.time() - started, 1)}
