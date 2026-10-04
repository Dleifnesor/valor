"""Editing a built range: map edits into a draft, what the draft changes, the draft API and the range chat."""

import dataclasses

import pytest

from valor import ai, drafts
from valor.errors import ValorError
from valor.spec import normalize, parse_spec
from valor.web import builder, db

from test_builder import Fake
from test_web import client, enroll, env  # noqa: F401  (the web app fixture and helpers)

SPEC = """apiVersion: valor/v1
name: lab
segments:
  - {name: dmz, vlan: 680, cidr: 10.68.0.0/24, internet: true}
  - {name: lan, vlan: 681, cidr: 10.69.0.0/24}
hosts:
  - {name: web, segment: dmz, address: 10.68.0.10, roles: [{name: nginx}]}
  - {name: pc, segment: lan, address: 10.69.0.10}
policy:
  - {from: pc, to: web, proto: tcp, ports: [80]}
tests:
  - {from: pc, to: web, proto: tcp, port: 80, expect: open}
"""


def test_map_edits():
    spec = parse_spec(SPEC)
    data = drafts.apply_ops(spec, [
        {"op": "add_host", "segment": "dmz", "name": "db", "os": "debian-13", "memory": 2048},
        {"op": "add_role", "host": "db", "role": "postgresql", "params": {"allow_from": "web"}, "allow_from": ["web"]},
        {"op": "update_host", "name": "pc", "cores": 2},
    ], {"postgresql": [5432]})
    new = parse_spec(drafts.to_yaml(data))
    db = new.host("db")
    assert str(db.address) == "10.68.0.11" and db.memory == 2048 and db.roles[0].name == "postgresql"   # .10 taken
    assert len(new.policy) == 1                       # web and db share the DMZ: no rule needed
    lan = parse_spec(drafts.to_yaml(drafts.apply_ops(new, [{"op": "add_role", "host": "db", "role": "nginx",
                                                            "allow_from": ["lan"]}], {"nginx": [80]})))
    assert any(r.from_ == "lan" and r.to == "db" and r.ports == [80] for r in lan.policy)
    assert new.host("pc").cores == 2
    gone = parse_spec(drafts.to_yaml(drafts.apply_ops(spec, [{"op": "remove_host", "name": "pc"}])))
    assert [h.name for h in gone.hosts] == ["web"] and gone.policy == [] and gone.tests == []   # its rules go too
    for ops, code in (([{"op": "add_host", "segment": "dmz", "name": "web"}], "name_taken"),
                      ([{"op": "add_host", "segment": "nope", "name": "x1"}], "unknown_segment"),
                      ([{"op": "add_role", "host": "web", "role": "nginx"}], "role_exists"),
                      ([{"op": "remove_role", "host": "pc", "role": "nginx"}], "no_role")):
        with pytest.raises(ValorError) as e:
            drafts.apply_ops(spec, ops)
        assert e.value.code == code


def test_what_a_draft_changes():
    cur = normalize(parse_spec(SPEC), "ubuntu-24.04")
    d = drafts.apply_ops(parse_spec(SPEC), [{"op": "add_host", "segment": "lan", "name": "pc2"},
                                            {"op": "add_role", "host": "pc", "role": "dev-tools"},
                                            {"op": "remove_host", "name": "web"}])
    d["hosts"][0]["os"] = "debian-13"                                    # pc: a new OS means a new VM
    ch = drafts.changes(cur, normalize(parse_spec(drafts.to_yaml(d)), "ubuntu-24.04"))
    acts = {a["host"]: a["action"] for a in ch["actions"]}
    assert acts == {"pc": "replace", "pc2": "create", "web": "remove", "rtr": "update"}
    assert ch["summary"] == {"replace": 1, "create": 1, "remove": 1, "update": 1}


def _app(env, tmp_path):
    app, wcfg = env
    cfg = dataclasses.replace(app.state.cfg, data_dir=str(tmp_path / "data"))
    app.state.cfg = cfg
    cfg.ranges_dir.mkdir(parents=True)
    (cfg.ranges_dir / "lab.yaml").write_text(SPEC)
    builder._rate.clear()
    return app, wcfg, cfg


def test_draft_api(env, tmp_path):
    app, wcfg, cfg = _app(env, tmp_path)
    with client(app) as op, client(app) as viewer:
        me, _ = enroll(op, "olivia")
        h = {"X-CSRF-Token": me["csrf"]}
        vme, _ = enroll(viewer, "vic")
        assert viewer.get("/api/ranges/lab/draft").json()["draft"] is None
        add = {"ops": [{"op": "add_host", "segment": "lan", "name": "kali1", "os": "kali", "memory": 2048,
                        "roles": [{"name": "attacker-tools"}]}]}
        assert viewer.post("/api/ranges/lab/draft/ops", json=add, headers={"X-CSRF-Token": vme["csrf"]}).status_code == 403
        r = op.post("/api/ranges/lab/draft/ops", json=add, headers=h).json()
        assert r["draft"]["summary"] == {"create": 1} and "kali1" in r["draft"]["diff"]
        node = next(n for n in r["topology"]["nodes"] if n["id"] == "host:kali1")
        assert node["change"] == "create" and node["address"] == "10.69.0.11"
        assert (cfg.ranges_dir / "lab.yaml").read_text() == SPEC              # the spec itself is untouched
        bad = op.post("/api/ranges/lab/draft/ops", json={"ops": [{"op": "add_role", "host": "pc", "role": "nope"}]}, headers=h)
        assert bad.status_code == 400 and bad.json()["error"] == "unknown_role"
        assert op.put("/api/ranges/lab/draft", json={"yaml": "name: [x"}, headers=h).status_code == 400
        assert op.put("/api/ranges/lab/draft", json={"yaml": SPEC.replace("name: lab", "name: other")},
                      headers=h).json()["error"] == "name_mismatch"
        assert op.delete("/api/ranges/lab/draft", headers=h).json()["draft"] is None
        assert db.connect(wcfg.db).execute("SELECT COUNT(*) FROM audit WHERE action='range.draft.edit'").fetchone()[0] == 1


def test_range_chat_modes(env, tmp_path, monkeypatch):
    app, wcfg, cfg = _app(env, tmp_path)
    new_spec = SPEC.replace("  - {name: pc, segment: lan, address: 10.69.0.10}",
                            "  - {name: pc, segment: lan, address: 10.69.0.10}\n  - {name: pc2, segment: lan, address: 10.69.0.11}")
    fake = Fake(["The web server is reachable from pc on port 80 only.",
                 f"- add pc2 to the LAN\n```yaml\n{new_spec}```",
                 f"Added pc3.\n```yaml\n{new_spec.replace('pc2', 'pc3')}```",
                 f"Renamed.\n```yaml\n{SPEC.replace('name: lab', 'name: other')}```"] + ["```yaml\nname: [x\n```"] * 3)
    with client(app) as adm, client(app) as op, client(app) as viewer:
        ame, _ = enroll(adm, "admin")
        adm.put("/api/settings/ai", json={"provider": "anthropic", "api_key": "k"}, headers={"X-CSRF-Token": ame["csrf"]})
        monkeypatch.setattr(ai, "provider_from", lambda st, key: fake)
        me, _ = enroll(op, "olivia")
        h = {"X-CSRF-Token": me["csrf"]}
        vme, _ = enroll(viewer, "vic")
        q = op.post("/api/ranges/lab/chat", json={"mode": "question", "message": "who can reach web?"}, headers=h).json()
        assert q["message"]["content"].startswith("The web server") and q["draft"] is None
        system, msgs = fake.calls[0]
        assert "Do not write or change the spec" in system and "name: lab" in system and "Live state" in system
        p = op.post("/api/ranges/lab/chat", json={"mode": "plan", "message": "add a second client"}, headers=h).json()
        assert p["draft"]["summary"] == {"create": 1} and p["message"]["meta"]["draft"] == {"create": 1}
        assert "complete updated spec" in fake.calls[1][0] and len(fake.calls[1][1]) == 3   # history went along
        c = op.post("/api/ranges/lab/chat", json={"mode": "code", "message": "call it pc3"}, headers=h).json()
        assert "+  - {name: pc3" in c["message"]["meta"]["diff"] or "pc3" in c["message"]["meta"]["diff"]
        assert "kept as a draft" not in c["message"]["content"]
        bad = op.post("/api/ranges/lab/chat", json={"mode": "code", "message": "rename the range"}, headers=h).json()
        assert bad["message"]["meta"]["problems"] and "pc3" in bad["draft"]["yaml"]          # draft kept, not replaced
        hist = viewer.get("/api/ranges/lab/chat").json()["messages"]
        assert [m["role"] for m in hist[:2]] == ["user", "assistant"] and len(hist) == 8
        assert viewer.post("/api/ranges/lab/chat", json={"mode": "question", "message": "hi"},
                           headers={"X-CSRF-Token": vme["csrf"]}).status_code == 403
        assert op.delete("/api/ranges/lab/chat", headers=h).status_code == 200
        assert viewer.get("/api/ranges/lab/chat").json()["messages"] == []
