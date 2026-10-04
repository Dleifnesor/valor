"""Range logins, display, power and snapshots - against a fake Proxmox (no cluster needed)."""

import json

import pytest

from valor import credentials, lifecycle
from valor.cluster import render_description
from valor.config import Config
from valor.crypto import Box
from valor.errors import ValorError
from valor.plan import _classify, desired_state


@pytest.fixture
def kcfg(cfg, tmp_path):
    key = tmp_path / "secret.key"
    Box.create_key_file(key)
    return Config(**{**cfg.__dict__, "secret_key_file": str(key)})


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


def test_logins_disabled_without_a_key(cfg):
    assert credentials.enabled(cfg) is False and credentials.ensure(cfg, "x") is None
    assert credentials.version(cfg, "x") == 0


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
