"""Range blueprints: copies get free VLANs and networks, keep host positions, student peers; deploy = preview."""

import dataclasses
import ipaddress
import shutil

import pytest

from valor import blueprints as bp
from valor.errors import ValorError
from valor.spec import parse_spec

from conftest import ROOT
from test_web import client, enroll, env  # noqa: F401  (the web app fixture and helpers)

BLUEPRINT = """
apiVersion: valor/v1
name: weblab
description: Web lab for one student.
segments:
  - {name: dmz, vlan: 110, cidr: 10.110.0.0/24, internet: true}
  - {name: lan, vlan: 120, cidr: 10.120.0.0/24}
hosts:
  - name: web
    segment: dmz
    address: 10.110.0.10
    roles: [{name: nginx}]
  - name: db
    segment: lan
    address: 10.120.0.20
    roles: [{name: postgresql, params: {allow_from: web}}]
policy:
  - {from: web, to: db, proto: tcp, ports: [5432]}
tests:
  - {from: web, to: 10.120.0.20, proto: tcp, port: 5432, expect: open}
access:
  wireguard:
    peers: [instructor]
"""


@pytest.fixture
def dcfg(cfg, tmp_path):
    """A data dir with the repository's example ranges already 'deployed'."""
    data = tmp_path / "data"
    shutil.copytree(ROOT / "ranges", data / "ranges")
    return dataclasses.replace(cfg, data_dir=str(data))


def test_copies_get_their_own_vlans_networks_and_peers(dcfg):
    spec = bp.save(dcfg, "weblab", BLUEPRINT)
    existing_vlans = {s.vlan for p in dcfg.ranges_dir.glob("*.yaml") for s in parse_spec(p.read_text()).segments}
    out = bp.plan_copies(dcfg, spec, [{"name": "lab-alice", "student": "alice"}, {"name": "lab-bob", "student": "bob"}],
                         live_vlans={100: "other"}, bp_id="weblab")
    vlans = [s.vlan for c in out for s in c["spec"].segments]
    assert len(set(vlans)) == 4 and not set(vlans) & (existing_vlans | {100})
    nets = [s.cidr for c in out for s in c["spec"].segments] + [c["spec"].access.wireguard.network for c in out]
    assert all(not a.overlaps(b) for i, a in enumerate(nets) for b in nets[i + 1:])
    assert all(not n.overlaps(ipaddress.IPv4Network("192.168.50.0/24")) for n in nets)       # reserved network
    alice = out[0]["spec"]
    web, db = alice.host("web"), alice.host("db")
    assert int(web.address) - int(alice.segment("dmz").cidr.network_address) == 10       # .10 stays .10
    assert int(db.address) - int(alice.segment("lan").cidr.network_address) == 20
    assert alice.tests[0].to == str(db.address)                                          # explicit IPs follow
    assert alice.access.wireguard.peers == ["instructor", "alice"]
    assert out[0]["yaml"].startswith("# valor-blueprint: weblab\n") and "Copy for alice" in alice.description
    assert alice.host("db").roles[0].params == {"allow_from": "web"}
    assert "internet: false" not in out[0]["yaml"] and "cores: 1" not in out[0]["yaml"]      # reads hand-written


def test_names_are_checked(dcfg):
    spec = bp.save(dcfg, "weblab", BLUEPRINT)
    for copies, code in (([{"name": "web2tier"}], "range_exists"), ([{"name": "Bad Name"}], "invalid_name"),
                         ([{"name": "a1"}, {"name": "a1"}], "duplicate_names"), ([], "no_copies")):
        with pytest.raises(ValorError) as e:
            bp.plan_copies(dcfg, spec, copies, bp_id="weblab")
        assert e.value.code == code
    with pytest.raises(ValorError):
        bp.save(dcfg, "../evil", BLUEPRINT)
    with pytest.raises(ValorError):
        bp.save(dcfg, "broken", BLUEPRINT.replace("vlan: 120", "vlan: 110"))                # invalid spec


def test_web_preview_and_deploy(env, tmp_path):
    app, wcfg = env
    cfg = dataclasses.replace(app.state.cfg, data_dir=str(tmp_path / "data"))
    app.state.cfg = cfg
    with client(app) as op, client(app) as viewer:
        me, _ = enroll(op, "olivia")
        h = {"X-CSRF-Token": me["csrf"]}
        vme, _ = enroll(viewer, "vic")
        assert op.put("/api/blueprints/weblab", json={"yaml": BLUEPRINT}, headers=h).status_code == 200
        assert viewer.put("/api/blueprints/weblab", json={"yaml": BLUEPRINT},
                          headers={"X-CSRF-Token": vme["csrf"]}).status_code == 403
        assert op.put("/api/blueprints/x", json={"yaml": "name: [oops"}, headers=h).status_code in (400, 422)
        lst = viewer.get("/api/blueprints").json()["blueprints"]
        assert lst[0]["id"] == "weblab" and lst[0]["wireguard"] and lst[0]["hosts"] == 2
        assert viewer.get("/api/blueprints/weblab").json()["topology"]["nodes"]
        copies = {"copies": [{"name": "lab-alice", "student": "alice"}, {"name": "lab-bob", "student": "bob"}]}
        p = op.post("/api/blueprints/weblab/preview", json=copies, headers=h).json()
        assert p["cluster_error"] and not p["ok"] and len(p["copies"]) == 2           # no cluster in tests
        bad = op.post("/api/blueprints/weblab/deploy", json={**copies, "deploy_hash": "0" * 32, "build": False}, headers=h)
        assert bad.status_code == 409
        nobuild = op.post("/api/blueprints/weblab/deploy", json={**copies, "deploy_hash": p["deploy_hash"], "build": True},
                          headers=h)
        assert nobuild.status_code == 409                                             # can't build without a cluster
        r = op.post("/api/blueprints/weblab/deploy", json={**copies, "deploy_hash": p["deploy_hash"], "build": False},
                    headers=h)
        assert r.status_code == 200 and r.json()["ranges"] == ["lab-alice", "lab-bob"] and r.json()["jobs"] == []
        assert (cfg.ranges_dir / "lab-alice.yaml").read_text() == p["copies"][0]["yaml"]
        assert viewer.get("/api/blueprints").json()["blueprints"][0]["copies"] == ["lab-alice", "lab-bob"]
        again = op.post("/api/blueprints/weblab/preview", json=copies, headers=h)
        assert again.status_code == 400 and again.json()["error"] == "range_exists"
        assert op.post("/api/blueprints/from-range", json={"range": "lab-bob", "id": "weblab2"}, headers=h).status_code == 200
        assert "valor-blueprint" not in op.get("/api/blueprints/weblab2").json()["yaml"]
        assert op.delete("/api/blueprints/weblab2", headers=h).status_code == 200
        assert op.get("/api/blueprints/weblab2").status_code == 404
