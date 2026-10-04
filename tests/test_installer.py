"""Installer logic that must work on any cluster: parsing, defaults, validation (no Proxmox needed)."""

import tomllib

import pytest

from installer import answers as A
from installer import discover as D
from installer.appliance import config_toml, appliance_env
from installer.network import _new_lines

ROUTES = [{"dst": "default", "gateway": "10.0.20.1", "dev": "vmbr0.20"}]
LINKS = [{"ifname": "vmbr0.20", "link": "vmbr0", "linkinfo": {"info_kind": "vlan", "info_data": {"id": 20}}},
         {"ifname": "vmbr0"}]
ADDRS = [{"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
         {"ifname": "vmbr0.20", "addr_info": [{"family": "inet", "local": "10.0.20.11", "prefixlen": 24}]},
         {"ifname": "vmbr9", "addr_info": [{"family": "inet", "local": "172.30.0.11", "prefixlen": 24}]}]


def facts(**over) -> D.Facts:
    st = D.parse_storages(
        [{"storage": "local", "type": "dir", "content": "iso,vztmpl,backup", "avail": 50 * 2**30, "total": 90 * 2**30, "active": 1},
         {"storage": "local-lvm", "type": "lvmthin", "content": "images,rootdir", "avail": 300 * 2**30, "active": 1},
         {"storage": "thick", "type": "lvm", "content": "images", "avail": 900 * 2**30, "active": 1},
         {"storage": "ceph", "type": "rbd", "content": "images", "avail": 800 * 2**30, "active": 1, "shared": 1}],
        {"local": {"path": "/var/lib/vz"}})
    br = D.parse_bridges([
        {"iface": "vmbr0", "type": "bridge", "bridge_ports": "eno1", "bridge_vlan_aware": 1, "active": 1},
        {"iface": "vmbr9", "type": "bridge", "bridge_ports": "eno2", "cidr": "172.30.0.11/24", "active": 1},
        {"iface": "vmbr100", "type": "bridge", "bridge_ports": "", "bridge_vlan_aware": 1, "active": 1},
        {"iface": "eno1", "type": "eth"}])
    bridge, vlan, ip, net, gw = D.parse_mgmt(ROUTES, LINKS, ADDRS)
    base = dict(pve_version="8.4.1", node="pve-a", node_ip=ip, cluster_name="lab", nodes=[{"name": "pve-a", "ip": ip, "online": True}],
                cpu_threads=16, cpu_flags={"sse4_2"}, mem_total=64 * 2**30, mem_free=40 * 2**30, storages=st, bridges=br,
                mgmt_bridge=bridge, mgmt_vlan=vlan, mgmt_network=net, mgmt_gateway=gw,
                networks=D.parse_networks(ADDRS, [{"dst": "192.168.77.0/24", "gateway": "10.0.20.254"}]),
                nameservers=["10.0.20.53"], search_domain="lab.example", used_vmids={100, 101, 4000, 4500},
                vm_pools={100: "", 101: "", 4000: "", 4500: ""}, pools=set(), users={"root@pam": ""},
                roles={}, privileges={"VM.Monitor", "VM.Allocate", "Sys.Audit"}, valor_templates=[],
                pending_network_changes=False)
    base.update(over)
    return D.Facts(**base)


CAT = {"ubuntu-24.04": {}, "debian-13": {}}


def test_mgmt_on_vlan_interface():
    f = facts()
    assert (f.mgmt_bridge, f.mgmt_vlan, f.node_ip, f.mgmt_network, f.mgmt_gateway) == \
        ("vmbr0", 20, "10.0.20.11", "10.0.20.0/24", "10.0.20.1")
    assert f.networks == ["10.0.20.0/24", "172.30.0.0/24", "192.168.77.0/24"]     # routes too, no loopback


def test_resolv_and_cpu_flags():
    assert D.parse_resolv("search a.b c\nnameserver 127.0.0.53\nnameserver 9.9.9.9\n") == (["9.9.9.9"], "a.b")
    assert "aes" in D.parse_cpu_flags("processor: 0\nflags\t\t: fpu sse aes avx\n")


def test_defaults_are_detected_not_hardcoded():
    f = facts()
    a = A.defaults(f)
    assert a.vm_storage == "local-lvm" or a.vm_storage == "ceph"          # linked clones; plain LVM never
    assert a.range_storage != "thick"
    assert (a.vm_bridge, a.vm_vlan) == ("vmbr0", 20)
    assert a.vmid_start == 5000                                          # 4000-4999 is taken
    assert a.allowed_networks == ["10.0.20.0/24"]
    assert a.reserved_networks == f.networks
    assert a.vm_dns == ["10.0.20.53"] and a.vm_domain == "lab.example"
    assert a.snippets_storage == "local"
    assert a.instance_name == "VALOR lab"
    name, create = A.resolve_bridge(a, f)
    assert (name, create) == ("vmbr101", True)                           # vmbr100 exists


def test_validation_catches_bad_choices():
    f = facts()
    a = A.defaults(f, {"range_storage": "thick", "vm_ip": "10.0.20.50/24", "vm_gateway": "10.9.9.9",
                       "range_bridge": "vmbr9", "vmid_start": 4000, "tls": "own", "id": "Bad"})
    errs = "\n".join(A.problems(a, f, CAT))
    for needle in ("linked clones", "vm_gateway", "physical ports", "vmid_start", "tls_cert", "id:"):
        assert needle in errs, needle
    good = A.defaults(f, {"range_storage": "ceph", "vm_storage": "ceph"})
    assert A.problems(good, f, CAT) == []


def test_answers_file_roundtrip(tmp_path):
    f = facts()
    a = A.defaults(f, {"vm_ip": "10.0.20.50/24", "vm_gateway": "10.0.20.1", "allowed_networks": ["10.0.20.0/24", "10.8.0.0/16"]})
    p = tmp_path / "a.toml"
    p.write_text(a.to_toml())
    back = A.defaults(f, A.load_file(p))
    assert back == a
    p.write_text("[web]\nnot_a_setting = 1\n")
    with pytest.raises(ValueError):
        A.load_file(p)


def test_generated_config_is_valid_and_complete():
    f = facts()
    a = A.defaults(f)
    cfg = tomllib.loads(config_toml(a, f, "vmbr101", "10.0.20.11", "PEM", "10.0.20.50"))
    from valor.config import REQUIRED
    flat = {k: v for section in cfg.values() for k, v in section.items()}
    assert all(flat.get(k) for k in REQUIRED)
    assert flat["uplink_vlan"] == 20 and flat["vmid_min"] == 5000 and flat["vmid_max"] == 5899
    assert cfg["web"]["appliance_vmid"] == 5999 and flat["job_runner"] == "worker"
    env = appliance_env(a, f, "install", "10.0.20.11")
    assert 'ALLOWED_NETWORKS="10.0.20.0/24"' in env and 'SSH_FROM="10.0.20.11"' in env


def test_bridge_change_check():
    before = "auto lo\niface lo inet loopback\n\nauto vmbr0\niface vmbr0 inet static\n"
    after = before + "\nauto vmbr101\niface vmbr101 inet manual\n\tbridge-ports none\n"
    removed, added = _new_lines(before, after)
    assert removed == [] and "iface vmbr101 inet manual" in added
    removed, _ = _new_lines(before, after.replace("iface vmbr0 inet static", "iface vmbr0 inet dhcp"))
    assert removed == ["iface vmbr0 inet static"]


def test_pve8_privileges():
    from installer.identity import roles
    r = roles(facts())
    assert "VM.Monitor" in r["ValorEngine"] and not any("GuestAgent" in p for p in r["ValorEngine"])
