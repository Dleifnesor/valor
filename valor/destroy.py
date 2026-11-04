# *ears flatten carefully* OwO this is valor/destroy.py, the file that tears a range down >w<
# Destroying is the scariest thing VALOR does, so this file is extra careful: it only deletes VMs that are in
# VALOR's own Proxmox pool AND carry the `valor` tag AND carry this range's tag. Anything else on the cluster is
# never touched. The range's spec, journal and history are kept, so you can build it again later ^w^ *wags tail*
"""Teardown (FR-11): removes only VMs in the engine's pool tagged with the named range.

Nothing else is ever deleted: range_vms only returns VMs in VALOR's pool with the `valor` tag, and each VM is
checked for the range's own tag once more before it goes. The range's spec, journal and history stay, so the
range can be built again; the web UI deletes those separately.
"""

# Lazy type hints UwU
from __future__ import annotations

# time measures how long the teardown took OwO
import time

# range_vms lists the range's VMs (already filtered to VALOR's pool + valor tag); ValorError is the error type;
# PVE is the Proxmox API client *nuzzles*
from .cluster import range_vms
from .errors import ValorError
from .pve import PVE


# destroy: delete every VM of `range_name` and clean up leftover kickstart CDs.
# `emit` reports progress events (step_start / step_done) to whoever is listening (the job log, the UI). The
# default is a do-nothing lambda, so callers that don't care about progress can skip it ^w^
# Returns a summary dict: which VMs were removed, which CDs were cleaned, and how many seconds it took.
def destroy(pve: PVE, range_name: str, emit=lambda *a, **k: None) -> dict:
    started = time.time()
    # Ask Proxmox for this range's VMs. with_config=False skips loading each VM's full config (faster; destroy only
    # needs the id, name and tags) >w<
    vms = range_vms(pve, range_name, with_config=False)
    # removed = VMs deleted (for the result); done = finished step names (for error reports) UwU
    removed, done = [], []
    for vm in vms:
        # Defence in depth *guards with fluffy tail*: even though range_vms already filtered, check the range's own tag
        # once more right before deleting. If something ever slipped through, it gets skipped instead of destroyed.
        if f"valor-range-{range_name}" not in vm.tags:      # defence in depth: never touch anything else
            continue
        # Tell listeners we're starting to delete this VM OwO
        emit("step_start", step="delete VM", host=vm.name)
        try:
            # pve.destroy stops the VM if needed and deletes it with its disks ^w^
            pve.destroy(vm.vmid)
        # If a delete fails, attach the list of what DID finish to the error before passing it up, so the person sees
        # exactly how far the teardown got >w<
        except ValorError as e:
            e.completed_steps = done
            raise
        # Record the success in both lists, then report the step as done *happy tail wags*
        done.append(f"delete VM [{vm.name}]")
        removed.append({"vmid": vm.vmid, "name": vm.name})
        emit("step_done", step="delete VM", host=vm.name)
    # After the VMs, clean up kickstart CDs left in the ISO library by interrupted installs UwU
    leftovers = _kickstart_cds(pve, range_name)
    # The summary; seconds are rounded to one decimal place to keep it tidy ^w^
    return {"range": range_name, "removed": removed, "kickstart_cds_removed": leftovers,
            "seconds": round(time.time() - started, 1)}


# _kickstart_cds: remove leftover "valor-ks-<range>-..." ISO files. Kickstart CDs are small generated ISOs that
# drive an unattended OS install; if an install was interrupted they can stay behind. They hold no secrets, so
# this is just tidying up OwO *sweeps with tail*
def _kickstart_cds(pve: PVE, range_name: str) -> list[str]:
    """Kickstart CDs an interrupted ISO install left in the ISO library (they hold no secrets)."""
    # Which Proxmox storage holds ISOs (from the engine config) >w<
    storage = pve.cfg.iso_storage
    out = []
    # No ISO storage configured = nothing to clean ^w^
    if not storage:
        return out
    # List the ISO files in that storage. If listing fails (storage offline...), just give up quietly: cleaning up
    # is a nice-to-have, never a reason to fail the destroy UwU
    try:
        items = pve.storage_content(storage, "iso")
    except ValorError:
        return out
    # A volid looks like "storage:iso/name.iso"; split off everything before the first "/" and check the file name
    # starts with this range's kickstart prefix *sniffs each file*
    for item in items:
        if item["volid"].split("/", 1)[-1].startswith(f"valor-ks-{range_name}-"):
            # Delete it and remember it for the summary; a failed delete is ignored for the same reason as above OwO
            try:
                pve.delete_volume(storage, item["volid"])
                out.append(item["volid"])
            except ValorError:
                pass
    return out
