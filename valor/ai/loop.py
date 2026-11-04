# *rolls up sleeves with tiny paws* OwO this is valor/ai/loop.py, the validate-and-fix loop!
# When you ask the chat builder for an environment, the model writes a range spec. This loop checks the spec
# (schema, unique name, does the cluster have what it needs) and, if anything's wrong, quietly tells the model
# the problems and asks again, up to FIX_ROUNDS extra times. Only a spec that passes (or the last try) comes back ^w^
"""The validate-and-fix loop: ask the model for a spec, check it (schema, unique name, the cluster), and ask again
with the problems until it passes or FIX_ROUNDS run out."""

# Lazy type hints UwU
from __future__ import annotations

# ValorError for the "you didn't say anything" case >w<
from ..errors import ValorError
# SPEC_TEXT spots spec-looking text; extract_questions pulls out question cards; extract_yaml pulls out the spec ^w^
from .answers import SPEC_TEXT, extract_questions, extract_yaml
# Provider = a model client; Reply = one answer from it; cut_off builds the error for an answer that hit the
# model's output limit OwO
from .providers import Provider, Reply, cut_off

# Size limits for what's sent to the model *measures with tail*:
#   MAX_TURNS   only the newest 24 messages of a conversation are sent
#   MAX_CHARS   and only as many as fit in 60,000 characters (underscores in numbers are just for readability)
#   FIX_ROUNDS  how many extra tries the model gets to fix a spec that fails the checks
MAX_TURNS = 24
MAX_CHARS = 60_000
FIX_ROUNDS = 2


# trim: keep the newest messages that fit the limits, and make sure the list starts with a user message (models
# expect the conversation to start with the person talking) UwU
def trim(messages: list[dict]) -> list[dict]:
    """The newest turns that fit the size limits, starting with a user turn."""
    # out = the kept messages (built newest-first); size = characters counted so far ^w^
    out, size = [], 0
    # Walk backwards from the newest message (only the last MAX_TURNS of them) >w<
    for message in reversed(messages[-MAX_TURNS:]):
        # Add this message's length; once over the limit, stop: this message and everything older is dropped *snips*
        size += len(message["content"])
        if size > MAX_CHARS:
            break
        out.append(message)
    # We collected newest-first, so flip back to oldest-first order OwO
    out.reverse()
    # Drop assistant messages at the start until a user message comes first ^w^
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


# _complete: ask the provider for one answer. If someone wants live text (on_delta = a callback for every new
# piece of streamed text), pass it along; otherwise call the simpler 2-argument form UwU
def _complete(provider: Provider, system: str, convo: list[dict], on_delta) -> Reply:
    # providers written before streaming callbacks (tests' fakes) take (system, messages) only
    return provider.complete(system, convo, on_delta=on_delta) if on_delta else provider.complete(system, convo)


# build(): the whole loop *wiggles excitedly*
#   provider   which model server to ask
#   system     the system prompt (rules + spec reference)
#   messages   the conversation so far
#   check      a function: check(yaml_text) -> list of problems (empty list = the spec is fine)
#   on_delta   optional live-text callback; on_check = optional callback told about every failed check
# Returns everything the chat needs in one dict (see the docstring) ^w^
def build(provider: Provider, system: str, messages: list[dict], check, on_delta=None, on_check=None) -> dict:
    """Ask the model; validate its spec with check(yaml) -> list of problems; ask for fixes (FIX_ROUNDS times).
    An answer with questions and no spec ends the round (the person answers first).
    Returns {reply, yaml, questions, problems, attempts, fixed (the problems the model corrected), reasoning,
    input_tokens, output_tokens}. on_check(problems) is told about every failed check."""
    # Trim the conversation; if nothing is left, the person hasn't asked anything yet >w<
    convo = trim(messages)
    if not convo:
        raise ValorError("ai_no_message", "Say what environment you want.")
    # Running totals of tokens (for the usage display), and the latest results of each try OwO
    used_in = used_out = 0
    reply = yaml_text = None
    questions = None
    problems: list[str] = []
    # `fixed` collects problems from earlier tries that a later try corrected (the UI shows "fixed N problems") ^w^
    fixed: list[str] = []                   # problems found in earlier attempts (the model corrected them)
    # Any "thinking" text the model produced, kept for the collapsible reasoning view UwU
    reasoning: list[str] = []
    # Attempt 1 is the first answer; attempts 2..FIX_ROUNDS+1 are the fix rounds *counts on toe beans*
    for attempt in range(1, FIX_ROUNDS + 2):
        # Ask the model and add up its token use >w<
        answer = _complete(provider, system, convo, on_delta)
        used_in += answer.input_tokens
        used_out += answer.output_tokens
        # Keep the model's reasoning text, if it gave any ^w^
        if answer.reasoning:
            reasoning.append(answer.reasoning)
        # The answer was cut off by the output limit: asking again would just get cut off in the same spot, so stop
        # with a clear error (with the token counts attached so the UI can show them) OwO
        if answer.truncated:            # asking again would be cut off at the same place
            error = cut_off(provider, answer)
            error.details = {"input_tokens": used_in, "output_tokens": used_out}
            raise error
        # Split the answer: question cards out first, then the YAML spec from what's left. `reply` is the prose part *sniffs*
        questions, text = extract_questions(answer.text)
        yaml_text, reply = extract_yaml(text)
        # No spec, AND either the model asked questions or the text never looked like a spec: this is a normal answer
        # (e.g. the model needs the person to choose something first). Nothing to check, end the loop ^w^
        if yaml_text is None and (questions or not SPEC_TEXT.search(text)):
            problems = []               # questions, or a plain answer to something that asked for no spec
            break
        # Spec-looking text but no ```yaml block: that's a problem to report back UwU
        if yaml_text is None:
            problems = ["the spec is not in a ```yaml block"]
        else:
            # A spec! Run the checks on it >w<
            problems = check(yaml_text)
        # No problems = done *happy tail wags*
        if not problems:
            break
        # Remember these problems as "fixed" (if a later try passes, they were) and tell the listener about them, but only
        # when another try is coming (no point announcing problems on the very last try) OwO
        fixed += problems
        if on_check and attempt <= FIX_ROUNDS:
            on_check(problems)
        # Ask again with the problems. The person never sees the failed attempt, so the model is told to answer
        # as if for the first time (no apologies for a mistake the person never saw).
        # Add the failed answer and a hidden "automatic check" message to the conversation for the next try ^w^
        convo = [*convo, {"role": "assistant", "content": answer.text},
                 {"role": "user", "content": "[Automatic check by VALOR - the person does not see this message or your "
                  "previous answer.] VALOR could not use that spec:\n" + "\n".join(f"- {p}" for p in problems)
                  + "\nWrite your answer to the person's request again, as if for the first time: a short explanation "
                  "of the environment (do not apologize or mention this check), then the corrected complete spec in "
                  "one ```yaml block."}]
    # The result: the prose reply, the spec, question cards, remaining problems (empty when it passed), how many
    # tries it took, what got fixed, all reasoning text joined with blank lines, and the token totals UwU *purrs*
    return {"reply": reply, "yaml": yaml_text, "questions": questions, "problems": problems, "attempts": attempt,
            "fixed": fixed, "reasoning": "\n\n".join(reasoning), "input_tokens": used_in, "output_tokens": used_out}
