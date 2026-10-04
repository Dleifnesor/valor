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
