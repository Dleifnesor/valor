"""Chat builder: provider settings (encrypted key), the validate-and-fix loop, budgets, permissions, wire format."""

import dataclasses
import json

import pytest

from valor import ai, validate
from valor.spec import parse_spec
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


def test_repair_yaml_quotes_a_colon_in_a_plain_value():
    slip = GOOD.replace("{name: pc, segment: lan, address: 10.65.0.10}",
                        "name: pc\n    segment: lan\n    address: 10.65.0.10\n    description: Admin PC: files, db  # note")
    y, _ = ai.extract_yaml(f"Here.\n```yaml\n{slip}```")
    assert '    description: "Admin PC: files, db"  # note\n' in y
    assert parse_spec(y).hosts[1].description == "Admin PC: files, db"
    fake = Fake([f"Spec.\n```yaml\n{slip}```"])           # no fix round for it
    assert ai.build(fake, "sys", [{"role": "user", "content": "lab"}], lambda t: [])["attempts"] == 1
    qs, rest = ai.extract_questions("Before I build:\n```questions\n- question: Which OS: Ubuntu or Rocky?\n"
                                    "  options: [Ubuntu, Rocky]\n```")
    assert (qs[0]["question"], qs[0]["options"], rest) == ("Which OS: Ubuntu or Rocky?", ["Ubuntu", "Rocky"], "Before I build:")
    # the opening fence dropped (Mistral wrote "down:questions"), or a yaml fence around the questions
    listed = "- header: OS\n  question: Which OS?\n  options: [Ubuntu, Rocky]\n  recommended: Ubuntu\n- question: How many clients?\n"
    for text, intro in ((f"A few choices are worth pinning down:questions\n{listed}```", "A few choices are worth pinning down:"),
                        (f"Before I build:\n```yaml\n{listed}```", "Before I build:"),
                        (f"Please answer these questions\n{listed}", "Please answer these questions")):
        qs, rest = ai.extract_questions(text)
        assert [q["question"] for q in qs] == ["Which OS?", "How many clients?"] and qs[0]["recommended"] == "Ubuntu"
        assert rest == intro
    assert ai.extract_questions("Steps:\n- question: one\n\nThen more text.")[0] is None    # not a trailing list
    for bad in ("a: b: c\nd: [e\n", "a: [b: c\n", "description: 'quoted': x\n"):
        assert ai.repair_yaml(bad) == bad                  # what it cannot make parse stays as the model wrote it


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
    gave_up = ai.build(Fake([f"Spec:\n{GOOD}"] * 3), "S", [{"role": "user", "content": "x"}], lambda t: [])
    assert gave_up["problems"] == ["the spec is not in a ```yaml block"] and gave_up["attempts"] == ai.FIX_ROUNDS + 1
    # a plain answer (the person asked something else) is not forced into a spec
    plain = ai.build(Fake(["Paris."]), "S", [{"role": "user", "content": "capital of France?"}], check)
    assert plain["attempts"] == 1 and plain["reply"] == "Paris." and not plain["problems"] and plain["yaml"] is None
    # a spec in a fence without a language (or ```YAML) is still the spec
    for fence in ("```", "```YAML"):
        res = ai.build(Fake([f"Here.\n{fence}\n{GOOD}```"]), "S", [{"role": "user", "content": "x"}], check)
        assert res["attempts"] == 1 and res["yaml"] == GOOD and res["reply"] == "Here."


class Stream:
    """A streamed (server-sent events) HTTP response."""

    def __init__(self, events=(), status=200, error=None, stall=None):
        self.status_code, self._events, self._error, self._stall = status, list(events), error, stall
        self.encoding = "ISO-8859-1"    # what requests assumes for text/event-stream without a charset (LM Studio)

    def json(self):
        return {"error": self._error or "x"}

    def iter_lines(self, decode_unicode=True):
        for e in self._events:
            yield ("data: " + json.dumps(e, ensure_ascii=False)).encode().decode(self.encoding)
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
                           {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                           {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "O"}},
                           {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "K"}},
                           {"type": "content_block_stop", "index": 0},
                           {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}}])
        return Stream([{"choices": [{"delta": {"content": "O"}}]}, {"choices": [{"delta": {"content": "K"}, "finish_reason": "stop"}]},
                       {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}])
    monkeypatch.setattr(ai.requests, "post", post)
    r = ai.Anthropic("claude-sonnet-5-5", api_key="k").complete("SYS", [{"role": "user", "content": "hi"}])
    assert sent["url"] == "https://api.anthropic.com/v1/messages" and sent["headers"]["x-api-key"] == "k"
    assert sent["headers"]["anthropic-version"] == "2023-06-01"
    assert sent["body"]["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert sent["body"]["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert sent["body"]["output_config"] == {"effort": "high"} and sent["body"]["fallbacks"] == "default"
    assert sent["headers"]["anthropic-beta"] == "server-side-fallback-2026-07-01"
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
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: Stream(
        [{"choices": [{"delta": {"reasoning_content": "web → db — ok"}}]},
         {"choices": [{"delta": {"content": "It isn’t café"}, "finish_reason": "stop"}]}]))
    r = ai.OpenAICompatible("m", base_url="https://x.example/v1").complete("S", [])
    assert (r.text, r.reasoning) == ("It isn’t café", "web → db — ok")     # UTF-8, not mojibake


def test_openrouter_on_the_wire(monkeypatch):
    sent = {}

    def post(url, headers, json, timeout, stream):
        sent.update(url=url, headers=headers, body=json)
        return Stream([{"choices": [{"delta": {"reasoning": "think"}}]}, {"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]},
                       {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}])
    monkeypatch.setattr(ai.requests, "post", post)
    p = ai.provider_from({"provider": "openrouter", "model": "qwen/qwen3.8-27b", "base_url": "http://ignored/v1"}, "sk-or-1")
    r = p.complete("S", [{"role": "user", "content": "hi"}])
    assert sent["url"] == "https://openrouter.ai/api/v1/chat/completions" and sent["headers"]["authorization"] == "Bearer sk-or-1"
    assert sent["headers"]["X-Title"] == "VALOR" and sent["body"]["model"] == "qwen/qwen3.8-27b"
    assert (r.text, r.reasoning, r.input_tokens) == ("OK", "think", 9)
    # an error after the stream started is an error, not an empty answer
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: Stream([
        {"choices": [{"delta": {"content": "half"}}]},
        {"error": {"code": "server_error", "message": "Provider disconnected unexpectedly"},
         "choices": [{"delta": {"content": ""}, "finish_reason": "error"}]}]))
    with pytest.raises(ai.ValorError) as e:
        p.complete("S", [])
    assert e.value.code == "ai_error" and "Provider disconnected" in e.value.message
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: Stream(status=402, error={"message": "can only afford 9000"}))
    with pytest.raises(ai.ValorError) as e:
        p.complete("S", [])
    assert e.value.code == "ai_credits" and "Max tokens" in e.value.hint and "9000" in e.value.message

    class Models:
        ok = True

        def json(self):
            return {"data": [{"id": "other", "context_length": 8192}, {"id": "qwen/qwen3.8-27b", "context_length": 1000000}]}
    monkeypatch.setattr(ai.requests, "get", lambda url, timeout: Models() if url == "https://openrouter.ai/api/v1/models" else None)
    assert p.context_length() == 1000000
    full = ai.cut_off(p, ai.Reply("", 30000, 2, truncated=True))
    assert full.code == "ai_context_full" and "OpenRouter" in full.hint and "LM Studio" not in full.hint


def test_one_provider_from_earlier_versions_is_migrated(env):
    app, wcfg = env
    box = app.state.box
    conn = db.connect(wcfg.db)
    db.put_setting(conn, "ai", {"provider": "openai", "model": "qwen/qwen3.8-27b", "base_url": "http://192.168.1.67:1234/v1",
                                "api_key": box.seal_text("lm-token", "ai:api_key"), "max_tokens": 64000,
                                "daily_tokens_per_user": 1234, "timeout": 500})
    prov, st = builder.resolve(conn, box)
    assert prov.api_key == "lm-token" and prov.base_url == "http://192.168.1.67:1234/v1" and prov.timeout == 500
    assert (st["name"], st["max_tokens"], st["daily_tokens_per_user"]) == ("Local model server", 64000, 1234)
    stored = db.get_setting(conn, "ai")
    assert stored["default"] == stored["providers"][0]["id"] and "lm-token" not in json.dumps(stored)
    assert builder.resolve(conn, box)[0].api_key == "lm-token"            # stored in the new form, key re-sealed
    db.put_setting(conn, "ai", {"provider": "none"})
    assert builder.ai_settings(conn, box)["providers"] == []


def test_context_length_is_the_loaded_one(monkeypatch):
    class Info:
        ok = True

        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d
    p = ai.OpenAICompatible("m", base_url="http://x:1234/v1")
    monkeypatch.setattr(ai.requests, "get", lambda *a, **k: Info({"state": "not-loaded", "max_context_length": 262144}))
    assert p.context_length() is None                  # unloaded: the model's maximum says nothing about the load
    monkeypatch.setattr(ai.requests, "get", lambda *a, **k: Info({"loaded_context_length": 35328, "max_context_length": 262144}))
    assert p.context_length() == 35328
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
        lm = {"name": "LM Studio", "provider": "openai", "model": "m", "base_url": "http://example.com/v1"}
        assert adm.put("/api/settings/ai", json={"providers": [lm]}, headers=ah).status_code == 400   # http to a public name
        lm["base_url"] = "http://192.168.1.50:8000/v1"
        cl = {"name": "Claude", "provider": "anthropic", "api_key": "sk-ant-SECRET", "default": True}
        r = adm.put("/api/settings/ai", json={"providers": [lm, cl], "daily_tokens_per_user": 5000}, headers=ah)
        assert r.status_code == 200, r.text
        lm_out, cl_out = r.json()["providers"]
        assert cl_out["api_key"] == builder.MASK and cl_out["model"] == "claude-sonnet-5-5" and cl_out["default"]
        assert not lm_out["default"] and lm_out["api_key"] == "" and lm_out["id"] != cl_out["id"]
        raw = db.connect(wcfg.db).execute("SELECT value FROM settings WHERE key='ai'").fetchone()[0]
        assert "sk-ant-SECRET" not in raw
        r = adm.put("/api/settings/ai", json={"providers": [lm_out, cl_out], "daily_tokens_per_user": 5000}, headers=ah)
        stored = json.loads(db.connect(wcfg.db).execute("SELECT value FROM settings WHERE key='ai'").fetchone()[0])
        assert r.status_code == 200 and stored["providers"][1]["api_key"] and stored["default"] == cl_out["id"]  # key kept
        for bad in ([lm_out, {**cl_out, "id": None, "name": "lm studio"}],                  # names are unique
                    [{"name": "OR", "provider": "openrouter", "model": "qwen/qwen3.8-27b"}]):  # OpenRouter needs a key
            assert adm.put("/api/settings/ai", json={"providers": bad}, headers=ah).status_code == 400

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
        info = op.get("/api/builder").json()
        assert info["configured"] and info["default"] == cl_out["id"] and "api_key" not in json.dumps(info)
        assert [(p["name"], p["provider"], p["model"]) for p in info["providers"]] == [
            ("LM Studio", "openai", "m"), ("Claude", "anthropic", "claude-sonnet-5-5")]
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
        adm.put("/api/settings/ai", json={"providers": [lm_out, cl_out], "daily_tokens_per_user": 0}, headers=ah)
        fake.replies = [f"```yaml\n{GOOD}```"] * 20
        codes = [op.post("/api/builder/chat", json=ask, headers=oh).status_code for _ in range(builder.RATE[0] + 1)]
        assert codes[-1] == 429 and codes[0] == 200
        audit = db.connect(wcfg.db).execute("SELECT COUNT(*) FROM audit WHERE action='settings.ai'").fetchone()[0]
        assert audit == 3


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
        adm.put("/api/settings/ai", json={"providers": [{"name": "Claude", "provider": "anthropic", "api_key": "k"}]},
                headers={"X-CSRF-Token": ame["csrf"]})
        assert adm.get("/api/settings/ai").json()["providers"][0]["max_tokens"] == ai.DEFAULTS["max_tokens"]
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
