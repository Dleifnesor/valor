"""The validate-and-fix loop: ask the model for a spec, check it (schema, unique name, the cluster), and ask again
with the problems until it passes or FIX_ROUNDS run out."""

from __future__ import annotations

from ..errors import ValorError
from .answers import SPEC_TEXT, extract_questions, extract_yaml
from .providers import Provider, Reply, cut_off

MAX_TURNS = 24
MAX_CHARS = 60_000
FIX_ROUNDS = 2


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


def _complete(provider: Provider, system: str, convo: list[dict], on_delta) -> Reply:
    # providers written before streaming callbacks (tests' fakes) take (system, messages) only
    return provider.complete(system, convo, on_delta=on_delta) if on_delta else provider.complete(system, convo)


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
