# *counts the RAM sticks* OwO tests/test_capacity.py: does a range fit the node's memory? ^w^
# The real use (without the page cache), what running VMs may still claim, the admin's limit (never above what the
# hardware holds), the refusal's hint, and the Datacenter > Resources API *wags tail*
"""Node memory for builds: measuring, the adjustable limit, the hardware ceiling and the settings API."""

from types import SimpleNamespace

import pytest

from valor import capacity

GIB = 2**30


# node: a fake Proxmox node (svr-02 on 2026-11-25: 251.5 GiB, 75 GiB free but 110 GiB available because 36 GiB is
# page cache) with some VMs OwO
def node(cfg, vms=(), total=257583, free=76212, available=112874):
    return SimpleNamespace(cfg=cfg, node_status=lambda: {"memory": {"total": total * 2**20, "free": free * 2**20,
                                                                     "available": available * 2**20}},
                           resources=lambda: list(vms))


def vm(node_name, status, maxmem_mib, memhost_mib, vmtype="qemu"):
    return {"node": node_name, "status": status, "maxmem": maxmem_mib * 2**20, "memhost": memhost_mib * 2**20,
            "mem": 1, "type": vmtype}


# In use = used without the page cache + what running VMs on THIS node may still claim (memhost below maxmem);
# stopped VMs and other nodes don't count ^w^
def test_measure_leaves_out_the_page_cache_and_counts_what_vms_may_still_claim(cfg):
    measured = capacity.measure(node(cfg, [vm("pve1", "running", 4096, 3072), vm("pve1", "running", 1024, 1100),
                                           vm("pve1", "stopped", 8192, 0), vm("other", "running", 8192, 0)]))
    assert measured["memory_growth_mib"] == 1024
    assert measured["memory_in_use_mib"] == 257583 - 112874 + 1024           # not total - free (that has the cache)
    assert measured["limit_fraction"] == 0.7 and measured["hardware_fraction"] == 0.95


# decella 2026-11-25: the node sat over the limit and an apply that added 0 MiB was refused. Adding nothing always
# fits; adding something must stay under the limit, and the hint names the limit that would let it through UwU
def test_a_range_that_adds_no_memory_is_never_refused(cfg):
    full = capacity.measure(node(cfg, available=40000))                       # ~84% in use: over the 70% limit
    assert capacity.judge(full, 0)["within_limit"] and capacity.judge(full, -2048)["within_limit"]
    more = capacity.judge(full, 4096)
    assert not more["within_limit"] and more["fits_hardware"] and more["needed_fraction"] == 0.87
    message, hint = capacity.refusal(more)
    assert "4096 MiB more" in message and "the limit is 70%" in message
    assert "Datacenter > Resources" in hint and "87%" in hint and "95%" in hint
    too_much = capacity.judge(full, 40000)
    assert not too_much["fits_hardware"] and "cannot hold" in capacity.refusal(too_much)[1]


# The admin's limit replaces the default, survives as a file in the state directory, can never exceed the hardware
# (a smaller node, or one changed since) and None goes back to the default >w<
def test_the_saved_limit_is_used_but_never_above_the_hardware(cfg):
    capacity.save_limit(cfg, 0.9, "admin")
    assert capacity.saved_limit(cfg)["by"] == "admin" and capacity.limit_fraction(cfg) == 0.9
    assert capacity.measure(node(cfg))["limit_fraction"] == 0.9
    assert capacity.measure(node(cfg, total=16384, available=8000))["limit_fraction"] == 0.87   # 16 GiB: 2 GiB reserve
    capacity.save_limit(cfg, None, "admin")
    assert capacity.saved_limit(cfg) is None and capacity.limit_fraction(cfg) == 0.7


# The plan counts only memory the range adds (running VMs of the range are already in use) OwO
def test_plan_counts_only_the_memory_the_range_adds(cfg):
    from valor.cluster import VMState
    from valor.plan import _node_memory
    want = [SimpleNamespace(host="rtr"), SimpleNamespace(host="web")]
    running = {"rtr": VMState(1, "x-rtr", "running", [], config={"memory": 1024})}
    full = node(cfg, available=40000)
    assert _node_memory(full, want, running, 1024)["within_limit"]                       # nothing new
    assert not _node_memory(full, want, running, 1024 + 8192)["within_limit"]


# Validation only refuses what the hardware can't hold (the adjustable limit is the plan's job); the range's own
# running VMs are already in use ^w^
def test_validation_refuses_only_what_the_hardware_cannot_hold(cfg, ref_spec):
    from valor.validate import _ClusterChecks
    needed = ref_spec.router.memory + sum(host.memory for host in ref_spec.hosts)

    def checks(pve):
        c = _ClusterChecks.__new__(_ClusterChecks)
        c.pve, c.spec, c.cfg, c.errors, c.warnings = pve, ref_spec, cfg, [], []
        c.pve.storage_status = lambda: {"avail": 100 * GIB}
        c.resources()
        return c.errors
    assert checks(node(cfg, available=40000)) == []                                       # over 70%, under 95%
    tight = 257583 - int(257583 * 0.95) + needed - 1024                                  # leaves 1 GiB too little
    assert checks(node(cfg, available=tight))[0]["location"] == "resources"
    own = [{**vm("pve1", "running", needed, needed), "tags": f"valor;valor-range-{ref_spec.name}"}]
    assert checks(node(cfg, own, available=tight)) == []                                  # already running
