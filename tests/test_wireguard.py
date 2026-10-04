"""WireGuard access: spec checks, keys at rest, router and peer configs, firewall rules, hashes, web endpoints."""

import base64
import dataclasses
import json

import pytest

from valor import wireguard
from valor.crypto import Box
from valor.errors import SpecError
from valor.netpolicy import render
from valor.plan import desired_state
from valor.spec import canonical, normalize, parse_spec

from test_web import client, enroll, env  # noqa: F401  (the web app fixture and helpers)

BASE = """
apiVersion: valor/v1
name: wglab
segments:
  - {name: srv, vlan: 901, cidr: 10.91.0.0/24}
  - {name: users, vlan: 902, cidr: 10.92.0.0/24}
hosts:
  - {name: web, segment: srv, address: 10.91.0.10}
  - {name: pc, segment: users, address: 10.92.0.10}
"""
WG = BASE + """access:
  wireguard:
    peers: [alice, bob]
    reach: [srv]
"""


def spec_of(text: str):
    return normalize(parse_spec(text), "ubuntu-24.04")


@pytest.fixture
def kcfg(cfg, tmp_path):
    key = tmp_path / "secret.key"
    Box.create_key_file(key)
    return dataclasses.replace(cfg, secret_key_file=str(key))


@pytest.mark.parametrize("change,message", [
    (("reach: [srv]", "reach: [dmz]"), "unknown segment 'dmz'"),
    (("[alice, bob]", "[alice, alice]"), "unique"),
    (("[alice, bob]", "[Alice]"), "lowercase"),
    (("reach: [srv]", "reach: [srv]\n    network: 10.91.0.0/16"), "overlaps segment srv"),
    (("reach: [srv]", "reach: [srv]\n    network: 8.8.8.0/24"), "private"),
    (("reach: [srv]", "reach: [srv]\n    port: 80"), "greater than or equal"),
    (("reach: [srv]", "reach: [srv]\n    endpoint: 'a b'"), "pattern"),
])
def test_spec_checks(change, message):
    with pytest.raises(SpecError) as e:
        parse_spec(WG.replace(*change))
    assert message in json.dumps(e.value.to_dict())


def test_spec_defaults_and_stable_hashes():
    wg = parse_spec(WG).access.wireguard
    assert wg.port == 51820 and str(wg.network) == "10.250.0.0/24" and wg.endpoint == ""
    assert "access" not in canonical(parse_spec(BASE))           # older specs keep their hashes
    assert '"access"' in canonical(parse_spec(WG))


def test_keys_addresses_and_configs(kcfg):
    spec = spec_of(WG)
    d1 = wireguard.ensure(kcfg, spec)
    assert d1["server_address"] == "10.250.0.1"
    assert [p["address"] for p in d1["peers"].values()] == ["10.250.0.2", "10.250.0.3"]
    raw = wireguard._path(kcfg, "wglab").read_text()
    assert d1["server"]["private"] not in raw and d1["peers"]["alice"]["psk"] not in raw      # encrypted at rest
    # a public key really belongs to its private key
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    k = X25519PrivateKey.from_private_bytes(base64.b64decode(d1["peers"]["alice"]["private"]))
    assert base64.b64encode(k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)).decode() == \
        d1["peers"]["alice"]["public"]

    server = wireguard.server_conf(d1, spec)
    assert "ListenPort = 51820" in server and server.count("[Peer]") == 2 and "AllowedIPs = 10.250.0.3/32" in server
    assert d1["peers"]["alice"]["private"] not in server                 # the router never sees peer private keys
    wireguard.set_router_address(kcfg, "wglab", "192.168.1.50")
    data = wireguard.load(kcfg, "wglab")
    alice = wireguard.peer_conf(data, spec, "alice", wireguard.endpoint(spec, data))
    assert "Endpoint = 192.168.1.50:51820" in alice and "AllowedIPs = 10.91.0.0/24, 10.250.0.0/24" in alice
    assert d1["peers"]["alice"]["private"] in alice and d1["peers"]["bob"]["private"] not in alice

    # adding/removing peers keeps the others' keys and addresses
    d2 = wireguard.ensure(kcfg, spec_of(WG.replace("[alice, bob]", "[bob, carol]")))
    assert d2["peers"]["bob"] == d1["peers"]["bob"] and set(d2["peers"]) == {"bob", "carol"}
    assert d2["peers"]["carol"]["address"] == "10.250.0.2" and d2["server"] == d1["server"]


def test_rotation_changes_the_router_hash_adding_keys_does_not(kcfg):
    spec = spec_of(WG)
    before = wireguard.digest(kcfg, spec)
    d1 = wireguard.ensure(kcfg, spec)
    assert wireguard.digest(kcfg, spec) == before                       # first keys: plan == apply
    d2 = wireguard.ensure(kcfg, spec, rotate=("alice",))
    assert wireguard.digest(kcfg, spec) != before
    assert d2["peers"]["alice"]["public"] != d1["peers"]["alice"]["public"]
    assert d2["peers"]["alice"]["address"] == d1["peers"]["alice"]["address"]
    assert d2["peers"]["bob"] == d1["peers"]["bob"]


def test_router_rules_and_plan(kcfg):
    spec = spec_of(WG)
    rules = render(spec, {"srv": "eth1", "users": "eth2"}, "eth0", build_egress=False, spec_id="x")
    assert 'iifname "eth0" udp dport 51820 accept' in rules
    assert 'iifname "wg0" oifname "eth1" ip saddr 10.250.0.0/24 ip daddr 10.91.0.0/24 accept' in rules
    assert 'oifname "eth2" ip saddr 10.250.0.0/24' not in rules          # users is not in reach
    plain = render(spec_of(BASE), {"srv": "eth1", "users": "eth2"}, "eth0", build_egress=False, spec_id="x")
    assert "wg0" not in plain and "51820" not in plain
    a, b = desired_state(kcfg, spec_of(BASE)), desired_state(kcfg, spec)
    assert a[0].conv_hash != b[0].conv_hash                             # the router converges
    assert [d.conv_hash for d in a[1:]] == [d.conv_hash for d in b[1:]]  # the hosts don't


def test_router_script_and_handshakes():
    script = wireguard.router_script("[Interface]\nPrivateKey = SECRET\n")
    assert "PrivateKey = SECRET" in script and "<<'VALOR_WG_EOF'" in script and "umask 077" in script
    assert "wg syncconf" in script
    gone = wireguard.router_script(None)
    assert "VALOR-WG-REMOVED" in gone and "ip link del wg0" in gone and "disable --now -q wg-quick@wg0" in gone
    dump = "priv\tpub\t51820\toff\nPUBA\tpsk\t1.2.3.4:5\t10.250.0.2/32\t1790000000\t1\t2\t25\nPUBB\tpsk\t(none)\t10.250.0.3/32\t0\t0\t0\t25\n"
    assert wireguard.handshakes(dump) == {"PUBA": 1790000000, "PUBB": 0}


def test_web_endpoints(env, tmp_path):
    from valor.web import db
    app, wcfg = env
    cfg = dataclasses.replace(app.state.cfg, data_dir=str(tmp_path / "data"))
    app.state.cfg = cfg
    cfg.ranges_dir.mkdir(parents=True)
    (cfg.ranges_dir / "wglab.yaml").write_text(WG)
    from valor import jobs
    with client(app) as op, client(app) as viewer:
        me, _ = enroll(op, "olivia")
        vme, _ = enroll(viewer, "vic")
        st = viewer.get("/api/ranges/wglab/wireguard").json()
        assert st["configured"] and not st["built"] and [p["ready"] for p in st["peers"]] == [False, False]
        assert op.get("/api/ranges/wglab/wireguard/peers/alice").status_code == 409            # not built yet
        wireguard.ensure(cfg, spec_of(WG))
        wireguard.set_router_address(cfg, "wglab", "192.168.1.50")
        assert viewer.get("/api/ranges/wglab/wireguard/peers/alice").status_code == 403
        assert op.get("/api/ranges/wglab/wireguard/peers/mallory").status_code == 404
        r = op.get("/api/ranges/wglab/wireguard/peers/alice")
        assert r.status_code == 200 and "Endpoint = 192.168.1.50:51820" in r.json()["config"]
        assert r.json()["qr_svg"].startswith('<svg xmlns="http://www.w3.org/2000/svg"') and r.json()["filename"] == "wglab-alice.conf"
        audit = db.connect(wcfg.db).execute(
            "SELECT COUNT(*) FROM audit WHERE action='range.wireguard.config'").fetchone()[0]
        assert audit == 1
        st = viewer.get("/api/ranges/wglab/wireguard").json()
        assert st["endpoint"] == "192.168.1.50:51820" and st["peers"][0]["address"] == "10.250.0.2"
        j = op.post("/api/ranges/wglab/wireguard/peers/bob/rotate", json={}, headers={"X-CSRF-Token": me["csrf"]})
        assert j.status_code == 200 and jobs.status(cfg, j.json()["job"])["kind"] == "wg_rotate"
        assert viewer.post("/api/ranges/wglab/wireguard/peers/bob/rotate", json={},
                           headers={"X-CSRF-Token": vme["csrf"]}).status_code == 403


def test_job_notifications_name_the_job():
    from valor.web.worker import _describe
    assert _describe({"kind": "power", "target": {"range": "x", "action": "start"}}, {"ok": True})[1] == \
        "Range x: start done"
    assert _describe({"kind": "destroy", "target": {"range": "x"}}, {"ok": True, "removed": [1, 2]})[1] == \
        "Range x destroyed"
    assert _describe({"kind": "wg_rotate", "target": {"range": "x", "peers": ["bob"]}}, {"ok": True})[1] == \
        "Range x: new WireGuard keys for bob"
