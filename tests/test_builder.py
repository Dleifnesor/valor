# *boops the AI builder* OwO tests/test_builder.py: tests for VALOR's AI helpers! Provider settings (with the API
# key sealed away), the validate-and-fix loop, what really goes over the wire, and answers that get cut off ^w^
"""AI providers: settings (encrypted key), the validate-and-fix loop, the wire formats, cut-off answers."""

# json, pytest, the ai package, the spec parser, and the web builder + database modules UwU *wags tail*
import json

import pytest

from valor import ai
from valor.errors import ValorError
from valor.spec import parse_spec
from valor.web import builder, db

# *borrows from a friend* the web test fixtures (an app, enrolled users, a test client) come from test_web.py >w<
from test_web import client, enroll, env  # noqa: F401  (the web app fixture and helpers)

# GOOD: a small valid spec (two segments, two hosts, one policy rule) that the fake model "writes" OwO
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


# *puts on a robot costume* Fake: a pretend AI provider that hands out canned replies in order, records every
# (system, messages) call, and charges 1000 tokens in / 200 out each time ^w^
class Fake(ai.Provider):
    name = "fake"

    def __init__(self, replies):
        super().__init__("fake-model")
        self.replies, self.calls = list(replies), []

    def complete(self, system, messages):
        self.calls.append((system, [dict(m) for m in messages]))
        return ai.Reply(self.replies.pop(0), 1000, 200)


# extract_yaml() pulls the spec out of a ```yaml fence and keeps the chat around it; trim() drops old messages so
# the history fits MAX_CHARS and always starts with a user turn UwU
def test_extract_and_trim():
    y, rest = ai.extract_yaml("Here you go.\n```yaml\nname: x\n```\nEnjoy.")
    assert y == "name: x\n" and rest == "Here you go.\n\nEnjoy."
    assert ai.extract_yaml("no spec")[0] is None
    msgs = [{"role": "assistant", "content": "hi"}] + [{"role": "user", "content": "x" * 30_000}] * 3
    out = ai.trim(msgs)
    assert out and out[0]["role"] == "user" and sum(len(m["content"]) for m in out) <= ai.MAX_CHARS


# *nibbles a stray colon* Models love writing "description: Admin PC: files" which YAML can't parse.
# repair_yaml() quotes the value (keeping the trailing comment), so no fix round is spent on it >w<
def test_repair_yaml_quotes_a_colon_in_a_plain_value():
    slip = GOOD.replace("{name: pc, segment: lan, address: 10.65.0.10}",
                        "name: pc\n    segment: lan\n    address: 10.65.0.10\n    description: Admin PC: files, db  # note")
    y, _ = ai.extract_yaml(f"Here.\n```yaml\n{slip}```")
    assert '    description: "Admin PC: files, db"  # note\n' in y
    assert parse_spec(y).hosts[1].description == "Admin PC: files, db"
    # Repaired before checking, so the very first attempt counts OwO
    fake = Fake([f"Spec.\n```yaml\n{slip}```"])           # no fix round for it
    assert ai.build(fake, "sys", [{"role": "user", "content": "lab"}], lambda t: [])["attempts"] == 1
    # extract_questions(): the clarifying questions the model asks, with their options, plus the intro text ^w^
    qs, rest = ai.extract_questions("Before I build:\n```questions\n- question: Which OS: Ubuntu or Rocky?\n"
                                    "  options: [Ubuntu, Rocky]\n```")
    assert (qs[0]["question"], qs[0]["options"], rest) == ("Which OS: Ubuntu or Rocky?", ["Ubuntu", "Rocky"], "Before I build:")
    # the opening fence dropped (Mistral wrote "down:questions"), or a yaml fence around the questions
    # *sniffs around for questions* they're still found when the opening fence went missing, when wrapped in a yaml
    # fence, or written as a bare list after "Please answer these questions" UwU
    listed = "- header: OS\n  question: Which OS?\n  options: [Ubuntu, Rocky]\n  recommended: Ubuntu\n- question: How many clients?\n"
    for text, intro in ((f"A few choices are worth pinning down:questions\n{listed}```", "A few choices are worth pinning down:"),
                        (f"Before I build:\n```yaml\n{listed}```", "Before I build:"),
                        (f"Please answer these questions\n{listed}", "Please answer these questions")):
        qs, rest = ai.extract_questions(text)
        assert [q["question"] for q in qs] == ["Which OS?", "How many clients?"] and qs[0]["recommended"] == "Ubuntu"
        assert rest == intro
    # A list in the middle of text is NOT a question block >w<
    assert ai.extract_questions("Steps:\n- question: one\n\nThen more text.")[0] is None    # not a trailing list
    # YAML repair can't fix gives back exactly what the model wrote, no harm done OwO
    for bad in ("a: b: c\nd: [e\n", "a: [b: c\n", "description: 'quoted': x\n"):
        assert ai.repair_yaml(bad) == bad                  # what it cannot make parse stays as the model wrote it


# *wiggles excitedly* The fix loop! The first reply reuses VLAN 640 (invalid), VALOR sends the problems back, and
# the second reply is accepted ^w^
def test_fix_loop():
    fake = Fake([f"First try.\n```yaml\n{GOOD.replace('vlan: 641', 'vlan: 640')}```",
                 f"Fixed the VLAN.\n```yaml\n{GOOD}```"])
    from valor.spec import parse_spec

    # check(): the problems parse_spec finds (an empty list when the spec is fine) UwU
    def check(text):
        try:
            parse_spec(text)
        except Exception as e:
            return [str(e)]
        return []
    # Two attempts, no problems left, tokens added up across both rounds >w<
    res = ai.build(fake, "SYSTEM", [{"role": "user", "content": "a web server and a client"}], check)
    assert res["attempts"] == 2 and not res["problems"] and res["reply"] == "Fixed the VLAN."
    assert res["input_tokens"] == 2000 and res["output_tokens"] == 400
    # The follow-up message says what went wrong, that the person can't see it, and not to apologize OwO
    second = fake.calls[1][1]
    assert second[-1]["role"] == "user" and "could not use that spec" in second[-1]["content"]
    assert "does not see" in second[-1]["content"] and "do not apologize" in second[-1]["content"]
    assert res["fixed"] == ["spec has 1 problem(s)"]                    # what the model corrected, for the UI
    assert ai.build(Fake([f"```yaml\n{GOOD}```"]), "S", [{"role": "user", "content": "x"}], check)["fixed"] == []
    # *sighs* a spec outside a ```yaml block, every time: it gives up after FIX_ROUNDS extra tries ^w^
    gave_up = ai.build(Fake([f"Spec:\n{GOOD}"] * 3), "S", [{"role": "user", "content": "x"}], lambda t: [])
    assert gave_up["problems"] == ["the spec is not in a ```yaml block"] and gave_up["attempts"] == ai.FIX_ROUNDS + 1
    # a plain answer (the person asked something else) is not forced into a spec
    plain = ai.build(Fake(["Paris."]), "S", [{"role": "user", "content": "capital of France?"}], check)
    assert plain["attempts"] == 1 and plain["reply"] == "Paris." and not plain["problems"] and plain["yaml"] is None
    # a spec in a fence without a language (or ```YAML) is still the spec
    for fence in ("```", "```YAML"):
        res = ai.build(Fake([f"Here.\n{fence}\n{GOOD}```"]), "S", [{"role": "user", "content": "x"}], check)
        assert res["attempts"] == 1 and res["yaml"] == GOOD and res["reply"] == "Here."


# *mimics a chatty server* Stream: a fake streamed (server-sent events) HTTP response that sends each event as a
# "data:" line, can fail with a status code, can stall mid-stream, and ends with [DONE] UwU
class Stream:
    """A streamed (server-sent events) HTTP response."""

    def __init__(self, events=(), status=200, error=None, stall=None):
        self.status_code, self._events, self._error, self._stall = status, list(events), error, stall
        self.encoding = "ISO-8859-1"    # what requests assumes for text/event-stream without a charset (LM Studio)

    def json(self):
        return {"error": self._error or "x"}

    # *encodes very carefully* events are encoded to UTF-8 bytes and decoded with the response's (wrong) encoding,
    # just like requests does for LM Studio, so the provider has to fix it >w<
    def iter_lines(self, decode_unicode=True):
        for e in self._events:
            yield ("data: " + json.dumps(e, ensure_ascii=False)).encode().decode(self.encoding)
            yield ""
        if self._stall:
            raise self._stall
        yield "data: [DONE]"

    def close(self):
        pass


# The OpenAI-compatible request: URL joined right, Bearer token, the system prompt first, streaming on (with usage
# reporting), (connect, read) timeouts, and the streamed pieces put together OwO
def test_wire_formats(monkeypatch):
    sent = {}

    def post(url, headers, json, timeout, stream):
        sent.update(url=url, headers=headers, body=json, timeout=timeout, stream=stream)
        return Stream([{"choices": [{"delta": {"content": "O"}}]}, {"choices": [{"delta": {"content": "K"}, "finish_reason": "stop"}]},
                       {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}])
    monkeypatch.setattr(ai.providers.requests, "post", post)
    r = ai.OpenAICompatible("llama", api_key="t", base_url="http://10.0.0.5:8000/v1/").complete("SYS", [{"role": "user", "content": "hi"}])
    assert sent["url"] == "http://10.0.0.5:8000/v1/chat/completions" and sent["headers"]["authorization"] == "Bearer t"
    assert sent["body"]["messages"][0] == {"role": "system", "content": "SYS"} and sent["body"]["stream_options"]
    assert sent["body"]["stream"] is True and sent["stream"] is True and sent["timeout"] == (10, 300)
    assert sent["body"]["model"] == "llama" and (r.text, r.input_tokens, r.output_tokens) == ("OK", 9, 2)


# *paws at old servers* An older server rejects stream_options, so VALOR retries without it; and reasoning text
# plus non-ASCII characters come through as real UTF-8 ^w^
def test_streaming_edge_cases(monkeypatch):
    calls = []

    def post(url, headers, json, timeout, stream):
        calls.append(dict(json))
        if "stream_options" in json:          # an older server
            return Stream(status=400, error="unknown field stream_options")
        return Stream([{"choices": [{"delta": {"content": "fine"}, "finish_reason": "stop"}]}])
    monkeypatch.setattr(ai.providers.requests, "post", post)
    r = ai.OpenAICompatible("m", base_url="https://x.example/v1").complete("S", [])
    assert r.text == "fine" and len(calls) == 2 and "stream_options" not in calls[1]
    monkeypatch.setattr(ai.providers.requests, "post", lambda *a, **key: Stream(
        [{"choices": [{"delta": {"reasoning_content": "web → db — ok"}}]},
         {"choices": [{"delta": {"content": "It isn’t café"}, "finish_reason": "stop"}]}]))
    r = ai.OpenAICompatible("m", base_url="https://x.example/v1").complete("S", [])
    assert (r.text, r.reasoning) == ("It isn’t café", "web → db — ok")     # UTF-8, not mojibake


# OpenRouter: its fixed URL (base_url ignored), the key, the X-Title header, and "reasoning" picked up UwU
def test_openrouter_on_the_wire(monkeypatch):
    sent = {}

    def post(url, headers, json, timeout, stream):
        sent.update(url=url, headers=headers, body=json)
        return Stream([{"choices": [{"delta": {"reasoning": "think"}}]}, {"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]},
                       {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}])
    monkeypatch.setattr(ai.providers.requests, "post", post)
    p = ai.provider_from({"provider": "openrouter", "model": "qwen/qwen3.8-27b", "base_url": "http://ignored/v1"}, "sk-or-1")
    r = p.complete("S", [{"role": "user", "content": "hi"}])
    assert sent["url"] == "https://openrouter.ai/api/v1/chat/completions" and sent["headers"]["authorization"] == "Bearer sk-or-1"
    assert sent["headers"]["X-Title"] == "VALOR" and sent["body"]["model"] == "qwen/qwen3.8-27b"
    assert (r.text, r.reasoning, r.input_tokens) == ("OK", "think", 9)
    # *ears flat* a mid-stream error is an error, never a silently half-empty answer >w<
    # an error after the stream started is an error, not an empty answer
    monkeypatch.setattr(ai.providers.requests, "post", lambda *a, **key: Stream([
        {"choices": [{"delta": {"content": "half"}}]},
        {"error": {"code": "server_error", "message": "Provider disconnected unexpectedly"},
         "choices": [{"delta": {"content": ""}, "finish_reason": "error"}]}]))
    with pytest.raises(ValorError) as e:
        p.complete("S", [])
    assert e.value.code == "ai_error" and "Provider disconnected" in e.value.message
    # HTTP 402: out of credits, with a hint about Max tokens OwO
    monkeypatch.setattr(ai.providers.requests, "post", lambda *a, **key: Stream(status=402, error={"message": "can only afford 9000"}))
    with pytest.raises(ValorError) as e:
        p.complete("S", [])
    assert e.value.code == "ai_credits" and "Max tokens" in e.value.hint and "9000" in e.value.message

    # Models: a fake /models listing, so the context length of the chosen model can be looked up ^w^
    class Models:
        ok = True

        def json(self):
            return {"data": [{"id": "other", "context_length": 8192}, {"id": "qwen/qwen3.8-27b", "context_length": 1000000}]}
    monkeypatch.setattr(ai.providers.requests, "get", lambda url, timeout: Models() if url == "https://openrouter.ai/api/v1/models" else None)
    assert p.context_length() == 1000000
    # A full context window on OpenRouter gets an OpenRouter hint (not the LM Studio one) UwU
    full = ai.cut_off(p, ai.Reply("", 30000, 2, truncated=True))
    assert full.code == "ai_context_full" and "OpenRouter" in full.hint and "LM Studio" not in full.hint


# *dusts off an old config* The single-provider settings of older versions are migrated into the provider list,
# and the API key is re-sealed (never stored in plain text) >w<
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
    # "provider: none" means no providers at all OwO
    db.put_setting(conn, "ai", {"provider": "none"})
    assert builder.ai_settings(conn, box)["providers"] == []


# *peeks at LM Studio* The context length is the LOADED one: an unloaded model says nothing, a loaded one gives
# its real size, and a stream that goes silent says how long it waited ^w^
def test_context_length_is_the_loaded_one(monkeypatch):
    # Info: a fake HTTP answer holding the given JSON UwU
    class Info:
        ok = True

        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d
    p = ai.OpenAICompatible("m", base_url="http://x:1234/v1")
    monkeypatch.setattr(ai.providers.requests, "get", lambda *a, **key: Info({"state": "not-loaded", "max_context_length": 262144}))
    assert p.context_length() is None                  # unloaded: the model's maximum says nothing about the load
    monkeypatch.setattr(ai.providers.requests, "get", lambda *a, **key: Info({"loaded_context_length": 35328, "max_context_length": 262144}))
    assert p.context_length() == 35328
    monkeypatch.setattr(ai.providers.requests, "post", lambda *a, **key: Stream([{"choices": [{"delta": {"content": "half"}}]}],
                                                                     stall=ai.providers.requests.ConnectionError("Read timed out.")))
    with pytest.raises(ValorError) as e:
        ai.OpenAICompatible("m", base_url="https://x.example/v1", timeout=60).complete("S", [])
    assert "sent nothing for 60 s" in e.value.message and "raise the timeout" in e.value.hint


# *guards the settings page* The AI settings API: only admins can see or change it, plain http to a public name is
# refused, keys come back masked (and are kept when the mask is sent back), names must be unique, OpenRouter needs
# a key and a model, retired provider kinds are rejected, and every change is audited >w<
def test_ai_settings(env):
    app, wcfg = env
    with client(app) as adm, client(app) as op:
        ame, _ = enroll(adm, "admin")
        ah = {"X-CSRF-Token": ame["csrf"]}
        enroll(op, "olivia")
        assert op.get("/api/settings/ai").status_code == 403
        assert op.get("/api/builder").json() == {"configured": False, "default": "", "providers": []}
        lm = {"name": "LM Studio", "provider": "openai", "model": "m", "base_url": "http://example.com/v1"}
        assert adm.put("/api/settings/ai", json={"providers": [lm]}, headers=ah).status_code == 400   # http to a public name
        lm["base_url"] = "http://192.168.1.50:8000/v1"
        orr = {"name": "OpenRouter", "provider": "openrouter", "model": "qwen/qwen3.8-27b", "api_key": "sk-or-SECRET",
               "default": True}
        r = adm.put("/api/settings/ai", json={"providers": [lm, orr], "daily_tokens_per_user": 5000}, headers=ah)
        assert r.status_code == 200, r.text
        lm_out, or_out = r.json()["providers"]
        assert or_out["api_key"] == builder.MASK and or_out["default"]
        assert adm.get("/api/settings/ai").json()["kinds"] == ["openai", "openrouter"]
        assert not lm_out["default"] and lm_out["api_key"] == "" and lm_out["id"] != or_out["id"]
        # The raw database row never holds the key in plain text OwO
        raw = db.connect(wcfg.db).execute("SELECT value FROM settings WHERE key='ai'").fetchone()[0]
        assert "sk-or-SECRET" not in raw
        r = adm.put("/api/settings/ai", json={"providers": [lm_out, or_out], "daily_tokens_per_user": 5000}, headers=ah)
        stored = json.loads(db.connect(wcfg.db).execute("SELECT value FROM settings WHERE key='ai'").fetchone()[0])
        assert r.status_code == 200 and stored["providers"][1]["api_key"] and stored["default"] == or_out["id"]  # key kept
        for bad in ([lm_out, {**or_out, "id": None, "name": "lm studio"}],                  # names are unique
                    [{"name": "OR", "provider": "openrouter", "model": "qwen/qwen3.8-27b"}],  # OpenRouter needs a key
                    [{"name": "OR", "provider": "openrouter", "api_key": "k"}]):             # and a model
            assert adm.put("/api/settings/ai", json={"providers": bad}, headers=ah).status_code == 400
        gone = {"name": "C", "provider": "anthropic", "model": "m", "api_key": "k"}           # no longer a provider kind
        assert adm.put("/api/settings/ai", json={"providers": [gone]}, headers=ah).status_code == 422
        # Operators see which providers exist, never their keys ^w^
        info = op.get("/api/builder").json()
        assert info["configured"] and info["default"] == or_out["id"] and "api_key" not in json.dumps(info)
        assert [(p["name"], p["provider"], p["model"]) for p in info["providers"]] == [
            ("LM Studio", "openai", "m"), ("OpenRouter", "openrouter", "qwen/qwen3.8-27b")]
        audit = db.connect(wcfg.db).execute("SELECT COUNT(*) FROM audit WHERE action='settings.ai'").fetchone()[0]
        assert audit == 2


# *nom nom tokens* A reply cut off at Max tokens fails at once with a hint (no pointless retries), and new
# providers get the default Max tokens UwU
def test_cut_off_answers_and_defaults(env):
    assert ai.DEFAULTS["max_tokens"] >= 16384 and ai.Provider("m").max_tokens == ai.DEFAULTS["max_tokens"]

    # Cut: a provider whose every answer stops halfway through the spec >w<
    class Cut(Fake):
        def complete(self, system, messages):
            self.calls.append(1)
            return ai.Reply("```yaml\nname: half", 900, self.max_tokens, truncated=True)
    cut = Cut([])
    with pytest.raises(ValorError) as e:
        ai.build(cut, "S", [{"role": "user", "content": "x"}], lambda t: [])
    assert e.value.code == "ai_truncated" and len(cut.calls) == 1 and "Max tokens" in e.value.hint   # no retries
    # The settings page shows the default Max tokens for a provider saved without one OwO
    app, wcfg = env
    with client(app) as adm:
        ame, _ = enroll(adm, "admin")
        adm.put("/api/settings/ai", json={"providers": [{"name": "OR", "provider": "openrouter", "model": "m",
                                                          "api_key": "k"}]}, headers={"X-CSRF-Token": ame["csrf"]})
        assert adm.get("/api/settings/ai").json()["providers"][0]["max_tokens"] == ai.DEFAULTS["max_tokens"]


# *stretches the context window* A full context window is reported as such (not blamed on Max tokens), and
# <think> tags are split off from the answer ^w^
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


# finish_reason "length" on the wire means truncated; "stop" means a complete answer UwU
def test_cut_off_detected_on_the_wire(monkeypatch):
    monkeypatch.setattr(ai.providers.requests, "post", lambda url, headers, json, timeout, stream: Stream(
        [{"choices": [{"delta": {"content": "x"}, "finish_reason": "length"}]}]))
    assert ai.OpenAICompatible("m", base_url="https://x.example/v1").complete("S", []).truncated
    monkeypatch.setattr(ai.providers.requests, "post", lambda url, headers, json, timeout, stream: Stream(
        [{"choices": [{"delta": {"reasoning_content": "hmm "}}]}, {"choices": [{"delta": {"content": "ok"}}]},
         {"choices": [{"delta": {}, "finish_reason": "stop"}]}]))
    r = ai.OpenAICompatible("m", base_url="https://x.example/v1").complete("S", [])
    assert r.text == "ok" and r.reasoning == "hmm " and not r.truncated


# *hides under the desk* Each kind of connection error names the address and what to check (LM Studio's network
# setting, the listener, the timeout) >w<
def test_connection_errors_say_what_to_check(monkeypatch):
    # boom(exc): a fake post() that raises the given exception OwO
    def boom(exc):
        def post(*a, **key):
            raise exc
        return post
    p = ai.OpenAICompatible("m", base_url="http://192.168.1.67:1234/v1")
    for exc, words in ((ai.providers.requests.ConnectTimeout(), "Serve on Local Network"),
                       (ai.providers.requests.ConnectionError(), "listens on the network"),
                       (ai.providers.requests.ReadTimeout(), "raise the timeout")):
        monkeypatch.setattr(ai.providers.requests, "post", boom(exc))
        with pytest.raises(ValorError) as e:
            p.complete("S", [])
        assert "192.168.1.67:1234" in e.value.message and words in e.value.hint

    # A 404 from a base URL without /v1 hints to add /v1 ^w^
    monkeypatch.setattr(ai.providers.requests, "post", lambda *a, **key: Stream(status=404))
    with pytest.raises(ValorError) as e:
        ai.OpenAICompatible("m", base_url="http://192.168.1.67:1234").complete("S", [])
    assert "/v1" in e.value.hint
