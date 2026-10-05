"""The build sequence of apply(), run against fakes: every host converges, in the right order, with the router's
build egress open only while hosts converge (a regression once skipped convergence for ranges without ISO installs)."""

import pytest

from valor import apply as A
from valor.spec import normalize, parse_spec

SPEC = """
name: flow
segments:
  - {name: dmz, vlan: 690, cidr: 10.70.0.0/24, internet: true}
  - {name: lan, vlan: 691, cidr: 10.71.0.0/24}
hosts:
  - {name: web, segment: dmz, address: 10.70.0.10, roles: [{name: nginx}]}
  - {name: pc, segment: lan, address: 10.71.0.10}
policy:
  - {from: pc, to: web, proto: tcp, ports: [80]}
"""


class Res:
    ok, exitcode, out, err = True, 0, "", ""

    def tail(self):
        return ""


class FakePVE:
    def __init__(self, cfg):
        self.cfg = cfg

    def wait_agent(self, vmid, timeout=300):
        pass

    def exec(self, vmid, cmd, timeout=900, input_data=None):
        return Res()

    def vm_status(self, vmid):
        return {"status": "running"}


@pytest.fixture
def run(cfg, monkeypatch):
    log: list[tuple] = []
    spec = normalize(parse_spec(SPEC), "ubuntu-24.04")
    monkeypatch.setattr(A, "validate_cluster", lambda pve, s: {"ok": True, "errors": []})
    monkeypatch.setattr(A, "template_ids", lambda pve: {"ubuntu-24.04": 9000})
    monkeypatch.setattr(A.credentials, "ensure", lambda cfg, name: None)

    def plan(pve, s, desired):
        acts = [{"host": d.host, "action": "create", "vmid": None, "reasons": ["new VM"]} for d in desired]
        return {"actions": acts, "changes": True, "summary": {"create": len(acts)}, "node": {"within_limit": True}}
    monkeypatch.setattr(A, "make_plan", plan)
    ids = iter(range(100, 200))
    monkeypatch.setattr(A, "create_vm", lambda pve, s, d, taken, mac=None: log.append(("create", d.host)) or next(ids))
    monkeypatch.setattr(A, "router_interfaces", lambda pve, vmid, n, s: ({"dmz": "eth1", "lan": "eth2"}, "eth0"))
    monkeypatch.setattr(A, "load_router_policy", lambda pve, s, vmid, ifmap, up, build_egress: log.append(
        ("policy", "build" if build_egress else "final")))
    monkeypatch.setattr(A, "set_login", lambda pve, d, vmid, login, steps: log.append(("login", d.host)))
    monkeypatch.setattr(A, "run_roles", lambda pve, s, d, vmid, steps, login=None: log.append(("roles", d.host)) or [])
    monkeypatch.setattr(A, "run_baseline", lambda pve, s, d, vmid, steps: log.append(("baseline", d.host)) or [])
    monkeypatch.setattr(A, "stamp", lambda pve, s, d, vmid, conv: log.append(("stamp", d.host)))
    monkeypatch.setattr(A, "configure_wireguard", lambda pve, s, vmid, up, steps: None)
    monkeypatch.setattr(A, "range_vms", lambda pve, name: [])
    return lambda: (A.apply(FakePVE(cfg), spec), log)


def test_every_host_converges_inside_the_build_window(run):
    result, log = run()
    assert result["changed"] is True
    for host in ("web", "pc"):
        assert ("roles", host) in log and ("baseline", host) in log and ("login", host) in log and ("stamp", host) in log
    build, final = log.index(("policy", "build")), log.index(("policy", "final"))
    host_steps = [i for i, e in enumerate(log) if e[0] in ("roles", "baseline") and e[1] != "rtr"]
    assert build < min(host_steps) and max(host_steps) < final       # egress open only while hosts converge
    assert log.count(("policy", "build")) == 1 and log[-1] == ("stamp", "rtr")


def test_a_failing_host_closes_the_build_window(run, monkeypatch):
    policies = []
    monkeypatch.setattr(A, "load_router_policy", lambda pve, s, vmid, ifmap, up, build_egress: policies.append(
        "build" if build_egress else "final"))

    def boom(*a, **k):
        raise A.ValorError("role_failed", "nginx failed")
    monkeypatch.setattr(A, "run_roles", boom)
    with pytest.raises(A.ValorError):
        run()
    assert policies == ["build", "final"]                             # never left open after a failure


def test_mac_from_net_config():
    assert A._mac("virtio=BC:24:11:AA:BB:CC,bridge=vmbr0,firewall=0") == "BC:24:11:AA:BB:CC"
    assert A._mac("virtio,bridge=vmbr0") is None and A._mac("") is None


def test_disk_size_from_config():
    assert A._disk_gib("valor-tank:base-5901-disk-1/vm-5024-disk-1,discard=on,size=64G,ssd=1") == 64   # once crashed
    assert A._disk_gib("local-lvm:vm-1-disk-0,size=10G") == 10 and A._disk_gib("x:vm-1-disk-0,size=1T") == 1024
    assert A._disk_gib("x:vm-1-disk-0,size=512M") == 0.5 and A._disk_gib("x:vm-1-disk-0") == 0 and A._disk_gib("") == 0
