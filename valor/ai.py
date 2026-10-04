"""Chat builder: describe an environment, an AI model writes the range spec.

Providers are pluggable (Anthropic's Messages API, any OpenAI-compatible endpoint such as a local model server, or
none). The model only ever produces text: VALOR extracts the YAML, validates it against the schema and the cluster,
and asks the model to fix what fails. Nothing is saved or built without a person: the spec goes to the editor,
then plan -> approve like any other range.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import requests
from urllib.parse import urlparse

from .errors import ValorError

PROVIDERS = ("none", "anthropic", "openai")
ANTHROPIC_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5-20251001")
# max_tokens is a ceiling, not a cost (only generated tokens count): high enough that a large spec is never cut off
DEFAULTS = {"provider": "none", "model": "", "base_url": "", "api_key": "", "max_tokens": 16384,
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
    truncated: bool = False             # the answer stopped at max_tokens


class Provider:
    name = "none"

    def __init__(self, model: str, api_key: str = "", base_url: str = "", max_tokens: int = 16384, timeout: int = 300):
        self.model, self.api_key, self.base_url = model, api_key, base_url.rstrip("/")
        self.max_tokens, self.timeout = max_tokens, timeout

    def complete(self, system: str, messages: list[dict]) -> Reply:   # pragma: no cover - interface
        raise NotImplementedError

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        where = urlparse(url).netloc
        try:
            r = requests.post(url, headers=headers, json=body, timeout=(10, self.timeout))
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
            raise ValorError("ai_unreachable", f"{where} did not finish its answer within {self.timeout} s",
                             hint="Raise the timeout, lower 'Max tokens per answer', or use a faster model.")
        except requests.RequestException as e:
            raise ValorError("ai_unreachable", f"the AI provider did not answer: {e.__class__.__name__}")
        if r.status_code == 404:
            raise ValorError("ai_error", f"{url} does not exist on the provider (HTTP 404)",
                             hint="For OpenAI-compatible servers the base URL usually ends in /v1 "
                                  "(e.g. http://192.168.1.50:1234/v1); also check the model name.")
        if r.status_code in (401, 403):
            raise ValorError("ai_auth", "the AI provider rejected the API key", hint="An administrator can update it in Settings.")
        if r.status_code == 429:
            raise ValorError("ai_rate_limited", "the AI provider is rate limiting VALOR; try again in a minute")
        if r.status_code >= 400:
            detail = ""
            try:
                detail = json.dumps(r.json().get("error", ""))[:300]
            except ValueError:
                pass
            raise ValorError("ai_error", f"the AI provider answered HTTP {r.status_code} {detail}".strip())
        try:
            return r.json()
        except ValueError:
            raise ValorError("ai_error", "the AI provider sent a response that is not JSON")


class Anthropic(Provider):
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def complete(self, system: str, messages: list[dict]) -> Reply:
        d = self._post(self.base_url + "/v1/messages" if self.base_url else self.URL,
                       {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                       {"model": self.model or ANTHROPIC_MODELS[0], "max_tokens": self.max_tokens, "system": system,
                        "messages": messages})
        text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        u = d.get("usage") or {}
        return Reply(text, int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)),
                     truncated=d.get("stop_reason") == "max_tokens")


class OpenAICompatible(Provider):
    name = "openai"

    def complete(self, system: str, messages: list[dict]) -> Reply:
        if not self.base_url:
            raise ValorError("ai_not_configured", "the OpenAI-compatible provider needs a base URL")
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        d = self._post(self.base_url + "/chat/completions", headers,
                       {"model": self.model, "max_tokens": self.max_tokens,
                        "messages": [{"role": "system", "content": system}, *messages]})
        try:
            text = d["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ValorError("ai_error", "the AI provider's answer has no message")
        u = d.get("usage") or {}
        return Reply(text, int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0)),
                     truncated=d["choices"][0].get("finish_reason") == "length")


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


def system_prompt(cfg, catalog: dict, present: set[str], roles: list[dict], facts: dict) -> str:
    oses = "\n".join(f"- {name}{' (default)' if name == cfg.default_os else ''}: {e.get('description', '')} "
                     f"[{e.get('family', 'debian')}]" for name, e in catalog.items() if name in present)
    role_lines = []
    for r in roles:
        params = "; ".join(f"{k}{'' if not v.get('required') else ' (required)'}: {v.get('description', '').strip()}"
                           + (f" (default {v['default']!r})" if 'default' in v else "")
                           for k, v in (r.get("params") or {}).items())
        role_lines.append(f"- {r['name']} [{', '.join(r.get('families') or ['debian'])}]: {r['description']}"
                          + (f" | params: {params}" if params else ""))
    return f"""You design cyber ranges (isolated lab networks of virtual machines) for VALOR, which builds them on
Proxmox VE. The person you talk to describes an environment; you answer with a short explanation (a few sentences:
what you built and any assumption you made) followed by exactly one complete range spec in a ```yaml code block.

Rules:
- Follow the spec reference below exactly; the spec is validated and you will be told about any problem.
- Use only operating systems from the list of available templates and roles from the role list; a role only works
  on the OS families shown in brackets.
- Never put passwords, keys or other secrets in the spec (VALOR generates logins itself).
- Keep a spec you were given and change only what the person asks for; always return the whole spec.
- Deny by default: only add policy rules the environment needs, and say which flows you allowed.
- Pick unused VLANs and private networks that avoid the networks listed under cluster facts.
- If the request is unclear, make a sensible small choice and say what you assumed.

# Spec reference
{spec_reference(cfg)}

# Available operating systems (templates on this cluster)
{oses or '- (none built yet)'}

# Roles
{chr(10).join(role_lines)}

# Cluster facts
{json.dumps(facts, indent=1, sort_keys=True)}
"""


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


def build(provider: Provider, system: str, messages: list[dict], check) -> dict:
    """Ask the model; validate its spec with check(yaml) -> list of problems; ask for fixes (FIX_ROUNDS times).
    Returns {reply, yaml, problems, attempts, input_tokens, output_tokens}."""
    convo = trim(messages)
    if not convo:
        raise ValorError("ai_no_message", "Say what environment you want.")
    used_in = used_out = 0
    reply = yaml_text = None
    problems: list[str] = []
    for attempt in range(1, FIX_ROUNDS + 2):
        r = provider.complete(system, convo)
        used_in += r.input_tokens
        used_out += r.output_tokens
        if r.truncated:                 # asking again would be cut off at the same place
            raise ValorError("ai_truncated", f"the model's answer was cut off at {provider.max_tokens} tokens",
                             hint="Raise 'Max tokens per answer' in Settings -> AI provider.",
                             details={"input_tokens": used_in, "output_tokens": used_out})
        yaml_text, reply = extract_yaml(r.text)
        if yaml_text is None:
            problems = ["the answer contains no ```yaml block with the spec"]
        else:
            problems = check(yaml_text)
        if not problems:
            break
        convo = [*convo, {"role": "assistant", "content": r.text},
                 {"role": "user", "content": "VALOR could not use that spec:\n" + "\n".join(f"- {p}" for p in problems)
                  + "\nReply with the corrected complete spec in one ```yaml block."}]
    return {"reply": reply, "yaml": yaml_text, "problems": problems, "attempts": attempt,
            "input_tokens": used_in, "output_tokens": used_out}
