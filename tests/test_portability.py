# *perks ears up* OwO tests/test_portability.py: VALOR must work on ANY Proxmox cluster, not just the one it was
# built on! These tests check the engine never quietly depends on our dev cluster's addresses or quirks ^w^
"""Engine behaviour that must not depend on the development cluster."""

# ipaddress for real network maths, pytest, and the engine pieces under test UwU *wags tail*
import ipaddress

import pytest

from valor.cluster import templates
from valor.config import Config
from valor.errors import ValorError
from valor.netpolicy import PRIVATE_V4, private_set, render
from valor.plan import desired_state
from valor.pve import PVE
from valor.web import topology


# *sniffs the private ranges* With no extra networks (or ones already inside RFC 1918) the private set stays the
# standard PRIVATE_V4 list >w<
def test_private_set_unchanged_without_new_networks():
    assert private_set(()) == PRIVATE_V4
    assert private_set(("192.168.1.0/24", "10.10.10.0/24")) == PRIVATE_V4      # already covered


# A LAN that uses PUBLIC addresses (some universities do!) gets added to the private set. Nested networks collapse
# so nothing overlaps, and IPv6 entries are left out of this IPv4 list OwO
def test_private_set_adds_public_lan_without_overlaps():
    out = private_set(("128.32.0.0/16", "128.32.5.0/24", "fd00::/8"))
    nets = [ipaddress.IPv4Network(n) for n in out]
    assert ipaddress.IPv4Network("128.32.0.0/16") in nets and "fd00::/8" not in out
    # *pounces on every pair* no two networks in the result may overlap ^w^
    for i, a in enumerate(nets):
        for b in nets[i + 1:]:
            assert not a.overlaps(b)


# Reserved networks passed to render() really show up in the router's firewall rules UwU
def test_reserved_networks_reach_the_router_policy(cfg, ref_spec):
    rules = render(ref_spec, {"dmz": "eth1", "lan": "eth2"}, "eth0", build_egress=False, spec_id="x",
                   reserved=("128.32.0.0/16",))
    assert "128.32.0.0/16" in rules


# *tilts head* An engine with no API token file must refuse to start with "not_configured" instead of crashing
# later >w<
def test_unconfigured_engine_refuses_to_run(tmp_path):
    with pytest.raises(ValorError) as e:
        PVE(Config(token_file=str(tmp_path / "t.json")))
    assert e.value.code == "not_configured"


# Custom internet probe ("host:port" split into a tuple) and an uplink VLAN that lands on the router's first NIC
# OwO
def test_probe_and_uplink_vlan(cfg, ref_spec):
    c = Config(**{**cfg.__dict__, "internet_probe": "9.9.9.9:53", "uplink_vlan": 20})
    assert c.probe == ("9.9.9.9", 53)
    rtr = desired_state(c, ref_spec)[0]
    assert rtr.nics[0] == {"bridge": "vmbr0", "vlan": 20, "ip": "dhcp", "gw": None}


# FakePVE: just enough of PVE for templates(): the config and a canned resource list *nuzzles*
class FakePVE:
    def __init__(self, cfg, resources):
        self.cfg, self._r = cfg, resources

    def resources(self):
        return self._r


# Templates are found by their tags (in any order). One on the range's own node wins, one in another pool counts
# as missing, and one with no visible pool is allowed (we can see it, so we may use it) ^w^
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


# *draws a map* The topology overlay marks each node keep/create/update/remove from the plan, counts the changes,
# and every edge points at nodes that really exist UwU
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


# *paws at the guest agent* AgentAPI: a fake agent endpoint that fails with the given Proxmox messages first, then
# answers {"pid": 7}, counting every call >w<
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


# *bounces through the table* Each row is (failures, resend allowed?, expected calls, succeeds?). "Agent not
# running" means nothing was sent, so retry; a timed-out guest-exec MAY have run, so never send it twice; a
# guest-file-open is safe to resend; retries stop after AGENT_RETRIES; real errors are never retried OwO
@pytest.mark.parametrize("failures, resend, calls, ok", [
    (["QEMU guest agent is not running"] * 2, False, 3, True),     # ping timed out: nothing was sent, retry
    (["VM 5024 qga command 'guest-exec' failed - got timeout"], False, 1, False),   # may have run: never twice
    (["VM 5024 qga command 'guest-file-open' failed - got timeout"], True, 2, True),  # safe to send again
    (["QEMU guest agent is not running"] * 4, True, 4, False),     # gives up after AGENT_RETRIES
    (["can't open file"], True, 1, False),                        # real errors are not retried
])
def test_agent_requests_survive_a_busy_guest(cfg, failures, resend, calls, ok):
    # A PVE built without __init__ (no real connection), with wait_agent recording each wait ^w^
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
    # Exactly the expected calls, one agent wait between each pair, and the retry limit is still 3 UwU
    assert api.calls == calls and len(waits) == calls - 1 and P.AGENT_RETRIES == 3


# *pretends to be a slow Windows box* BusyWindows: ps() on a guest whose agent misses Proxmox's 5 second start
# limit but still runs the script. It records the runs, the PID-file reads, the cleanup and which PID got
# followed >w<
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


# *holds breath* A script whose start timed out is FOLLOWED by the PID it wrote, never started a second time OwO
def test_a_timed_out_script_is_followed_not_started_twice(cfg, monkeypatch):
    from valor import pve as P
    # No real sleeping in tests, nya~ ^w^
    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    w = BusyWindows(cfg, "VM 5024 qga command 'guest-exec' failed - got timeout")
    res = w.ps(5024, "Write-Output 'VALOR-OK'")
    assert res.out == "VALOR-OK" and w.followed == 1868 and len(w.runs) == 1        # never run a second time
    # The script writes its own PID first, switches output to UTF-8, and both temp files get deleted afterwards UwU
    assert w.script.startswith("\ufeffSet-Content -LiteralPath 'C:\\Windows\\Temp\\valor-") and "$PID" in w.script.split("\r\n")[0]
    assert "UTF8Encoding" in w.script.split("\r\n")[1]                              # UTF-8 in (BOM) and out
    assert w.deleted[0].endswith(".ps1") and w.deleted[1].endswith(".ps1.pid")       # both files cleaned up

    # Any other error: no recovery attempt, no PID-file reads >w<
    other = BusyWindows(cfg, "VM 5024 is not running")
    with pytest.raises(ValorError) as e:
        other.ps(5024, "x")
    assert e.value.code == "proxmox_api" and other.reads == 0                      # other errors: no recovery

    # *whimpers* the PID file never shows up: give up with "agent_exec_lost", still only ONE run OwO
    monkeypatch.setattr(P, "PS_START_WAIT", 0)
    lost = BusyWindows(cfg, "got timeout", pid_after=99)
    with pytest.raises(ValorError) as e:
        lost.ps(5024, "x")
    assert e.value.code == "agent_exec_lost" and len(lost.runs) == 1



# *ears droop* When the guest agent restarts mid-command, the build runs the (idempotent) script once more instead
# of failing. Found on a real range (hr-nist171)! ^w^
def test_a_script_the_agent_lost_runs_again(cfg):
    """hr-nist171 (2026-10-25): qemu-guest-agent restarted during the baseline on ntp and Proxmox answered 'PID ...
    does not exist'. The build runs the (idempotent) script once more instead of failing."""
    from types import SimpleNamespace
    from valor import apply as A
    from valor.pve import ExecResult
    pve = PVE.__new__(PVE)
    pve.cfg, waits = cfg, []
    pve.vm = lambda vmid: SimpleNamespace(agent=lambda name: SimpleNamespace(get=None))
    pve.wait_agent = lambda vmid, timeout=300: waits.append(vmid)

    # gone(): Proxmox saying the PID no longer exists, which exec_wait must turn into "agent_exec_lost" UwU
    def gone(*a, **k):
        raise ValorError("proxmox_api", "read command status in VM 5028: HTTP 500 Internal Server Error",
                         details={"content": "Agent error: PID ld does not exist"})
    pve.agent_call = gone
    with pytest.raises(ValorError) as e:
        pve.exec_wait(5028, 4711)
    assert e.value.code == "agent_exec_lost" and "restarted" in e.value.message

    # *counts on paws* flaky(lost) makes a run() that is lost `lost` times, then finishes "done" >w<
    runs = []

    def flaky(lost):
        def run():
            runs.append(1)
            if len(runs) <= lost:
                raise ValorError("agent_exec_lost", "the guest agent of VM 5028 restarted while a command ran")
            return ExecResult(0, "done", "")
        return run
    # Lost once: runs again and succeeds (with one agent wait); lost twice: the build fails OwO
    assert A.run_again_if_lost(pve, 5028, flaky(1)).out == "done" and len(runs) == 2 and waits == [5028]
    runs.clear()
    with pytest.raises(ValorError):                                    # lost twice: the build fails (and is fixed)
        A.run_again_if_lost(pve, 5028, flaky(2))
    runs.clear()

    # Any other error is raised right away, without a second run ^w^
    def broken():
        runs.append(1)
        raise ValorError("proxmox_api", "VM 5028 is not running")
    with pytest.raises(ValorError):
        A.run_again_if_lost(pve, 5028, broken)
    assert len(runs) == 1                                              # other errors are not retried

# *squints at the bytes* AgentWire: the guest agent as Proxmox's API shows it, where text goes in and out as one
# character per byte (Latin-1). Characters above 255 make Proxmox fail, just like the real thing UwU
class AgentWire:
    """The guest agent as Proxmox's API presents it: text in and out is one character per byte (Latin-1)."""

    def __init__(self, output: bytes = b""):
        from types import SimpleNamespace
        self.output, self.posts, self.files = output, [], {}
        self.exec = SimpleNamespace(post=self._exec)

    def __call__(self, name):
        from types import SimpleNamespace
        return {"exec-status": SimpleNamespace(get=self._status), "file-write": SimpleNamespace(post=self._write),
                "file-read": SimpleNamespace(get=self._read)}[name]

    @staticmethod
    def _bytes(text: str) -> bytes:              # what Proxmox base64-encodes for the guest
        if any(ord(c) > 255 for c in text):
            from proxmoxer.core import ResourceException
            raise ResourceException(500, "Internal Server Error", "Wide character in subroutine entry")
        return text.encode("latin-1")

    def _exec(self, command, **params):
        self.posts.append((command, self._bytes(params["input-data"]) if "input-data" in params else None))
        return {"pid": 1}

    def _status(self, pid):
        return {"exited": 1, "exitcode": 0, "out-data": self.output.decode("latin-1"), "err-data": ""}

    def _write(self, file, content):
        self.files[file] = self._bytes(content)

    def _read(self, file):
        return {"content": self.files[file].decode("latin-1")}


# *purrs in UTF-8* Guest text survives both ways: command output, scripts, file writes and reads keep "café" and
# "●" intact. Non-ASCII in command ARGUMENTS is refused before sending (it would wedge the agent), and output that
# isn't UTF-8 comes back exactly as it arrived >w<
def test_guest_text_is_utf8_both_ways(cfg):
    from types import SimpleNamespace
    from valor.pve import from_agent
    wire = AgentWire("● nginx.service - café\n".encode())
    pve = PVE.__new__(PVE)
    pve.cfg, pve.vm = cfg, lambda vmid: SimpleNamespace(agent=wire)
    assert pve.exec(5040, ["systemctl", "status", "nginx"]).out == "● nginx.service - café\n"   # not 'â\x97\x8f'
    pve.script(5040, "echo 'café ●'\n")
    assert wire.posts[-1] == (["/bin/bash", "-s"], "echo 'café ●'\n".encode())     # the bytes the guest reads
    pve.write_file(5040, "/etc/motd", "Willkommen ● café\n")
    assert wire.files["/etc/motd"] == "Willkommen ● café\n".encode()
    assert pve.file_read(5040, "/etc/motd") == "Willkommen ● café\n"
    with pytest.raises(ValorError) as e:            # Proxmox would leave the agent stuck: never sent
        pve.exec(5040, ["bash", "-c", "echo é"])
    assert e.value.code == "agent_non_ascii" and len(wire.posts) == 2
    assert from_agent("code page 850: \x82") == "code page 850: \x82"                 # not UTF-8: as it came
