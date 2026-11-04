# *flicks power switches on a pretend cluster* OwO tests/test_lifecycle.py: tests for LOGINS, POWER and SNAPSHOTS
# (summary in the docstring)! ^w^
"""Range logins, display, power and snapshots - against a fake Proxmox (no cluster needed)."""

# SimpleNamespace (for tiny fake objects), pytest, and the modules under test UwU
from types import SimpleNamespace

import pytest
from proxmoxer.core import ResourceException

from valor import credentials, lifecycle
from valor import pve as P
from valor.cluster import render_description
from valor.config import Config
from valor.crypto import Box
from valor.errors import ValorError
from valor.plan import _classify, desired_state
from valor.pve import PVE


# kcfg: a config with a real (temporary) secret key, so logins are enabled >w<
@pytest.fixture
def kcfg(cfg, tmp_path):
    key = tmp_path / "secret.key"
    Box.create_key_file(key)
    return Config(**{**cfg.__dict__, "secret_key_file": str(key)})


# Passwords: 20 easy-to-type characters in 4 groups; stored encrypted (never in plain text); stable; rotation bumps
# the version; forget deletes it OwO
def test_password_format_and_encryption_at_rest(kcfg):
    pw = credentials.generate()
    assert len(pw) == 23 and pw.count("-") == 3 and not set(pw.replace("-", "")) & set("0o1li")
    login = credentials.ensure(kcfg, "web2tier")
    assert login["username"] == "valor" and login["version"] == 1
    raw = (credentials._path(kcfg, "web2tier")).read_text()
    assert login["password"] not in raw and "password_enc" in raw
    assert credentials.ensure(kcfg, "web2tier")["password"] == login["password"]       # stable
    new = credentials.rotate(kcfg, "web2tier")
    assert new["version"] == 2 and new["password"] != login["password"]
    assert credentials.load(kcfg, "web2tier")["password"] == new["password"]
    credentials.forget(kcfg, "web2tier")
    assert credentials.load(kcfg, "web2tier") is None


# No key = no logins (and version 0) ^w^
def test_logins_disabled_without_a_key(cfg):
    assert credentials.enabled(cfg) is False and credentials.ensure(cfg, "x") is None
    assert credentials.version(cfg, "x") == 0


# A different display makes the plan "update" a VM; rotating the password changes every VM's converge fingerprint UwU
def test_display_and_login_version_drive_the_plan(kcfg, ref_spec):
    d = desired_state(kcfg, ref_spec)[1]
    assert d.hw["display"] == "std"
    meta = {"host": d.host, "hw_spec": dict(d.hw), "hw": d.hw_hash, "conv": d.conv_hash, "spec": "S"}
    from valor.cluster import VMState
    old_display = dict(meta, hw_spec={**d.hw, "display": "serial0"})
    action, reasons = _classify(d, VMState(4001, "x", "running", ["valor"], old_display), "S")
    assert action == "update" and any("display" in r for r in reasons)
    credentials.ensure(kcfg, ref_spec.name)
    before = desired_state(kcfg, ref_spec)[1].conv_hash
    credentials.rotate(kcfg, ref_spec.name)
    assert desired_state(kcfg, ref_spec)[1].conv_hash != before                         # rotation re-converges


# FakePVE: just enough of the Proxmox client for lifecycle.py, remembering every call; zfs=True refuses rollbacks
# past newer snapshots, like ZFS >w<
class FakePVE:
    """Just enough of valor.pve.PVE for lifecycle.py."""

    def __init__(self, cfg, hosts=("rtr", "web", "db"), zfs=True):
        self.cfg, self.calls, self.zfs = cfg, [], zfs
        self.vms = {}
        for i, h in enumerate(hosts):
            meta = {"range": "lab", "host": h}
            self.vms[5000 + i] = {"status": "running", "snaps": [], "host": h,
                                  "desc": render_description(meta, "abc")}

    def resources(self):
        return [{"vmid": v, "type": "qemu", "name": f"lab-{x['host']}", "status": x["status"], "pool": self.cfg.pool,
                 "tags": "valor;valor-range-lab"} for v, x in self.vms.items()]

    def vm_config(self, vmid):
        return {"description": self.vms[vmid]["desc"]}

    def vm_status(self, vmid):
        return {"status": self.vms[vmid]["status"]}

    def power(self, vmid, action, wait=300, **params):
        self.calls.append((action, self.vms[vmid]["host"]))
        self.vms[vmid]["status"] = "running" if action in ("start", "reboot") else "stopped"

    def snapshots(self, vmid):
        return list(self.vms[vmid]["snaps"])

    def snapshot(self, vmid, name, description=""):
        t = 1000 + len(self.calls)
        self.calls.append(("snapshot", self.vms[vmid]["host"], name))
        self.vms[vmid]["snaps"].append({"name": name, "description": description, "snaptime": t})

    def delete_snapshot(self, vmid, name):
        self.calls.append(("delsnap", self.vms[vmid]["host"], name))
        self.vms[vmid]["snaps"] = [s for s in self.vms[vmid]["snaps"] if s["name"] != name]

    def rollback(self, vmid, name, start=True):
        snaps = self.vms[vmid]["snaps"]
        target = next(s for s in snaps if s["name"] == name)
        if self.zfs and any(s["snaptime"] > target["snaptime"] for s in snaps):
            raise ValorError("task_failed", "can't rollback, not the most recent snapshot")
        self.calls.append(("rollback", self.vms[vmid]["host"], name))
        self.vms[vmid]["status"] = "running"


# Power order: shutdown = hosts first, router last; start = router first; reboot = shutdown + start; already-running
# VMs are skipped; unknown hosts and actions fail OwO
def test_power_order_router_first_up_last_down(cfg):
    pve = FakePVE(cfg)
    lifecycle.power(pve, "lab", "shutdown")
    assert [c[1] for c in pve.calls] == ["web", "db", "rtr"]
    pve.calls.clear()
    lifecycle.power(pve, "lab", "start")
    assert [c[1] for c in pve.calls] == ["rtr", "web", "db"]
    pve.calls.clear()
    lifecycle.power(pve, "lab", "reboot", hosts=["web"])
    assert pve.calls == [("shutdown", "web"), ("start", "web")]          # robust reboot: shutdown, then start
    pve.calls.clear()
    res = lifecycle.power(pve, "lab", "start", hosts=["web"])
    assert pve.calls == [] and res["vms"][0]["result"] == "already running"
    with pytest.raises(ValorError):
        lifecycle.power(pve, "lab", "start", hosts=["nope"])
    with pytest.raises(ValorError):
        lifecycle.power(pve, "lab", "suspend")


# Snapshots: duplicates refused unless replacing; newest first; a reset blocked by newer snapshots (ZFS) until
# delete_newer, then the router rolls back first; bad names refused; delete ^w^
def test_snapshots_reset_and_zfs_rule(cfg):
    pve = FakePVE(cfg)
    lifecycle.snapshot(pve, "lab", lifecycle.CLEAN, "verified")
    with pytest.raises(ValorError) as e:
        lifecycle.snapshot(pve, "lab", lifecycle.CLEAN)
    assert e.value.code == "snapshot_exists"
    lifecycle.snapshot(pve, "lab", lifecycle.CLEAN, replace=True)          # what every verified build does
    lifecycle.snapshot(pve, "lab", "before-attack")
    snaps = lifecycle.list_snapshots(pve, "lab")
    assert [s["name"] for s in snaps] == ["before-attack", lifecycle.CLEAN] and all(s["complete"] for s in snaps)
    with pytest.raises(ValorError) as e:
        lifecycle.rollback(pve, "lab", lifecycle.CLEAN)
    assert e.value.code == "newer_snapshots" and "before-attack" in e.value.message
    pve.calls.clear()
    lifecycle.rollback(pve, "lab", lifecycle.CLEAN, delete_newer=True)
    assert [c[0] for c in pve.calls].count("delsnap") == 3 and [c for c in pve.calls if c[0] == "rollback"][0][1] == "rtr"
    with pytest.raises(ValorError):
        lifecycle.snapshot(pve, "lab", "bad name!")
    lifecycle.delete_snapshot(pve, "lab", lifecycle.CLEAN)
    assert lifecycle.list_snapshots(pve, "lab") == []


# ---- a VM whose config lock is busy (2026-11-02: a snapshot right after rebooting db) ----

def lock_busy(vmid):
    return f"can't lock file '/var/lock/qemu-server/lock-{vmid}.conf' - got timeout"


class FakeProxmox:
    """The Proxmox API (as proxmoxer exposes it) behind the real PVE class, for range lab: rtr 4000, web 4001, db 4002.
    fail() makes a VM's next tasks fail - by default like Proxmox while its cleanup after a reboot holds the VM's
    config lock; with http=True the API refuses before a task starts."""

    def __init__(self, cfg, snaps=()):
        self.pool = cfg.pool
        self.hosts = {4000: "rtr", 4001: "web", 4002: "db"}
        self.snaps = {vmid: [{"name": name, "snaptime": 1} for name in snaps] for vmid in self.hosts}
        self.started, self.failing, self.task_errors = [], {}, {}
        self.cluster = SimpleNamespace(resources=SimpleNamespace(get=self.resources))

    def fail(self, vmid, times, error=None, http=False):
        self.failing[vmid] = {"times": times, "error": error or lock_busy(vmid), "http": http}

    def resources(self, type):
        return [{"vmid": vmid, "type": "qemu", "name": f"lab-{host}", "status": "running", "pool": self.pool,
                 "tags": "valor;valor-range-lab"} for vmid, host in self.hosts.items()]

    def nodes(self, node):
        return self

    def qemu(self, vmid):
        description = render_description({"range": "lab", "host": self.hosts[vmid]}, "abc")
        return SimpleNamespace(config=SimpleNamespace(get=lambda: {"description": description}),
                               snapshot=FakeSnapshots(self, vmid))

    def task(self, action, vmid, do):
        """Starts a task and returns its ID (or raises like the API when it refuses one)."""
        self.started.append((action, self.hosts[vmid]))
        upid = f"UPID:pve1:{len(self.started)}:{action}:{vmid}"
        failing = self.failing.get(vmid)
        if failing and failing["times"]:
            failing["times"] -= 1
            if failing["http"]:
                raise ResourceException(500, "Internal Server Error", failing["error"])
            self.task_errors[upid] = failing["error"]
        elif do:
            do()
        return upid

    def tasks(self, upid):
        error = self.task_errors.get(upid)
        log = [{"t": f"TASK ERROR: {error}"}] if error else [{"t": "TASK OK"}]
        if error and "can't lock" in error:
            log.insert(0, {"t": "trying to acquire lock..."})
        return SimpleNamespace(status=SimpleNamespace(get=lambda: {"status": "stopped", "exitstatus": error or "OK"}),
                               log=SimpleNamespace(get=lambda limit=500: log))


class FakeSnapshots:
    """qemu(vmid).snapshot: get() and post() list and take snapshots; snapshot(name) has rollback.post and delete."""

    def __init__(self, proxmox, vmid):
        self.proxmox, self.vmid, self.snaps = proxmox, vmid, proxmox.snaps[vmid]

    def get(self):
        return [*self.snaps, {"name": "current"}]

    def post(self, snapname, description="", vmstate=0):
        return self.proxmox.task("snapshot", self.vmid, lambda: self.snaps.append({"name": snapname, "snaptime": 2}))

    def __call__(self, name):
        def delete():
            self.snaps[:] = [snap for snap in self.snaps if snap["name"] != name]
        rollback = SimpleNamespace(post=lambda start: self.proxmox.task("rollback", self.vmid, None))
        return SimpleNamespace(rollback=rollback, delete=lambda: self.proxmox.task("delete", self.vmid, delete))


def fake_pve(cfg, monkeypatch, snaps=()):
    """The real PVE class against FakeProxmox; the pauses between attempts are recorded instead of slept."""
    proxmox = FakeProxmox(cfg, snaps)
    pve = PVE.__new__(PVE)
    pve.cfg, pve.node, pve.api = cfg, cfg.node, proxmox
    pauses = []
    monkeypatch.setattr(P.time, "sleep", pauses.append)
    return pve, proxmox, pauses


RANGE_OPERATIONS = {                    # snapshots the range has before, and the job
    "snapshot": ((), lambda pve: lifecycle.snapshot(pve, "lab", "rdsnap")),
    "rollback": (("rdsnap",), lambda pve: lifecycle.rollback(pve, "lab", "rdsnap")),
    "delete": (("rdsnap",), lambda pve: lifecycle.delete_snapshot(pve, "lab", "rdsnap")),
}


@pytest.mark.parametrize("http", [False, True], ids=["task", "api"])
@pytest.mark.parametrize("operation", RANGE_OPERATIONS)
def test_snapshot_jobs_wait_out_a_busy_vm_lock(cfg, monkeypatch, operation, http):
    """2026-11-02: a range snapshot right after rebooting db failed on db's config lock, which Proxmox's cleanup of the
    stopped QEMU process still held, and the reset to that snapshot then failed with snapshot_missing. Now the task is
    started again after a pause and the job covers every VM."""
    snaps, run = RANGE_OPERATIONS[operation]
    pve, proxmox, pauses = fake_pve(cfg, monkeypatch, snaps)
    proxmox.fail(4002, times=1, http=http)
    assert run(pve)["vms"] == ["rtr", "web", "db"]
    assert proxmox.started == [(operation, "rtr"), (operation, "web"), (operation, "db"), (operation, "db")]
    assert pauses == [P.VM_LOCK_PAUSE]


def test_a_lock_that_stays_busy_fails_the_snapshot_as_before(cfg, monkeypatch):
    """After the last attempt the job fails with Proxmox's error word for word, and the range keeps the partial
    snapshot as before: the list marks it incomplete and a reset to it stops before touching a VM."""
    pve, proxmox, pauses = fake_pve(cfg, monkeypatch)
    proxmox.fail(4002, times=99)
    with pytest.raises(ValorError) as e:
        lifecycle.snapshot(pve, "lab", "rdsnap")
    assert e.value.code == "task_failed"
    assert e.value.message == f"snapshot rdsnap of VM 4002 failed: {lock_busy(4002)}"
    assert e.value.details == ["trying to acquire lock...", f"TASK ERROR: {lock_busy(4002)}"]
    assert proxmox.started.count(("snapshot", "db")) == P.VM_LOCK_RETRIES + 1 and len(pauses) == P.VM_LOCK_RETRIES
    [snap] = lifecycle.list_snapshots(pve, "lab")
    assert snap["hosts"] == ["rtr", "web"] and not snap["complete"]
    proxmox.started.clear()
    with pytest.raises(ValorError) as e:
        lifecycle.rollback(pve, "lab", "rdsnap")
    assert e.value.code == "snapshot_missing" and e.value.message == "db has no snapshot rdsnap"
    assert proxmox.started == []


@pytest.mark.parametrize("error, http, code", [
    ("snapshot feature is not available", False, "task_failed"),
    ("VM 4002 qga command 'guest-fsfreeze-freeze' failed - got timeout", False, "task_failed"),  # not the lock
    ("Permission check failed (/vms/4002, VM.Snapshot)", True, "proxmox_api"),
])
def test_other_snapshot_errors_are_not_retried(cfg, monkeypatch, error, http, code):
    pve, proxmox, pauses = fake_pve(cfg, monkeypatch)
    proxmox.fail(4002, times=1, error=error, http=http)
    with pytest.raises(ValorError) as e:
        lifecycle.snapshot(pve, "lab", "rdsnap")
    assert e.value.code == code and proxmox.started.count(("snapshot", "db")) == 1 and pauses == []
    if not http:
        assert e.value.message == f"snapshot rdsnap of VM 4002 failed: {error}"
