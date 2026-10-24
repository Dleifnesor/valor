"""Talking to AI models: one streamed request, its answer as a Reply (text, reasoning, tool calls, token counts).

Two kinds of provider: any OpenAI-compatible server (LM Studio, vLLM, Ollama's OpenAI API, ...) and OpenRouter.
Local models' quirks are smoothed over here: reasoning inside <think> tags, tool calls written as text, and a full
context window that looks like a cut-off answer.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

from ..errors import ValorError

PROVIDERS = ("openai", "openrouter")
KIND_NAMES = {"openai": "An OpenAI-compatible server", "openrouter": "OpenRouter"}
# max_tokens is a ceiling, not a cost (only generated tokens count): high enough that a large spec is never cut off
DEFAULTS = {"provider": "none", "model": "", "base_url": "", "api_key": "", "max_tokens": 32768,
            "daily_tokens_per_user": 400_000, "timeout": 300}


@dataclass
class Reply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    truncated: bool = False             # the answer stopped at a length limit (max_tokens or the context window)
    reasoning: str = ""                 # the model's thinking, when the provider streams it separately
    context_full: bool = False          # the provider said the context window, not max_tokens, ran out
    tool_calls: list = field(default_factory=list)   # [{id, name, input}]; input None when it was not valid JSON


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
        tool_calls, and {role: tool, results: [{id, name, content, error}]} answers them.
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
    if kind == "openai":
        return OpenAICompatible(**args)
    if kind == "openrouter":
        return OpenRouter(**args)
    raise ValorError("ai_not_configured", "No AI provider is connected.",
                     hint="An administrator can connect one in Settings -> AI providers.")


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
