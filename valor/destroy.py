"""Teardown (FR-11): removes only VMs in the engine's pool tagged with the named range."""

from __future__ import annotations

import time

from .cluster import range_vms
from .errors import ValorError
from .pve import PVE


def destroy(pve: PVE, range_name: str, emit=lambda *a, **k: None) -> dict:
    t0 = time.time()
    vms = range_vms(pve, range_name, with_config=False)
    removed, done = [], []
    for vm in vms:
        if f"valor-range-{range_name}" not in vm.tags:      # defence in depth: never touch anything else
            continue
        emit("step_start", step="delete VM", host=vm.name)
        try:
            pve.destroy(vm.vmid)
        except ValorError as e:
            e.completed_steps = done
            raise
        done.append(f"delete VM [{vm.name}]")
        removed.append({"vmid": vm.vmid, "name": vm.name})
        emit("step_done", step="delete VM", host=vm.name)
    return {"range": range_name, "removed": removed, "seconds": round(time.time() - t0, 1)}
