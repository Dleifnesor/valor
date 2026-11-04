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
            response = requests.post(url, headers=headers, json=body, timeout=(10, self.timeout), stream=True)
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
        self._check_status(response, url)
        response.encoding = "utf-8"     # event streams are UTF-8; without a charset requests would assume ISO-8859-1
        started = time.time()
        try:
            for line in response.iter_lines(decode_unicode=True):
                if time.time() - started > self.max_total:
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
            response.close()

    def _silent(self, where: str) -> ValorError:
        return ValorError("ai_unreachable", f"{where} sent nothing for {self.timeout} s",
                          hint="The model may be loading or very slow: raise the timeout in Settings -> AI providers, "
                               "or use a smaller model.")

    @staticmethod
    def _check_status(response, url: str) -> None:
        """Turn an HTTP error from the provider into a ValorError that says what to do about it."""
        status = response.status_code
        if status < 400:
            return
        detail = ""
        try:
            detail = json.dumps(response.json().get("error", ""))[:300]
        except ValueError:
            pass
        if status == 404:
            raise ValorError("ai_error", f"{url} does not exist on the provider (HTTP 404)",
                             hint="For OpenAI-compatible servers the base URL usually ends in /v1 "
                                  "(e.g. http://192.168.1.50:1234/v1); also check the model name.")
        if status in (401, 403):
            raise ValorError("ai_auth", "the AI provider rejected the API key",
                             hint="An administrator can update it in Settings.")
        if status == 429:
            raise ValorError("ai_rate_limited", "the AI provider is rate limiting VALOR; try again in a minute")
        if status == 400 and CONTEXT_ERROR.search(detail):
            raise ValorError("ai_context_full", f"the model's context window is too small: {detail}", hint=CONTEXT_HINT)
        if status == 402:                       # OpenRouter: the credits cannot cover max_tokens of this model
            raise ValorError("ai_credits", f"the AI provider wants more credits for this request {detail}".strip(),
                             hint="Add credits, or lower 'Max tokens per answer' for this provider in Settings -> "
                                  "AI providers (the provider reserves the whole maximum up front).")
        raise ValorError("ai_error", f"the AI provider answered HTTP {status} {detail}".strip(),
                         details={"status": status, "detail": detail})


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
            body["tools"] = [{"type": "function", "function": {"name": tool["name"], "description": tool["description"],
                                                               "parameters": tool["input_schema"]}} for tool in tools]
        url = self.base_url + "/chat/completions"
        try:
            return self._read(self._events(url, headers, body), on_delta)
        except ValorError as e:
            details = e.details if isinstance(e.details, dict) else {}
            detail = details.get("detail", "")
            if details.get("status") == 400 and "stream_options" in detail:
                body.pop("stream_options")      # servers that don't know it: no token counts, but an answer
                return self._read(self._events(url, headers, body), on_delta)
            if tools and details.get("status") == 400 and "tool" in detail.lower():
                raise ValorError("ai_no_tools", "the model server does not accept tools for this model",
                                 hint="Question and Code mode on a range use tools (commands in the VMs): pick a model "
                                      "with tool use, e.g. Qwen 3, Llama 3.1+ or Mistral Small, or use Plan mode.")
            raise

    def context_length(self) -> int | None:
        """The context window the server loaded the model with, when it says (LM Studio's REST API does)."""
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            response = requests.get(f"{root}/api/v0/models/{self.model}", headers=headers, timeout=10)
            info = response.json() if response.ok else {}
        except (requests.RequestException, ValueError):
            return None
        loaded = info.get("loaded_context_length")  # not max_context_length: the model's limit, not what is loaded
        return int(loaded) if isinstance(loaded, int) else None

    @staticmethod
    def _read(events, on_delta=None) -> Reply:
        """Assemble the streamed events of one answer into a Reply."""
        stream = _Stream(on_delta)
        for event in events:
            stream.add(event)
        return stream.reply()


class _Stream:
    """The pieces of one streamed answer as they arrive: text, reasoning, tool calls (their arguments arrive in
    fragments), the finish reason and the token counts."""

    def __init__(self, on_delta=None):
        self.on_delta = on_delta
        self.text: list[str] = []
        self.thinking: list[str] = []
        self.input_tokens = self.output_tokens = 0
        self.finish = None
        self.calls: dict[int, dict] = {}            # index -> {id, name, args (JSON text so far)}
        self.splitter = ThinkSplitter(on_delta or (lambda kind, text: None))

    def add(self, event: dict) -> None:
        if event.get("error"):              # a failure after the stream started (OpenRouter, vLLM) arrives as an event
            _raise_stream_error(event["error"])
        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            piece = delta.get("content") or ""
            self.text.append(piece)
            self.splitter.feed(piece)
            # reasoning models behind LM Studio, vLLM, llama.cpp: separate field (name varies by server)
            reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
            self.thinking.append(reasoning)
            if reasoning and self.on_delta:
                self.on_delta("thinking", reasoning)
            for fragment in delta.get("tool_calls") or []:
                self.add_tool_fragment(fragment)
            self.finish = choice.get("finish_reason") or self.finish
        usage = event.get("usage") or {}
        if usage:
            self.input_tokens = int(usage.get("prompt_tokens", self.input_tokens))
            self.output_tokens = int(usage.get("completion_tokens", self.output_tokens))

    def add_tool_fragment(self, fragment: dict) -> None:
        call = self.calls.setdefault(int(fragment.get("index", len(self.calls))), {"id": "", "name": "", "args": ""})
        function = fragment.get("function") or {}
        call["id"] = fragment.get("id") or call["id"]
        if function.get("name") and not call["name"]:
            call["name"] = function["name"]
            if self.on_delta:
                self.on_delta("tool", call["name"])
        call["args"] += function.get("arguments") or ""

    def reply(self) -> Reply:
        content, inline_thinking = split_thinking("".join(self.text))
        tool_calls = [{"id": call["id"] or f"call_{secrets.token_hex(6)}", "name": call["name"],
                       "input": _args(call["args"])}
                      for _, call in sorted(self.calls.items()) if call["name"]]
        if not tool_calls:                  # the server didn't parse them: maybe the model wrote them as text
            content, tool_calls = text_tool_calls(content)
        return Reply(content, self.input_tokens, self.output_tokens, truncated=self.finish == "length",
                     reasoning="".join(self.thinking) + inline_thinking, tool_calls=tool_calls)


def _raise_stream_error(error) -> None:
    message = str(error.get("message") or error.get("code") or error) if isinstance(error, dict) else str(error)
    if CONTEXT_ERROR.search(message):       # LM Studio / llama.cpp: the prompt does not fit the loaded context
        raise ValorError("ai_context_full", f"the model's context window is too small: {message[:300]}",
                         hint=CONTEXT_HINT)
    raise ValorError("ai_error", f"the model provider reported an error in the middle of the answer: {message[:300]}",
                     hint="Usually temporary: send the message again, or pick another model or provider.")


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
            response = requests.get(f"{self.BASE_URL}/models", timeout=15)
            models = response.json().get("data", []) if response.ok else []
        except (requests.RequestException, ValueError, AttributeError):
            return None
        length = next((model.get("context_length") for model in models
                       if isinstance(model, dict) and model.get("id") == self.model), None)
        return int(length) if isinstance(length, int) else None


def _args(text: str) -> dict | None:
    """A tool call's arguments: a JSON object, {} when empty, None when the model wrote invalid JSON."""
    try:
        value = json.loads(text) if text.strip() else {}
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


TOOL_CALL_TAG = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def text_tool_calls(text: str) -> tuple[str, list[dict]]:
    """Tool calls a model wrote into its text (Qwen/Hermes <tool_call> tags) when the server did not parse them."""
    calls = []
    for match in TOOL_CALL_TAG.finditer(text):
        try:
            call = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(call, dict) and call.get("name"):
            args = call.get("arguments", call.get("parameters", {}))
            if isinstance(args, str):
                args = _args(args)
            calls.append({"id": f"call_{secrets.token_hex(6)}", "name": str(call["name"]),
                          "input": args if isinstance(args, dict) else None})
    return (TOOL_CALL_TAG.sub("", text).strip(), calls) if calls else (text, [])


def openai_messages(system: str, messages: list[dict]) -> list[dict]:
    """VALOR's neutral message format (see Provider.complete) in the OpenAI chat format."""
    out = [{"role": "system", "content": system}]
    for message in messages:
        if message["role"] == "tool":
            out += [{"role": "tool", "tool_call_id": result["id"], "content": result.get("content") or "(no output)"}
                    for result in message["results"]]
        elif message["role"] == "assistant" and message.get("tool_calls"):
            out.append({"role": "assistant", "content": message.get("content") or None, "tool_calls": [
                {"id": call["id"], "type": "function",
                 "function": {"name": call["name"], "arguments": json.dumps(call.get("input") or {})}}
                for call in message["tool_calls"]]})
        else:
            out.append({"role": message["role"], "content": message["content"]})
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
    # Stopping well before max_tokens means something else ran out: the context window.
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
