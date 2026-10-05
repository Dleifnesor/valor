"""Chat builder: provider settings (encrypted key), the validate-and-fix loop, budgets, permissions, wire format."""

import dataclasses
import json

import pytest

from valor import ai, validate
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
    assert "does not see" in second[-1]["content"] and "do not apologize" in second[-1]["content"]
    assert res["fixed"] == ["spec has 1 problem(s)"]                    # what the model corrected, for the UI
    assert ai.build(Fake([f"```yaml\n{GOOD}```"]), "S", [{"role": "user", "content": "x"}], check)["fixed"] == []
    gave_up = ai.build(Fake(["no yaml here"] * 3), "S", [{"role": "user", "content": "x"}], lambda t: [])
    assert gave_up["problems"] and gave_up["attempts"] == ai.FIX_ROUNDS + 1


class Stream:
    """A streamed (server-sent events) HTTP response."""

    def __init__(self, events=(), status=200, error=None, stall=None):
        self.status_code, self._events, self._error, self._stall = status, list(events), error, stall

    def json(self):
        return {"error": self._error or "x"}

    def iter_lines(self, decode_unicode=True):
        for e in self._events:
            yield "data: " + json.dumps(e)
            yield ""
        if self._stall:
            raise self._stall
        yield "data: [DONE]"

    def close(self):
        pass


def test_wire_formats(monkeypatch):
    sent = {}

    def post(url, headers, json, timeout, stream):
        sent.update(url=url, headers=headers, body=json, timeout=timeout, stream=stream)
        if "anthropic" in url:
            return Stream([{"type": "message_start", "message": {"usage": {"input_tokens": 12}}},
                           {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "O"}},
                           {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "K"}},
                           {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}}])
        return Stream([{"choices": [{"delta": {"content": "O"}}]}, {"choices": [{"delta": {"content": "K"}, "finish_reason": "stop"}]},
                       {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}])
    monkeypatch.setattr(ai.requests, "post", post)
    r = ai.Anthropic("claude-sonnet-5-5", api_key="k").complete("SYS", [{"role": "user", "content": "hi"}])
    assert sent["url"] == "https://api.anthropic.com/v1/messages" and sent["headers"]["x-api-key"] == "k"
    assert sent["headers"]["anthropic-version"] == "2023-06-01" and sent["body"]["system"] == "SYS"
    assert sent["body"]["stream"] is True and sent["stream"] is True and sent["timeout"] == (10, 300)
    assert sent["body"]["model"] == "claude-sonnet-5-5" and (r.text, r.input_tokens, r.output_tokens) == ("OK", 12, 3)
    r = ai.OpenAICompatible("llama", api_key="t", base_url="http://10.0.0.5:8000/v1/").complete("SYS", [{"role": "user", "content": "hi"}])
    assert sent["url"] == "http://10.0.0.5:8000/v1/chat/completions" and sent["headers"]["authorization"] == "Bearer t"
    assert sent["body"]["messages"][0] == {"role": "system", "content": "SYS"} and sent["body"]["stream_options"]
    assert (r.text, r.input_tokens, r.output_tokens) == ("OK", 9, 2)


def test_streaming_edge_cases(monkeypatch):
    calls = []

    def post(url, headers, json, timeout, stream):
        calls.append(dict(json))
        if "stream_options" in json:          # an older server
            return Stream(status=400, error="unknown field stream_options")
        return Stream([{"choices": [{"delta": {"content": "fine"}, "finish_reason": "stop"}]}])
    monkeypatch.setattr(ai.requests, "post", post)
    r = ai.OpenAICompatible("m", base_url="https://x.example/v1").complete("S", [])
    assert r.text == "fine" and len(calls) == 2 and "stream_options" not in calls[1]
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: Stream([{"choices": [{"delta": {"content": "half"}}]}],
                                                                     stall=ai.requests.ConnectionError("Read timed out.")))
    with pytest.raises(ai.ValorError) as e:
        ai.OpenAICompatible("m", base_url="https://x.example/v1", timeout=60).complete("S", [])
    assert "sent nothing for 60 s" in e.value.message and "raise the timeout" in e.value.hint


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
        assert len(res["fixed"]) == 1 and "already exists" in res["fixed"][0] and res["warnings"] == []
        assert seen["key"] == "sk-ant-SECRET"                                               # decrypted server-side only
        system, msgs = fake.calls[0]
        assert "ad-dc" in system and "access:" in system and "taken" in system              # roles, reference, facts
        assert "The current spec in the builder" in msgs[-1]["content"]
        assert "already exists" in fake.calls[1][1][-1]["content"]
        assert op.get("/api/builder").json() == {"configured": True, "provider": "anthropic", "model": "claude-sonnet-5-5"}
        u = adm.get("/api/settings/ai/usage").json()["users"]
        assert u[0]["username"] == "olivia" and u[0]["input"] == 2000 and u[0]["today"] == 2400
        fake.replies = [f"No hardening.\n```yaml\n{GOOD}baseline: none\n```"]
        res = op.post("/api/builder/chat", json=ask, headers=oh).json()
        assert res["ok"] and res["fixed"] == [] and res["warnings"] == [validate.NO_BASELINE]   # shown, not hidden
        fake.replies = [f"```yaml\n{GOOD}```"] * 5
        for _ in range(2):                      # 2400 + 3 x 1200 tokens (with the one above) > the 5000 budget
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


def test_cut_off_answers_and_defaults(env, monkeypatch):
    assert ai.DEFAULTS["max_tokens"] >= 16384 and ai.Provider("m").max_tokens == ai.DEFAULTS["max_tokens"]

    class Cut(Fake):
        def complete(self, system, messages):
            self.calls.append(1)
            return ai.Reply("```yaml\nname: half", 900, self.max_tokens, truncated=True)
    cut = Cut([])
    with pytest.raises(ai.ValorError) as e:
        ai.build(cut, "S", [{"role": "user", "content": "x"}], lambda t: [])
    assert e.value.code == "ai_truncated" and len(cut.calls) == 1          # no pointless retries
    app, wcfg = env
    builder._rate.clear()
    with client(app) as adm, client(app) as op:
        ame, _ = enroll(adm, "admin")
        assert adm.get("/api/settings/ai").json()["max_tokens"] == ai.DEFAULTS["max_tokens"]
        adm.put("/api/settings/ai", json={"provider": "anthropic", "api_key": "k"}, headers={"X-CSRF-Token": ame["csrf"]})
        ome, _ = enroll(op, "olivia")
        monkeypatch.setattr(ai, "provider_from", lambda st, key: Cut([]))
        r = op.post("/api/builder/chat", json={"messages": [{"role": "user", "content": "big lab"}]},
                    headers={"X-CSRF-Token": ome["csrf"]})
        assert r.status_code == 422 and r.json()["error"] == "ai_truncated" and "Max tokens" in r.json()["hint"]
        spent = db.connect(wcfg.db).execute("SELECT SUM(output_tokens) FROM ai_usage").fetchone()[0]
        assert spent == ai.DEFAULTS["max_tokens"]                             # cut-off answers still count


def test_a_full_context_window_is_not_blamed_on_max_tokens():
    """A local model loaded with a small context fills it with the prompt and stops at once with "length"."""
    p = ai.Provider("m", max_tokens=32768)
    e = ai.cut_off(p, ai.Reply("", 30000, 2, truncated=True))
    assert e.code == "ai_context_full" and "30,000" in e.message and "Context Length" in e.hint
    assert ai.cut_off(p, ai.Reply("", 0, 0, truncated=True)).code == "ai_context_full"        # no usage, no text
    assert ai.cut_off(p, ai.Reply("x" * 100, 900, 32768, truncated=True)).code == "ai_truncated"
    assert ai.cut_off(p, ai.Reply("", 9, 5, truncated=True, context_full=True)).code == "ai_context_full"
    assert ai.split_thinking("<think>plan it</think>The answer") == ("The answer", "plan it")
    assert ai.split_thinking("no tags") == ("no tags", "")


def test_cut_off_detected_on_the_wire(monkeypatch):
    monkeypatch.setattr(ai.requests, "post", lambda url, headers, json, timeout, stream: Stream(
        [{"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 5}}]
        if "anthropic" in url else [{"choices": [{"delta": {"content": "x"}, "finish_reason": "length"}]}]))
    assert ai.Anthropic("m", api_key="k").complete("S", []).truncated
    assert ai.OpenAICompatible("m", base_url="https://x.example/v1").complete("S", []).truncated
    monkeypatch.setattr(ai.requests, "post", lambda url, headers, json, timeout, stream: Stream(
        [{"choices": [{"delta": {"reasoning_content": "hmm "}}]}, {"choices": [{"delta": {"content": "ok"}}]},
         {"choices": [{"delta": {}, "finish_reason": "stop"}]}]))
    r = ai.OpenAICompatible("m", base_url="https://x.example/v1").complete("S", [])
    assert r.text == "ok" and r.reasoning == "hmm " and not r.truncated


def test_connection_errors_say_what_to_check(monkeypatch):
    def boom(exc):
        def post(*a, **k):
            raise exc
        return post
    p = ai.OpenAICompatible("m", base_url="http://192.168.1.67:1234/v1")
    for exc, words in ((ai.requests.ConnectTimeout(), "Serve on Local Network"),
                       (ai.requests.ConnectionError(), "listens on the network"),
                       (ai.requests.ReadTimeout(), "raise the timeout")):
        monkeypatch.setattr(ai.requests, "post", boom(exc))
        with pytest.raises(ai.ValorError) as e:
            p.complete("S", [])
        assert "192.168.1.67:1234" in e.value.message and words in e.value.hint

    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: Stream(status=404))
    with pytest.raises(ai.ValorError) as e:
        ai.OpenAICompatible("m", base_url="http://192.168.1.67:1234").complete("S", [])
    assert "/v1" in e.value.hint
