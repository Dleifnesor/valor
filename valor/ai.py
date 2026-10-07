"""Chat builder: describe an environment, an AI model writes the range spec.

Providers are pluggable (Anthropic's Messages API, any OpenAI-compatible endpoint such as a local model server, or
none). The model only ever produces text: VALOR extracts the YAML, validates it against the schema and the cluster,
and asks the model to fix what fails. Nothing is saved or built without a person: the spec goes to the editor,
then plan -> approve like any other range.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from urllib.parse import urlparse

from .errors import ValorError

PROVIDERS = ("none", "anthropic", "openai")
ANTHROPIC_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5-20251001")
# max_tokens is a ceiling, not a cost (only generated tokens count): high enough that a large spec is never cut off
DEFAULTS = {"provider": "none", "model": "", "base_url": "", "api_key": "", "max_tokens": 32768,
            "daily_tokens_per_user": 400_000, "timeout": 300}
MAX_TURNS = 24
MAX_CHARS = 60_000
FIX_ROUNDS = 2
YAML_BLOCK = re.compile(r"```(?:yaml|yml)\s*\n(.*?)```", re.S)


@dataclass
class Reply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    truncated: bool = False             # the answer stopped at a length limit (max_tokens or the context window)
    reasoning: str = ""                 # the model's thinking, when the provider streams it separately
    context_full: bool = False          # the provider said the context window, not max_tokens, ran out


MAX_TOTAL = 1140             # seconds for one answer, under the web proxy's 20 minutes for a chat request


class Provider:
    """Answers are streamed: `timeout` is how long the model may stay silent, so slow local models that think
    for minutes still work, while a stuck server is noticed."""
    name = "none"

    def __init__(self, model: str, api_key: str = "", base_url: str = "", max_tokens: int = DEFAULTS["max_tokens"],
                 timeout: int = 300):
        self.model, self.api_key, self.base_url = model, api_key, base_url.rstrip("/")
        self.max_tokens, self.timeout = max_tokens, timeout

    def complete(self, system: str, messages: list[dict]) -> Reply:   # pragma: no cover - interface
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
        t0 = time.time()
        try:
            for line in r.iter_lines(decode_unicode=True):
                if time.time() - t0 > MAX_TOTAL:
                    raise ValorError("ai_unreachable", f"{where} was still answering after {MAX_TOTAL // 60} minutes",
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
                          hint="The model may be loading or very slow: raise the timeout in Settings -> AI provider, "
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
        raise ValorError("ai_error", f"the AI provider answered HTTP {r.status_code} {detail}".strip(),
                         details={"status": r.status_code, "detail": detail})


class Anthropic(Provider):
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def complete(self, system: str, messages: list[dict]) -> Reply:
        text, thinking, tin, tout, stop = [], [], 0, 0, None
        for ev in self._events(self.base_url + "/v1/messages" if self.base_url else self.URL,
                               {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                "content-type": "application/json"},
                               {"model": self.model or ANTHROPIC_MODELS[0], "max_tokens": self.max_tokens,
                                "system": system, "messages": messages, "stream": True}):
            kind = ev.get("type")
            if kind == "message_start":
                tin = int(((ev.get("message") or {}).get("usage") or {}).get("input_tokens", 0))
            elif kind == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                text.append(ev["delta"].get("text", ""))
            elif kind == "content_block_delta" and (ev.get("delta") or {}).get("type") == "thinking_delta":
                thinking.append(ev["delta"].get("thinking", ""))
            elif kind == "message_delta":
                stop = (ev.get("delta") or {}).get("stop_reason") or stop
                tout = int((ev.get("usage") or {}).get("output_tokens", tout))
            elif kind == "error":
                raise ValorError("ai_error", f"the AI provider reported an error: {json.dumps(ev.get('error'))[:300]}")
        return Reply("".join(text), tin, tout, truncated=stop in ("max_tokens", "model_context_window_exceeded"),
                     reasoning="".join(thinking), context_full=stop == "model_context_window_exceeded")


class OpenAICompatible(Provider):
    name = "openai"

    def complete(self, system: str, messages: list[dict]) -> Reply:
        if not self.base_url:
            raise ValorError("ai_not_configured", "the OpenAI-compatible provider needs a base URL")
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        body = {"model": self.model, "max_tokens": self.max_tokens, "stream": True,
                "stream_options": {"include_usage": True},
                "messages": [{"role": "system", "content": system}, *messages]}
        try:
            return self._read(self._events(self.base_url + "/chat/completions", headers, body))
        except ValorError as e:
            if not (isinstance(e.details, dict) and e.details.get("status") == 400 and "stream_options" in e.details.get("detail", "")):
                raise
            body.pop("stream_options")      # servers that don't know it: no token counts, but an answer
            return self._read(self._events(self.base_url + "/chat/completions", headers, body))

    def context_length(self) -> int | None:
        """The context window the server loaded the model with, when it says (LM Studio's REST API does)."""
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            r = requests.get(f"{root}/api/v0/models/{self.model}", headers=headers, timeout=10)
            info = r.json() if r.ok else {}
        except (requests.RequestException, ValueError):
            return None
        n = info.get("loaded_context_length") or info.get("max_context_length")
        return int(n) if isinstance(n, int) else None

    @staticmethod
    def _read(events) -> Reply:
        text, thinking, tin, tout, finish = [], [], 0, 0, None
        for ev in events:
            for ch in ev.get("choices") or []:
                delta = ch.get("delta") or {}
                text.append(delta.get("content") or "")
                # reasoning models behind LM Studio, vLLM, llama.cpp: separate field (name varies by server)
                thinking.append(delta.get("reasoning_content") or delta.get("reasoning") or "")
                finish = ch.get("finish_reason") or finish
            u = ev.get("usage") or {}
            if u:
                tin, tout = int(u.get("prompt_tokens", tin)), int(u.get("completion_tokens", tout))
        content, inline = split_thinking("".join(text))
        return Reply(content, tin, tout, truncated=finish == "length", reasoning="".join(thinking) + inline)


def provider_from(settings: dict, api_key: str) -> Provider:
    kind = settings.get("provider", "none")
    args = dict(model=settings.get("model", ""), api_key=api_key, base_url=settings.get("base_url", ""),
                max_tokens=int(settings.get("max_tokens", DEFAULTS["max_tokens"])),
                timeout=int(settings.get("timeout", DEFAULTS["timeout"])))
    if kind == "anthropic":
        return Anthropic(**args)
    if kind == "openai":
        return OpenAICompatible(**args)
    raise ValorError("ai_not_configured", "No AI provider is connected.",
                     hint="An administrator can connect one in Settings -> AI provider.")


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
    return f"""# Spec reference
{spec_reference(cfg)}

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
  automatically. Never set baseline to windows-l1, and only use 'none' when the person asks for no hardening.
- If the request is unclear, make a sensible small choice and say what you assumed."""


def system_prompt(cfg, catalog: dict, present: set[str], roles: list[dict], facts: dict) -> str:
    return builder_prompt(reference(cfg, catalog, present, roles, facts))


def builder_prompt(ref: str) -> str:
    return f"""You design cyber ranges (isolated lab networks of virtual machines) for VALOR, which builds them on
Proxmox VE. The person you talk to describes an environment; you answer with a short explanation (a few sentences:
what you built and any assumption you made) followed by exactly one complete range spec in a ```yaml code block.

{SPEC_RULES}

{ref}"""


RANGE_MODES = {
    "question": """You answer questions about one VALOR range (shown below with its live state). Answer in plain,
concise text: what the range contains, who can reach what under its policy, why a test failed, how to use a service.
Do not write or change the spec in this mode; if the person wants a change, tell them to switch to Plan or Code mode.""",
    "plan": f"""You plan changes to one VALOR range (shown below with its live state). Start with a short plan: a few
bullet points saying what you change and why, and which traffic you allow. Then give the complete updated spec in
exactly one ```yaml code block. VALOR shows it as a draft on the map; nothing is built until a person approves the plan.

{SPEC_RULES}""",
    "code": f"""You edit the YAML spec of one VALOR range (shown below with its live state). Reply with the complete
updated spec in exactly one ```yaml code block and at most two sentences about the change. VALOR shows the diff and
keeps it as a draft; nothing is built until a person approves the plan.

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


def extract_yaml(text: str) -> tuple[str | None, str]:
    """(the last ```yaml block, the text without it)."""
    blocks = YAML_BLOCK.findall(text)
    if not blocks:
        return None, text.strip()
    return blocks[-1].strip() + "\n", YAML_BLOCK.sub("", text).strip()


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
        return ValorError("ai_context_full",
                          f"the model stopped after {r.output_tokens:,} tokens because its context window is full{size}",
                          hint=f"Give the model a larger context window: in LM Studio load it with a Context Length of "
                               f"at least {need:,} (or pick a model that supports it). Raising 'Max tokens per answer' "
                               "does not help here.", details=details)
    thought = f", {len(r.reasoning) // 4:,} of them thinking" if len(r.reasoning) > 400 else ""
    return ValorError("ai_truncated", f"the model's answer was cut off at {provider.max_tokens:,} tokens{thought}",
                      hint="Raise 'Max tokens per answer' in Settings -> AI provider (it is a ceiling: only the tokens "
                           "the model writes count).", details=details)


def ask(provider: Provider, system: str, messages: list[dict]) -> dict:
    """One answer, no spec (question mode)."""
    convo = trim(messages)
    if not convo:
        raise ValorError("ai_no_message", "Ask something.")
    r = provider.complete(system, convo)
    if r.truncated:
        raise cut_off(provider, r)
    return {"reply": r.text.strip(), "problems": [], "input_tokens": r.input_tokens, "output_tokens": r.output_tokens}


def build(provider: Provider, system: str, messages: list[dict], check) -> dict:
    """Ask the model; validate its spec with check(yaml) -> list of problems; ask for fixes (FIX_ROUNDS times).
    Returns {reply, yaml, problems, attempts, fixed (the problems the model corrected), input_tokens, output_tokens}."""
    convo = trim(messages)
    if not convo:
        raise ValorError("ai_no_message", "Say what environment you want.")
    used_in = used_out = 0
    reply = yaml_text = None
    problems: list[str] = []
    fixed: list[str] = []                   # problems found in earlier attempts (the model corrected them)
    for attempt in range(1, FIX_ROUNDS + 2):
        r = provider.complete(system, convo)
        used_in += r.input_tokens
        used_out += r.output_tokens
        if r.truncated:                 # asking again would be cut off at the same place
            raise cut_off(provider, r)
        yaml_text, reply = extract_yaml(r.text)
        if yaml_text is None:
            problems = ["the answer contains no ```yaml block with the spec"]
        else:
            problems = check(yaml_text)
        if not problems:
            break
        fixed += problems
        convo = [*convo, {"role": "assistant", "content": r.text},
                 {"role": "user", "content": "[Automatic check by VALOR - the person does not see this message or your "
                  "previous answer.] VALOR could not use that spec:\n" + "\n".join(f"- {p}" for p in problems)
                  + "\nWrite your answer to the person's request again, as if for the first time: a short explanation "
                  "of the environment (do not apologize or mention this check), then the corrected complete spec in "
                  "one ```yaml block."}]
    return {"reply": reply, "yaml": yaml_text, "problems": problems, "attempts": attempt, "fixed": fixed,
            "input_tokens": used_in, "output_tokens": used_out}
