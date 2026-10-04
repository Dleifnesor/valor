"""Chat builder: provider settings (encrypted key), the validate-and-fix loop, budgets, permissions, wire format."""

import dataclasses
import json

import pytest

from valor import ai
from valor.web import builder, db

from test_web import client, enroll, env  # noqa: F401  (the web app fixture and helpers)

GOOD = """apiVersion: valor/v1
name: chatlab
description: Two segments built by the chat builder.
segments:
  - {name: dmz, vlan: 640, cidr: 10.64.0.0/24, internet: true}
  - {name: lan, vlan: 641, cidr: 10.65.0.0/24}
hosts:
  - {name: web, segment: dmz, address: 10.64.0.10, roles: [{name: nginx}]}
  - {name: pc, segment: lan, address: 10.65.0.10}
policy:
  - {from: pc, to: web, proto: tcp, ports: [80]}
"""


class Fake(ai.Provider):
    name = "fake"

    def __init__(self, replies):
        super().__init__("fake-model")
        self.replies, self.calls = list(replies), []

    def complete(self, system, messages):
        self.calls.append((system, [dict(m) for m in messages]))
        return ai.Reply(self.replies.pop(0), 1000, 200)


def test_extract_and_trim():
    y, rest = ai.extract_yaml("Here you go.\n```yaml\nname: x\n```\nEnjoy.")
    assert y == "name: x\n" and rest == "Here you go.\n\nEnjoy."
    assert ai.extract_yaml("no spec")[0] is None
    msgs = [{"role": "assistant", "content": "hi"}] + [{"role": "user", "content": "x" * 30_000}] * 3
    out = ai.trim(msgs)
    assert out and out[0]["role"] == "user" and sum(len(m["content"]) for m in out) <= ai.MAX_CHARS


def test_fix_loop():
    fake = Fake([f"First try.\n```yaml\n{GOOD.replace('vlan: 641', 'vlan: 640')}```",
                 f"Fixed the VLAN.\n```yaml\n{GOOD}```"])
    from valor.spec import parse_spec

    def check(text):
        try:
            parse_spec(text)
        except Exception as e:
            return [str(e)]
        return []
    res = ai.build(fake, "SYSTEM", [{"role": "user", "content": "a web server and a client"}], check)
    assert res["attempts"] == 2 and not res["problems"] and res["reply"] == "Fixed the VLAN."
    assert res["input_tokens"] == 2000 and res["output_tokens"] == 400
    second = fake.calls[1][1]
    assert second[-1]["role"] == "user" and "could not use that spec" in second[-1]["content"]
    gave_up = ai.build(Fake(["no yaml here"] * 3), "S", [{"role": "user", "content": "x"}], lambda t: [])
    assert gave_up["problems"] and gave_up["attempts"] == ai.FIX_ROUNDS + 1


def test_wire_formats(monkeypatch):
    sent = {}

    class R:
        def __init__(self, body):
            self.status_code, self._b = 200, body

        def json(self):
            return self._b

    def post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, body=json)
        if "anthropic" in url:
            return R({"content": [{"type": "text", "text": "OK"}], "usage": {"input_tokens": 12, "output_tokens": 3}})
        return R({"choices": [{"message": {"content": "OK"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 2}})
    monkeypatch.setattr(ai.requests, "post", post)
    r = ai.Anthropic("claude-sonnet-5-5", api_key="k").complete("SYS", [{"role": "user", "content": "hi"}])
    assert sent["url"] == "https://api.anthropic.com/v1/messages" and sent["headers"]["x-api-key"] == "k"
    assert sent["headers"]["anthropic-version"] == "2023-06-01" and sent["body"]["system"] == "SYS"
    assert sent["body"]["model"] == "claude-sonnet-5-5" and (r.text, r.input_tokens, r.output_tokens) == ("OK", 12, 3)
    r = ai.OpenAICompatible("llama", api_key="t", base_url="http://10.0.0.5:8000/v1/").complete("SYS", [{"role": "user", "content": "hi"}])
    assert sent["url"] == "http://10.0.0.5:8000/v1/chat/completions" and sent["headers"]["authorization"] == "Bearer t"
    assert sent["body"]["messages"][0] == {"role": "system", "content": "SYS"} and r.output_tokens == 2


def test_settings_and_chat(env, tmp_path, monkeypatch):
    app, wcfg = env
    cfg = dataclasses.replace(app.state.cfg, data_dir=str(tmp_path / "data"))
    app.state.cfg = cfg
    cfg.ranges_dir.mkdir(parents=True)
    (cfg.ranges_dir / "taken.yaml").write_text(GOOD.replace("name: chatlab", "name: taken"))
    builder._rate.clear()
    with client(app) as adm, client(app) as op, client(app) as viewer:
        ame, _ = enroll(adm, "admin")
        ah = {"X-CSRF-Token": ame["csrf"]}
        ome, _ = enroll(op, "olivia")
        oh = {"X-CSRF-Token": ome["csrf"]}
        vme, _ = enroll(viewer, "vic")
        ask = {"messages": [{"role": "user", "content": "a web server and a client that may browse it"}]}
        assert op.post("/api/builder/chat", json=ask, headers=oh).status_code == 409          # no provider yet
        assert op.get("/api/settings/ai").status_code == 403
        bad = adm.put("/api/settings/ai", json={"provider": "openai", "model": "m", "base_url": "http://example.com/v1"}, headers=ah)
        assert bad.status_code == 400
        ok = adm.put("/api/settings/ai", json={"provider": "openai", "model": "m", "base_url": "http://192.168.1.50:8000/v1"}, headers=ah)
        assert ok.status_code == 200
        r = adm.put("/api/settings/ai", json={"provider": "anthropic", "api_key": "sk-ant-SECRET", "daily_tokens_per_user": 5000}, headers=ah)
        assert r.status_code == 200 and r.json()["api_key"] == builder.MASK and r.json()["model"] == "claude-sonnet-5-5"
        raw = db.connect(wcfg.db).execute("SELECT value FROM settings WHERE key='ai'").fetchone()[0]
        assert "sk-ant-SECRET" not in raw
        r = adm.put("/api/settings/ai", json={"provider": "anthropic", "api_key": builder.MASK, "daily_tokens_per_user": 5000}, headers=ah)
        assert r.status_code == 200 and json.loads(db.connect(wcfg.db).execute(
            "SELECT value FROM settings WHERE key='ai'").fetchone()[0])["api_key"]               # key kept

        fake = Fake([f"Named it taken.\n```yaml\n{GOOD.replace('name: chatlab', 'name: taken')}```",
                     f"Renamed.\n```yaml\n{GOOD}```"])
        seen = {}

        def provider_from(st, key):
            seen["key"] = key
            return fake
        monkeypatch.setattr(ai, "provider_from", provider_from)
        assert viewer.post("/api/builder/chat", json=ask, headers={"X-CSRF-Token": vme["csrf"]}).status_code == 403
        r = op.post("/api/builder/chat", json={**ask, "spec": "name: draft"}, headers=oh)
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["ok"] and res["attempts"] == 2 and res["reply"] == "Renamed." and res["topology"]["nodes"]
        assert seen["key"] == "sk-ant-SECRET"                                               # decrypted server-side only
        system, msgs = fake.calls[0]
        assert "ad-dc" in system and "access:" in system and "taken" in system              # roles, reference, facts
        assert "The current spec in the builder" in msgs[-1]["content"]
        assert "already exists" in fake.calls[1][1][-1]["content"]
        assert op.get("/api/builder").json() == {"configured": True, "provider": "anthropic", "model": "claude-sonnet-5-5"}
        u = adm.get("/api/settings/ai/usage").json()["users"]
        assert u[0]["username"] == "olivia" and u[0]["input"] == 2000 and u[0]["today"] == 2400
        fake.replies = [f"```yaml\n{GOOD}```"] * 5
        for _ in range(3):                      # 2400 + 3 x 1200 tokens > the 5000 budget
            op.post("/api/builder/chat", json=ask, headers=oh)
        over = op.post("/api/builder/chat", json=ask, headers=oh)
        assert over.status_code == 429 and over.json()["error"] == "ai_budget"
        builder._rate.clear()
        adm.put("/api/settings/ai", json={"provider": "anthropic", "api_key": builder.MASK, "daily_tokens_per_user": 0}, headers=ah)
        fake.replies = [f"```yaml\n{GOOD}```"] * 20
        codes = [op.post("/api/builder/chat", json=ask, headers=oh).status_code for _ in range(builder.RATE[0] + 1)]
        assert codes[-1] == 429 and codes[0] == 200
        audit = db.connect(wcfg.db).execute("SELECT COUNT(*) FROM audit WHERE action='settings.ai'").fetchone()[0]
        assert audit == 4
