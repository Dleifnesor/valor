"""Chat builder: describe an environment, an AI model writes the range spec.

Providers are pluggable (Anthropic's Messages API, any OpenAI-compatible endpoint such as a local model server, or
none). The model only ever produces text: VALOR extracts the YAML, validates it against the schema and the cluster,
and asks the model to fix what fails. Nothing is saved or built without a person: the spec goes to the editor,
then plan -> approve like any other range.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml
from urllib.parse import urlparse

from . import compliance
from .errors import ValorError

PROVIDERS = ("anthropic", "openai", "openrouter")
KIND_NAMES = {"anthropic": "Anthropic", "openai": "An OpenAI-compatible server", "openrouter": "OpenRouter"}
ANTHROPIC_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5-20251001")
# max_tokens is a ceiling, not a cost (only generated tokens count): high enough that a large spec is never cut off
DEFAULTS = {"provider": "none", "model": "", "base_url": "", "api_key": "", "max_tokens": 32768,
            "daily_tokens_per_user": 400_000, "timeout": 300}
MAX_TURNS = 24
MAX_CHARS = 60_000
FIX_ROUNDS = 2
YAML_BLOCK = re.compile(r"```(?:yaml|yml)\s*\n(.*?)```", re.S | re.I)
SPEC_BLOCK = re.compile(r"```[ \t]*\n(\s*apiVersion:.*?)```", re.S)     # a spec in a fence without a language
SPEC_TEXT = re.compile(r"^\s*apiVersion:", re.M)
QUESTIONS_BLOCK = re.compile(r"```questions\s*\n(.*?)```", re.S)
MAX_QUESTIONS, MAX_OPTIONS = 12, 10
# Anthropic models that take adaptive thinking and an effort level (older ones get no thinking parameter)
ADAPTIVE = re.compile(r"claude-(opus|sonnet|fable|mythos)-(5|4-[678])")
FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")   # server-side refusal fallbacks (Claude API only)


@dataclass
class Reply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    truncated: bool = False             # the answer stopped at a length limit (max_tokens or the context window)
    reasoning: str = ""                 # the model's thinking, when the provider streams it separately
    context_full: bool = False          # the provider said the context window, not max_tokens, ran out
    tool_calls: list = field(default_factory=list)   # [{id, name, input}]; input None when it was not valid JSON
    raw: list | None = None             # Anthropic: the content blocks, replayed verbatim within a turn (thinking
                                        # blocks are bound to the conversation that produced them)


CONTEXT_ERROR = re.compile(r"exceed\w*[_ ]context|context[_ ](size|length|window)|n_ctx", re.I)
CONTEXT_HINT = ("Give the model a larger context window: in LM Studio load it with a Context Length of at least 32,768 - "
                "and set that as the model's default, or LM Studio loads it with a small one after a restart.")
MAX_TOTAL = 1140             # seconds for one answer, under the web proxy's 20 minutes for a chat request
TURN_MAX_TOTAL = 3600       # one answer in a background chat turn (no request waits on it)


class Provider:
    """Answers are streamed: `timeout` is how long the model may stay silent, so slow local models that think
    for minutes still work, while a stuck server is noticed."""
    name = "none"

    def __init__(self, model: str, api_key: str = "", base_url: str = "", max_tokens: int = DEFAULTS["max_tokens"],
                 timeout: int = 300):
        self.model, self.api_key, self.base_url = model, api_key, base_url.rstrip("/")
        self.max_tokens, self.timeout = max_tokens, timeout
        self.max_total = MAX_TOTAL     # chat turns run in the background and allow longer

    def complete(self, system: str, messages: list[dict], tools: list[dict] | None = None,
                 on_delta=None) -> Reply:   # pragma: no cover - interface
        """messages use VALOR's neutral format: {role: user|assistant, content}, an assistant turn may carry
        tool_calls (and raw), and {role: tool, results: [{id, name, content, error}]} answers them.
        tools: [{name, description, input_schema}]. on_delta(kind, text) streams "thinking", "text" and "tool"."""
        raise NotImplementedError

    def context_length(self) -> int | None:
        return None

    def _events(self, url: str, headers: dict, body: dict):
        """POST with stream=true; yields the JSON payload of every server-sent event."""
        where = urlparse(url).netloc
        try:
            r = requests.post(url, headers=headers, json=body, timeout=(10, self.timeout), stream=True)
        except requests.ConnectTimeout:
            raise ValorError("ai_unreachable", f"VALOR could not reach {where}: nothing answered (connection timed out)",
                             hint="Usually a firewall on the model server's computer drops the connection, or the "
                                  "server listens only on localhost (LM Studio: Developer -> 'Serve on Local Network'). "
                                  "Allow the port from the VALOR VM.")
        except requests.ConnectionError:
            raise ValorError("ai_unreachable", f"VALOR could not connect to {where} (refused or no route)",
                             hint="Check that the model server is running, listens on the network (not only on "
                                  "localhost) and uses that port.")
        except requests.ReadTimeout:
            raise self._silent(where)
        except requests.RequestException as e:
            raise ValorError("ai_unreachable", f"the AI provider did not answer: {e.__class__.__name__}")
        self._check_status(r, url)
        r.encoding = "utf-8"            # event streams are UTF-8; without a charset requests would assume ISO-8859-1
        t0 = time.time()
        try:
            for line in r.iter_lines(decode_unicode=True):
                if time.time() - t0 > self.max_total:
                    raise ValorError("ai_unreachable", f"{where} was still answering after {self.max_total // 60} minutes",
                                     hint="Lower 'Max tokens per answer' or use a faster model.")
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except ValueError:
                    continue
        except (requests.ReadTimeout, requests.ConnectionError) as e:
            if isinstance(e, requests.ConnectionError) and "Read timed out" not in str(e):
                raise ValorError("ai_unreachable", f"{where} closed the connection in the middle of the answer")
            raise self._silent(where)
        finally:
            r.close()

    def _silent(self, where: str) -> ValorError:
        return ValorError("ai_unreachable", f"{where} sent nothing for {self.timeout} s",
                          hint="The model may be loading or very slow: raise the timeout in Settings -> AI providers, "
                               "or use a smaller model.")

    @staticmethod
    def _check_status(r, url: str) -> None:
        if r.status_code < 400:
            return
        detail = ""
        try:
            detail = json.dumps(r.json().get("error", ""))[:300]
        except ValueError:
            pass
        if r.status_code == 404:
            raise ValorError("ai_error", f"{url} does not exist on the provider (HTTP 404)",
                             hint="For OpenAI-compatible servers the base URL usually ends in /v1 "
                                  "(e.g. http://192.168.1.50:1234/v1); also check the model name.")
        if r.status_code in (401, 403):
            raise ValorError("ai_auth", "the AI provider rejected the API key", hint="An administrator can update it in Settings.")
        if r.status_code == 429:
            raise ValorError("ai_rate_limited", "the AI provider is rate limiting VALOR; try again in a minute")
        if r.status_code == 400 and CONTEXT_ERROR.search(detail):
            raise ValorError("ai_context_full", f"the model's context window is too small: {detail}", hint=CONTEXT_HINT)
        if r.status_code == 402:                # OpenRouter: the credits cannot cover max_tokens of this model
            raise ValorError("ai_credits", f"the AI provider wants more credits for this request {detail}".strip(),
                             hint="Add credits, or lower 'Max tokens per answer' for this provider in Settings -> "
                                  "AI providers (the provider reserves the whole maximum up front).")
        raise ValorError("ai_error", f"the AI provider answered HTTP {r.status_code} {detail}".strip(),
                         details={"status": r.status_code, "detail": detail})


class Anthropic(Provider):
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def request_body(self, system: str, messages: list[dict], tools: list[dict] | None) -> tuple[dict, dict]:
        model = self.model or ANTHROPIC_MODELS[0]
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        # the system prompt is large and the same for a whole conversation: cache it
        body = {"model": model, "max_tokens": self.max_tokens, "stream": True,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": anthropic_messages(messages)}
        if tools:
            body["tools"] = [{**t, "eager_input_streaming": True} for t in tools]
        if ADAPTIVE.match(model):
            body["thinking"] = {"type": "adaptive", "display": "summarized"}     # readable reasoning for the feed
            body["output_config"] = {"effort": "high"}
        elif model.startswith("claude-haiku-4-5") and self.max_tokens > 4096:
            body["thinking"] = {"type": "enabled", "budget_tokens": min(16000, self.max_tokens // 2)}
        if model in FALLBACK_MODELS and not self.base_url:
            # a lab-range builder talks about attacks and vulnerable services: when a safety classifier declines,
            # let the API answer with the fallback model it picks instead of failing the request
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
            body["fallbacks"] = "default"
        return headers, body

    def complete(self, system: str, messages: list[dict], tools: list[dict] | None = None, on_delta=None) -> Reply:
        headers, body = self.request_body(system, messages, tools)
        emit = on_delta or (lambda kind, text: None)
        blocks: dict[int, dict] = {}
        tin = tout = 0
        stop, refusal = None, None
        for ev in self._events(self.base_url + "/v1/messages" if self.base_url else self.URL, headers, body):
            kind = ev.get("type")
            if kind == "message_start":
                u = (ev.get("message") or {}).get("usage") or {}
                tin = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                       "cache_creation_input_tokens"))
            elif kind == "content_block_start":
                b = dict(ev.get("content_block") or {})
                if b.get("type") == "tool_use":
                    b["_json"] = ""
                    emit("tool", b.get("name", ""))
                blocks[ev.get("index", len(blocks))] = b
            elif kind == "content_block_delta":
                d, b = ev.get("delta") or {}, blocks.get(ev.get("index"))
                if b is None:
                    continue
                dt = d.get("type")
                if dt == "text_delta":
                    b["text"] = b.get("text", "") + d.get("text", "")
                    emit("text", d.get("text", ""))
                elif dt == "thinking_delta":
                    b["thinking"] = b.get("thinking", "") + d.get("thinking", "")
                    emit("thinking", d.get("thinking", ""))
                elif dt == "signature_delta":
                    b["signature"] = b.get("signature", "") + d.get("signature", "")
                elif dt == "input_json_delta":
                    b["_json"] += d.get("partial_json", "")
            elif kind == "message_delta":
                delta = ev.get("delta") or {}
                stop = delta.get("stop_reason") or stop
                refusal = delta.get("stop_details") or refusal
                tout = int((ev.get("usage") or {}).get("output_tokens", tout))
            elif kind == "error":
                raise ValorError("ai_error", f"the AI provider reported an error: {json.dumps(ev.get('error'))[:300]}")
        if stop == "refusal":
            cat = (refusal or {}).get("category") if isinstance(refusal, dict) else None
            raise ValorError("ai_refused", "the model declined this request" + (f" ({cat})" if cat else ""),
                             hint="Rephrase the request (say it is an isolated training lab), or pick another model in "
                                  "Settings -> AI providers.", details={"input_tokens": tin, "output_tokens": tout})
        raw, calls = [], []
        for i in sorted(blocks):
            b = blocks[i]
            if b.get("type") == "tool_use":
                text = b.pop("_json")
                try:
                    args = json.loads(text) if text.strip() else {}
                    args = args if isinstance(args, dict) else None
                except ValueError:
                    args = None                 # streamed input that is not valid JSON: answered as an error
                b["input"] = args if args is not None else {}
                calls.append({"id": b.get("id"), "name": b.get("name"), "input": args})
            raw.append(b)
        text = "".join(b.get("text", "") for b in raw if b.get("type") == "text")
        thinking = "\n".join(b.get("thinking", "") for b in raw if b.get("type") == "thinking" and b.get("thinking"))
        return Reply(text, tin, tout, truncated=stop in ("max_tokens", "model_context_window_exceeded"),
                     reasoning=thinking, context_full=stop == "model_context_window_exceeded",
                     tool_calls=calls, raw=raw)


def anthropic_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if m["role"] == "tool":
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": r["id"], "content": r.get("content") or "(no output)",
                 **({"is_error": True} if r.get("error") else {})} for r in m["results"]]})
        elif m["role"] == "assistant" and (m.get("raw") or m.get("tool_calls")):
            blocks = m.get("raw") or (([{"type": "text", "text": m["content"]}] if m.get("content") else []) +
                                      [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c.get("input") or {}}
                                       for c in m["tool_calls"]])
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


class OpenAICompatible(Provider):
    name = "openai"
    extra_headers: dict = {}

    def complete(self, system: str, messages: list[dict], tools: list[dict] | None = None, on_delta=None) -> Reply:
        if not self.base_url:
            raise ValorError("ai_not_configured", "the OpenAI-compatible provider needs a base URL")
        headers = {"content-type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        body = {"model": self.model, "max_tokens": self.max_tokens, "stream": True,
                "stream_options": {"include_usage": True}, "messages": openai_messages(system, messages)}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                               "parameters": t["input_schema"]}} for t in tools]
        url = self.base_url + "/chat/completions"
        try:
            return self._read(self._events(url, headers, body), on_delta)
        except ValorError as e:
            detail = e.details.get("detail", "") if isinstance(e.details, dict) else ""
            if isinstance(e.details, dict) and e.details.get("status") == 400 and "stream_options" in detail:
                body.pop("stream_options")      # servers that don't know it: no token counts, but an answer
                return self._read(self._events(url, headers, body), on_delta)
            if tools and isinstance(e.details, dict) and e.details.get("status") == 400 and "tool" in detail.lower():
                raise ValorError("ai_no_tools", "the model server does not accept tools for this model",
                                 hint="Question and Code mode on a range use tools (commands in the VMs): pick a model "
                                      "with tool use, e.g. Qwen 3, Llama 3.1+ or Mistral Small, or use Plan mode.")
            raise

    def context_length(self) -> int | None:
        """The context window the server loaded the model with, when it says (LM Studio's REST API does)."""
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            r = requests.get(f"{root}/api/v0/models/{self.model}", headers=headers, timeout=10)
            info = r.json() if r.ok else {}
        except (requests.RequestException, ValueError):
            return None
        n = info.get("loaded_context_length")      # not max_context_length: the model's limit, not what is loaded
        return int(n) if isinstance(n, int) else None

    @staticmethod
    def _read(events, on_delta=None) -> Reply:
        text, thinking, tin, tout, finish = [], [], 0, 0, None
        calls: dict[int, dict] = {}
        split = ThinkSplitter(on_delta or (lambda kind, text: None))
        for ev in events:
            if ev.get("error"):                 # a failure after the stream started (OpenRouter, vLLM) arrives as an event
                err = ev["error"]
                msg = str(err.get("message") or err.get("code") or err) if isinstance(err, dict) else str(err)
                if CONTEXT_ERROR.search(msg):   # LM Studio / llama.cpp: the prompt does not fit the loaded context
                    raise ValorError("ai_context_full", f"the model's context window is too small: {msg[:300]}",
                                     hint=CONTEXT_HINT)
                raise ValorError("ai_error", f"the model provider reported an error in the middle of the answer: {msg[:300]}",
                                 hint="Usually temporary: send the message again, or pick another model or provider.")
            for ch in ev.get("choices") or []:
                delta = ch.get("delta") or {}
                piece = delta.get("content") or ""
                text.append(piece)
                split.feed(piece)
                # reasoning models behind LM Studio, vLLM, llama.cpp: separate field (name varies by server)
                r = delta.get("reasoning_content") or delta.get("reasoning") or ""
                thinking.append(r)
                if r and on_delta:
                    on_delta("thinking", r)
                for tc in delta.get("tool_calls") or []:
                    c = calls.setdefault(int(tc.get("index", len(calls))), {"id": "", "name": "", "args": ""})
                    fn = tc.get("function") or {}
                    c["id"] = tc.get("id") or c["id"]
                    if fn.get("name") and not c["name"]:
                        c["name"] = fn["name"]
                        if on_delta:
                            on_delta("tool", c["name"])
                    c["args"] += fn.get("arguments") or ""
                finish = ch.get("finish_reason") or finish
            u = ev.get("usage") or {}
            if u:
                tin, tout = int(u.get("prompt_tokens", tin)), int(u.get("completion_tokens", tout))
        content, inline = split_thinking("".join(text))
        tool_calls = [{"id": c["id"] or f"call_{secrets.token_hex(6)}", "name": c["name"], "input": _args(c["args"])}
                      for _, c in sorted(calls.items()) if c["name"]]
        if not tool_calls:
            content, tool_calls = text_tool_calls(content)
        return Reply(content, tin, tout, truncated=finish == "length", reasoning="".join(thinking) + inline,
                     tool_calls=tool_calls)


class OpenRouter(OpenAICompatible):
    """OpenRouter: one key for many hosted models, through its OpenAI-compatible API."""
    name = "openrouter"
    BASE_URL = "https://openrouter.ai/api/v1"
    extra_headers = {"X-Title": "VALOR"}                # the app name in the account's activity list

    def __init__(self, model: str, api_key: str = "", base_url: str = "", **kw):
        super().__init__(model, api_key, self.BASE_URL, **kw)

    def context_length(self) -> int | None:
        """The model's context window from OpenRouter's public model list."""
        try:
            r = requests.get(f"{self.BASE_URL}/models", timeout=15)
            models = r.json().get("data", []) if r.ok else []
        except (requests.RequestException, ValueError, AttributeError):
            return None
        n = next((m.get("context_length") for m in models if isinstance(m, dict) and m.get("id") == self.model), None)
        return int(n) if isinstance(n, int) else None


def _args(text: str) -> dict | None:
    try:
        v = json.loads(text) if text.strip() else {}
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


TOOL_CALL_TAG = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def text_tool_calls(text: str) -> tuple[str, list[dict]]:
    """Tool calls a model wrote into its text (Qwen/Hermes <tool_call> tags) when the server did not parse them."""
    calls = []
    for m in TOOL_CALL_TAG.finditer(text):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("name"):
            args = d.get("arguments", d.get("parameters", {}))
            if isinstance(args, str):
                args = _args(args)
            calls.append({"id": f"call_{secrets.token_hex(6)}", "name": str(d["name"]),
                          "input": args if isinstance(args, dict) else None})
    return (TOOL_CALL_TAG.sub("", text).strip(), calls) if calls else (text, [])


def openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "tool":
            out += [{"role": "tool", "tool_call_id": r["id"], "content": r.get("content") or "(no output)"}
                    for r in m["results"]]
        elif m["role"] == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": json.dumps(c.get("input") or {})}}
                for c in m["tool_calls"]]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


class ThinkSplitter:
    """Streams text that may start with inline <think>...</think> reasoning: reasoning goes out as "thinking"."""

    def __init__(self, emit):
        self.emit, self.buf, self.state = emit, "", "start"      # start -> think -> text

    def feed(self, piece: str) -> None:
        if not piece:
            return
        if self.state == "text":
            self.emit("text", piece)
            return
        self.buf += piece
        if self.state == "start":
            stripped = self.buf.lstrip()
            if "<think>".startswith(stripped) and len(stripped) < 7:
                return                          # could still become <think>
            if not stripped.startswith("<think>"):
                self.state = "text"
                self.emit("text", self.buf)
                self.buf = ""
                return
            self.state, self.buf = "think", stripped[7:]
        if "</think>" in self.buf:
            thought, rest = self.buf.split("</think>", 1)
            if thought:
                self.emit("thinking", thought)
            self.state, self.buf = "text", ""
            if rest.lstrip():
                self.emit("text", rest.lstrip())
        elif len(self.buf) > 16:                # keep a tail in case "</think>" is split across pieces
            self.emit("thinking", self.buf[:-8])
            self.buf = self.buf[-8:]


def provider_from(settings: dict, api_key: str) -> Provider:
    kind = settings.get("provider", "none")
    args = dict(model=settings.get("model", ""), api_key=api_key, base_url=settings.get("base_url", ""),
                max_tokens=int(settings.get("max_tokens", DEFAULTS["max_tokens"])),
                timeout=int(settings.get("timeout", DEFAULTS["timeout"])))
    if kind == "anthropic":
        return Anthropic(**args)
    if kind == "openai":
        return OpenAICompatible(**args)
    if kind == "openrouter":
        return OpenRouter(**args)
    raise ValorError("ai_not_configured", "No AI provider is connected.",
                     hint="An administrator can connect one in Settings -> AI providers.")


# ---------------------------------------------------------------------- prompt
def spec_reference(cfg) -> str:
    for p in (Path(cfg.content_dir or cfg.project_dir) / "spec-reference.md",
              Path(cfg.project_dir) / ".claude" / "skills" / "range-build" / "spec-reference.md"):
        if p.is_file():
            return p.read_text()
    return ""


def reference(cfg, catalog: dict, present: set[str], roles: list[dict], facts: dict) -> str:
    """What every VALOR prompt needs: the spec format, the OSes and roles this cluster has, and its facts."""
    oses = "\n".join(f"- {name}{' (default)' if name == cfg.default_os else ''}: {e.get('description', '')} "
                     f"[{e.get('family', 'debian')}]" for name, e in catalog.items() if name in present)
    role_lines = []
    for r in roles:
        params = "; ".join(f"{k}{'' if not v.get('required') else ' (required)'}: {' '.join(v.get('description', '').split())}"
                           + (f" (default {v['default']!r})" if 'default' in v else "")
                           for k, v in (r.get("params") or {}).items())
        ports = (f"TCP ports from its '{r['ports_param']}' param" if r.get("ports_param")
                 else "TCP " + ", ".join(map(str, r["ports"])) if r.get("ports") else "")
        role_lines.append(f"- {r['name']} [{', '.join(r.get('families') or ['debian'])}]"
                          f"{' (generic)' if r.get('category') == 'generic' else ''}: {' '.join(r['description'].split())}"
                          + (f" | serves: {ports}" if ports else "") + (f" | params: {params}" if params else ""))
    frameworks = compliance.reference(Path(cfg.baselines_dir))
    return f"""# Spec reference
{spec_reference(cfg)}

# Compliance frameworks (compliance.frameworks)
{frameworks or '- (none available)'}

# Available operating systems (templates on this cluster)
{oses or '- (none built yet)'}

# Roles
{chr(10).join(role_lines)}

# Cluster facts
{json.dumps(facts, indent=1, sort_keys=True)}
"""


SPEC_RULES = """Rules for specs:
- Follow the spec reference below exactly; the spec is validated and you will be told about any problem.
- Use only operating systems from the list of available templates and roles from the role list; a role only works
  on the OS families shown in brackets.
- Never put passwords, keys or other secrets in the spec (VALOR generates logins itself).
- Keep a spec you were given and change only what the person asks for; always return the whole spec.
- Deny by default: only add policy rules the environment needs, and say which flows you allowed.
- Pick unused VLANs and private networks that avoid the networks listed under cluster facts.
- Leave `baseline` out (or at ubuntu-l1): VALOR hardens Linux hosts with it and Windows hosts with windows-l1
  automatically. Never set a Windows profile as baseline, and only use 'none' when the person asks for no hardening.
- Compliance: when the person wants a framework, set `compliance.frameworks` and `baseline: linux-moderate`, list the
  segments holding the regulated data in `compliance.scope` (no internet there), add a log server (syslog-server
  role) with syslog-client on the other Linux hosts and a time server (ntp-server, ntp-client elsewhere). Say the
  range is "aligned with" the framework's technical requirements, never "compliant" or "certified".
- If the request is unclear, make a sensible small choice and say what you assumed."""


def system_prompt(cfg, catalog: dict, present: set[str], roles: list[dict], facts: dict) -> str:
    return builder_prompt(reference(cfg, catalog, present, roles, facts))


QUESTIONS_FORMAT = """To ask the person questions, end your answer with one ```questions block (YAML) and nothing after it.
VALOR shows each question as a card with clickable options plus a free-text "Other" answer:
```questions
- header: Compliance                 # a short label (optional)
  question: Should the range be built toward a compliance framework?
  options: [NIST SP 800-171 (CUI / CMMC L2), NIST SP 800-53 Moderate, PCI DSS, HIPAA, CIS Level 1 only, None]
  recommended: None                  # optional; one of the options
- question: Which services should the web tier run?
  options: [nginx, Apache, MariaDB, PostgreSQL, Juice Shop (vulnerable app)]
  multiple: true                     # several answers allowed
```
At most 12 questions per round, 2-10 short options each. The answers come back in the person's next message."""


def builder_prompt(ref: str) -> str:
    return f"""You design cyber ranges (isolated lab networks of virtual machines) for VALOR, which builds them on
Proxmox VE. The person describes an environment; you find out what they need, then write the range spec.

Asking first: unless the person already answered everything that matters, or asks you to just build it / use your
defaults, reply to a new request with questions instead of a spec - as many as are useful, formed from what they
described: what the range is for (training, red or blue team, testing, a demo), whether to build it toward a compliance
framework (offer NIST SP 800-171 / CMMC L2, NIST SP 800-53 Low or Moderate, PCI DSS, HIPAA, CIS Level 1 only, or none),
which services each server runs and how they are configured (web, database, DNS, DHCP, file shares, mail, logging,
time, Active Directory, containers, deliberately vulnerable apps), operating systems, how many clients, which segments
get internet access, remote access (WireGuard) and who may reach what. Ask again in later rounds while something
important is still open, but never ask what they already told you.

Writing the spec: answer with a short explanation (a few sentences: what you built and any assumption you made)
followed by exactly one complete range spec in a ```yaml code block. When the person asks something else (about the
spec you wrote, VALOR, a choice you made), answer in plain text without a spec.

{QUESTIONS_FORMAT}

{SPEC_RULES}

{ref}"""


RANGE_MODES = {
    "question": """You answer questions about one VALOR range (shown below with its live state). Answer in plain,
concise text: what the range contains, who can reach what under its policy, why a test failed, how to use a service.
You may look inside the VMs with read-only commands (run_command, read_file, range_status) to check facts; you cannot
change anything in this mode. If the person wants a change, tell them to switch to Plan or Code mode.""",
    "plan": f"""You plan changes to one VALOR range (shown below with its live state). Start with a short plan: a few
bullet points saying what you change and why, and which traffic you allow. Then give the complete updated spec in
exactly one ```yaml code block. VALOR shows it as a draft on the map; nothing is built until a person approves the plan.

{SPEC_RULES}""",
    "code": f"""You operate one VALOR range (shown below with its spec and live state) for the person: you run commands
inside its VMs over the QEMU guest agent (bash as root on Linux, PowerShell as SYSTEM on Windows), read and write files,
power VMs, take and roll back snapshots, and record changes in the range's spec.

How to work:
- Look first: read-only commands (status, cat, ss, Get-Service...) run at once. Anything that changes a VM, power and
  snapshot actions and spec changes wait for the person to approve a card in the chat - so batch related changes into
  one command or script, and say what it does in `purpose`.
- Configure services the way a role would (packages, config files, enabled services, firewall ports), then check that
  they work (service status, listening ports, a request from another host when the policy allows it).
- Keep the range reproducible: once a live change works, call update_spec with the complete spec that records it - a
  ready-made role when one fits, otherwise packages / windows-features / custom-script with the same commands - so a
  rebuild gives the same result. Network changes (segments, policy, internet access) are made in the spec only; VALOR
  builds them after the person approves the plan.
- Hosts reach only what the policy allows, and segments without internet access cannot download packages; say so and
  propose a spec change instead of working around the policy.
- If something is unclear, ask (questions block below) and stop. Finish with a short summary of what you changed and
  how you verified it.

{QUESTIONS_FORMAT}

{SPEC_RULES}""",
}


def range_prompt(mode: str, ref: str, spec_yaml: str, live: dict) -> str:
    return f"""{RANGE_MODES[mode]}

# The range's current spec{' (with unsaved draft changes)' if live.get('draft') else ''}
```yaml
{spec_yaml.strip()}
```

# Live state
{json.dumps(live, indent=1, sort_keys=True, default=str)}

{ref}"""


QUESTION_ITEM = re.compile(r"^-[ \t]+(?:header|question)[ \t]*:", re.M)
# what is left of a mangled opening fence before such a list: "```questions", "```yaml", "...below:questions"
QUESTIONS_LABEL = re.compile(r"(?:```[ \t]*(?:questions|ya?ml)?|(?<=[^\w\s])questions|(?:^|\n)[ \t]*questions)[ \t]*\Z")


def extract_questions(text: str) -> tuple[list[dict] | None, str]:
    """(the questions of the last ```questions block, normalized; the text without the block). A list of questions
    that ends the answer counts too when the model dropped or mangled the opening fence (seen with Mistral)."""
    blocks = QUESTIONS_BLOCK.findall(text)
    if blocks:
        out = _questions(blocks[-1])
        return (None, text) if out is None else ((out or None), QUESTIONS_BLOCK.sub("", text).strip())
    m = QUESTION_ITEM.search(text)
    if not m:
        return None, text
    body = re.sub(r"\n[ \t]*```[ \t]*\Z", "", text[m.start():].rstrip())
    out = _questions(body) if "```" not in body else None
    if not out:
        return None, text                       # shown as written
    return out, QUESTIONS_LABEL.sub("", text[:m.start()].rstrip()).rstrip()


def _questions(block: str) -> list[dict] | None:
    """The normalized questions of a questions block (None: not YAML)."""
    try:
        data = yaml.safe_load(repair_yaml(block))
    except yaml.YAMLError:
        return None
    if isinstance(data, dict):
        data = data.get("questions")
    out = []
    for q in data if isinstance(data, list) else []:
        if isinstance(q, str):
            q = {"question": q}
        if not isinstance(q, dict) or not str(q.get("question") or "").strip():
            continue
        opts = q.get("options") if isinstance(q.get("options"), list) else []
        options = list(dict.fromkeys(str(o).strip()[:200] for o in opts if str(o).strip()))[:MAX_OPTIONS]
        rec = str(q.get("recommended") or "").strip()
        out.append({"id": len(out) + 1, "header": str(q.get("header") or "").strip()[:40],
                    "question": str(q["question"]).strip()[:500], "options": options,
                    "multiple": bool(q.get("multiple") or q.get("multi")),
                    "recommended": rec if rec in options else None})
        if len(out) == MAX_QUESTIONS:
            break
    return out


def extract_yaml(text: str) -> tuple[str | None, str]:
    """(the last ```yaml block, repaired; the text without it)."""
    fence = YAML_BLOCK if YAML_BLOCK.search(text) else SPEC_BLOCK
    blocks = fence.findall(text)
    if not blocks:
        return None, text.strip()
    return repair_yaml(blocks[-1].strip() + "\n"), fence.sub("", text).strip()


PLAIN_VALUE = re.compile(r"^(\s*(?:-\s+)?[A-Za-z_][\w-]*:\s+)([^\s\"'\[{|>&*!#%@`].*?)(\s+#.*)?\s*$")


def repair_yaml(text: str) -> str:
    """Quote the plain values that contain ": " (`description: Data tier: files`), the YAML slip models make most:
    a whole fix round with a slow local model otherwise. Only lines YAML rejects change, and only if it then parses."""
    lines = text.split("\n")
    for _ in range(50):
        try:
            yaml.safe_load("\n".join(lines))
            return "\n".join(lines)
        except yaml.MarkedYAMLError as e:
            n = e.problem_mark.line if e.problem_mark else -1
            m = PLAIN_VALUE.match(lines[n]) if "mapping values are not allowed" in (e.problem or "") and \
                0 <= n < len(lines) else None
            if not m:
                return text
            lines[n] = m[1] + json.dumps(m[2], ensure_ascii=False) + (m[3] or "")
        except yaml.YAMLError:
            return text
    return text


def trim(messages: list[dict]) -> list[dict]:
    """The newest turns that fit the size limits, starting with a user turn."""
    out, size = [], 0
    for m in reversed(messages[-MAX_TURNS:]):
        size += len(m["content"])
        if size > MAX_CHARS:
            break
        out.append(m)
    out.reverse()
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


THINK = re.compile(r"<think>(.*?)(?:</think>|$)", re.S)


def split_thinking(text: str) -> tuple[str, str]:
    """(answer, reasoning): models such as Qwen and DeepSeek put their reasoning inline in <think> tags."""
    found = THINK.findall(text)
    return THINK.sub("", text).strip() if found else text, "\n".join(found)


def cut_off(provider: Provider, r: Reply) -> ValorError:
    """Why an answer stopped early. Servers say "length" both for max_tokens and for a full context window (a local
    model loaded with a small context fills it with VALOR's prompt and stops at once), so tell them apart."""
    details = {"input_tokens": r.input_tokens, "output_tokens": r.output_tokens}
    early = r.output_tokens < provider.max_tokens * 0.9 if r.output_tokens else not (r.text or r.reasoning)
    if r.context_full or early:
        size = f" (VALOR's request is {r.input_tokens:,} tokens)" if r.input_tokens else ""
        need = max(32768, (r.input_tokens or 0) + 16384)
        hint = (f"Pick a model with a larger context window or answer limit (at least {need:,} tokens) on OpenRouter."
                if provider.name == "openrouter" else
                f"Give the model a larger context window: in LM Studio load it with a Context Length of at least "
                f"{need:,} (or pick a model that supports it). Raising 'Max tokens per answer' does not help here.")
        return ValorError("ai_context_full",
                          f"the model stopped after {r.output_tokens:,} tokens because its context window is full{size}",
                          hint=hint, details=details)
    thought = f", {len(r.reasoning) // 4:,} of them thinking" if len(r.reasoning) > 400 else ""
    return ValorError("ai_truncated", f"the model's answer was cut off at {provider.max_tokens:,} tokens{thought}",
                      hint="Raise 'Max tokens per answer' in Settings -> AI providers (it is a ceiling: only the tokens "
                           "the model writes count).", details=details)


def _complete(provider: Provider, system: str, convo: list[dict], on_delta) -> Reply:
    # providers written before streaming callbacks (tests' fakes) take (system, messages) only
    return provider.complete(system, convo, on_delta=on_delta) if on_delta else provider.complete(system, convo)


def ask(provider: Provider, system: str, messages: list[dict], on_delta=None) -> dict:
    """One answer, no spec (question mode)."""
    convo = trim(messages)
    if not convo:
        raise ValorError("ai_no_message", "Ask something.")
    r = _complete(provider, system, convo, on_delta)
    if r.truncated:
        raise cut_off(provider, r)
    questions, text = extract_questions(r.text.strip())
    return {"reply": text, "questions": questions, "problems": [], "reasoning": r.reasoning,
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens}


def build(provider: Provider, system: str, messages: list[dict], check, on_delta=None, on_check=None) -> dict:
    """Ask the model; validate its spec with check(yaml) -> list of problems; ask for fixes (FIX_ROUNDS times).
    An answer with questions and no spec ends the round (the person answers first).
    Returns {reply, yaml, questions, problems, attempts, fixed (the problems the model corrected), reasoning,
    input_tokens, output_tokens}. on_check(problems) is told about every failed check."""
    convo = trim(messages)
    if not convo:
        raise ValorError("ai_no_message", "Say what environment you want.")
    used_in = used_out = 0
    reply = yaml_text = None
    questions = None
    problems: list[str] = []
    fixed: list[str] = []                   # problems found in earlier attempts (the model corrected them)
    reasoning: list[str] = []
    for attempt in range(1, FIX_ROUNDS + 2):
        r = _complete(provider, system, convo, on_delta)
        used_in += r.input_tokens
        used_out += r.output_tokens
        if r.reasoning:
            reasoning.append(r.reasoning)
        if r.truncated:                 # asking again would be cut off at the same place
            e = cut_off(provider, r)
            e.details = {"input_tokens": used_in, "output_tokens": used_out}
            raise e
        questions, text = extract_questions(r.text)
        yaml_text, reply = extract_yaml(text)
        if yaml_text is None and (questions or not SPEC_TEXT.search(text)):
            problems = []               # questions, or a plain answer to something that asked for no spec
            break
        if yaml_text is None:
            problems = ["the spec is not in a ```yaml block"]
        else:
            problems = check(yaml_text)
        if not problems:
            break
        fixed += problems
        if on_check and attempt <= FIX_ROUNDS:
            on_check(problems)
        convo = [*convo, {"role": "assistant", "content": r.text},
                 {"role": "user", "content": "[Automatic check by VALOR - the person does not see this message or your "
                  "previous answer.] VALOR could not use that spec:\n" + "\n".join(f"- {p}" for p in problems)
                  + "\nWrite your answer to the person's request again, as if for the first time: a short explanation "
                  "of the environment (do not apologize or mention this check), then the corrected complete spec in "
                  "one ```yaml block."}]
    return {"reply": reply, "yaml": yaml_text, "questions": questions, "problems": problems, "attempts": attempt,
            "fixed": fixed, "reasoning": "\n\n".join(reasoning), "input_tokens": used_in, "output_tokens": used_out}
