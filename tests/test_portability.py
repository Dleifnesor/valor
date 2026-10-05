"""Engine behaviour that must not depend on the development cluster."""

import ipaddress

import pytest

from valor.cluster import templates
from valor.config import Config
from valor.errors import ValorError
from valor.netpolicy import PRIVATE_V4, private_set, render
from valor.plan import desired_state
from valor.pve import PVE
from valor.web import topology


def test_private_set_unchanged_without_new_networks():
    assert private_set(()) == PRIVATE_V4
    assert private_set(("192.168.1.0/24", "10.10.10.0/24")) == PRIVATE_V4      # already covered


def test_private_set_adds_public_lan_without_overlaps():
    out = private_set(("128.32.0.0/16", "128.32.5.0/24", "fd00::/8"))
    nets = [ipaddress.IPv4Network(n) for n in out]
    assert ipaddress.IPv4Network("128.32.0.0/16") in nets and "fd00::/8" not in out
    for i, a in enumerate(nets):
        for b in nets[i + 1:]:
            assert not a.overlaps(b)


def test_reserved_networks_reach_the_router_policy(cfg, ref_spec):
    rules = render(ref_spec, {"dmz": "eth1", "lan": "eth2"}, "eth0", build_egress=False, spec_id="x",
                   reserved=("128.32.0.0/16",))
    assert "128.32.0.0/16" in rules


def test_unconfigured_engine_refuses_to_run(tmp_path):
    with pytest.raises(ValorError) as e:
        PVE(Config(token_file=str(tmp_path / "t.json")))
    assert e.value.code == "not_configured"


def test_probe_and_uplink_vlan(cfg, ref_spec):
    c = Config(**{**cfg.__dict__, "internet_probe": "9.9.9.9:53", "uplink_vlan": 20})
    assert c.probe == ("9.9.9.9", 53)
    rtr = desired_state(c, ref_spec)[0]
    assert rtr.nics[0] == {"bridge": "vmbr0", "vlan": 20, "ip": "dhcp", "gw": None}


class FakePVE:
    def __init__(self, cfg, resources):
        self.cfg, self._r = cfg, resources

    def resources(self):
        return self._r


def test_templates_found_by_tag_prefer_range_node(cfg):
    res = [
        {"vmid": 9000, "template": 1, "type": "qemu", "node": "pve2", "pool": "valor-templates",
         "tags": "valor-template;os-ubuntu-24-04"},
        {"vmid": 5900, "template": 1, "type": "qemu", "node": "pve1", "pool": "valor-templates",
         "tags": "os-ubuntu-24-04;valor-template"},
        {"vmid": 7000, "template": 1, "type": "qemu", "node": "pve1", "pool": "someone-else",
         "tags": "valor-template;os-debian-13"},
        {"vmid": 7001, "template": 1, "type": "qemu", "node": "pve1", "tags": "valor-template;os-debian-12"},
    ]
    t = templates(FakePVE(cfg, res), {"ubuntu-24.04": {}, "debian-13": {}, "debian-12": {}})
    assert t["ubuntu-24.04"]["vmid"] == 5900          # on the range node
    assert t["debian-13"]["present"] is False         # visibly in another pool
    assert t["debian-12"]["vmid"] == 7001             # pool hidden (no Pool.Audit): visible means allowed


def test_topology_overlay(cfg, ref_spec):
    plan = {"actions": [{"host": "rtr", "action": "keep"}, {"host": "web", "action": "create"},
                        {"host": "db", "action": "converge", "reasons": ["roles changed"]},
                        {"host": "old", "action": "remove", "vmid": 4005}]}
    g = topology.build(ref_spec, {"web": {"vmid": 4001, "status": "running"}}, plan)
    by = {n["id"]: n for n in g["nodes"]}
    assert by["host:web"]["change"] == "create" and by["host:db"]["change"] == "update"
    assert by["host:old"]["change"] == "remove" and by["rtr"]["change"] == "keep"
    assert g["changes"] == {"keep": 1, "create": 1, "update": 1, "remove": 1}
    kinds = {e["kind"] for e in g["edges"]}
    assert {"uplink", "gateway", "policy"} <= kinds
    assert all(e["source"] in by and e["target"] in by for e in g["edges"])


class AgentAPI:
    """Agent requests that fail the way Proxmox reports them (proxmoxer puts the reason in .content)."""

    def __init__(self, failures):
        self.failures, self.calls = list(failures), 0

    def __call__(self, **params):
        self.calls += 1
        if self.failures:
            from proxmoxer.core import ResourceException
            raise ResourceException(500, "Internal Server Error", self.failures.pop(0))
        return {"pid": 7}


@pytest.mark.parametrize("failures, resend, calls, ok", [
    (["QEMU guest agent is not running"] * 2, False, 3, True),     # ping timed out: nothing was sent, retry
    (["VM 5024 qga command 'guest-exec' failed - got timeout"], False, 1, False),   # may have run: never twice
    (["VM 5024 qga command 'guest-file-open' failed - got timeout"], True, 2, True),  # safe to send again
    (["QEMU guest agent is not running"] * 4, True, 4, False),     # gives up after AGENT_RETRIES
    (["can't open file"], True, 1, False),                        # real errors are not retried
])
def test_agent_requests_survive_a_busy_guest(cfg, failures, resend, calls, ok):
    from valor import pve as P
    pve = PVE.__new__(PVE)
    pve.cfg, waits = cfg, []
    pve.wait_agent = lambda vmid, timeout=300: waits.append(vmid)
    api = AgentAPI(failures)
    if ok:
        assert pve.agent_call(5024, "write file in VM 5024", api, resend=resend, file="x") == {"pid": 7}
    else:
        with pytest.raises(ValorError):
            pve.agent_call(5024, "write file in VM 5024", api, resend=resend, file="x")
    assert api.calls == calls and len(waits) == calls - 1 and P.AGENT_RETRIES == 3


class BusyWindows(PVE):
    """ps() against a guest whose agent misses Proxmox's 5 s limit for starting the script, but runs it."""

    def __init__(self, cfg, start_error, pid_after=1):
        self.cfg, self.start_error, self.pid_after = cfg, start_error, pid_after
        self.runs, self.reads, self.deleted, self.followed = [], 0, [], None

    def write_file(self, vmid, path, content):
        self.script = content

    def exec(self, vmid, command, input_data=None, timeout=900):
        if command[0] == "cmd.exe":
            self.deleted = command[-2:]
            return None
        self.runs.append(command)
        raise ValorError("proxmox_api", f"run command in VM {vmid}: HTTP 500", details={"content": self.start_error})

    def file_read(self, vmid, path):
        self.reads += 1
        return "1868\r\n" if self.reads > self.pid_after else None

    def exec_wait(self, vmid, pid, timeout=900):
        self.followed = pid
        from valor.pve import ExecResult
        return ExecResult(0, "VALOR-OK", "")


def test_a_timed_out_script_is_followed_not_started_twice(cfg, monkeypatch):
    from valor import pve as P
    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    w = BusyWindows(cfg, "VM 5024 qga command 'guest-exec' failed - got timeout")
    res = w.ps(5024, "Write-Output 'VALOR-OK'")
    assert res.out == "VALOR-OK" and w.followed == 1868 and len(w.runs) == 1        # never run a second time
    assert w.script.startswith("Set-Content -LiteralPath 'C:\\Windows\\Temp\\valor-") and "$PID" in w.script.split("\r\n")[0]
    assert w.deleted[0].endswith(".ps1") and w.deleted[1].endswith(".ps1.pid")       # both files cleaned up

    other = BusyWindows(cfg, "VM 5024 is not running")
    with pytest.raises(ValorError) as e:
        other.ps(5024, "x")
    assert e.value.code == "proxmox_api" and other.reads == 0                      # other errors: no recovery

    monkeypatch.setattr(P, "PS_START_WAIT", 0)
    lost = BusyWindows(cfg, "got timeout", pid_after=99)
    with pytest.raises(ValorError) as e:
        lost.ps(5024, "x")
    assert e.value.code == "agent_exec_lost" and len(lost.runs) == 1
