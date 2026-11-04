# *squints at the model's reply* OwO valor/ai/answers.py reads what the AI model wrote!
# Models answer in Markdown. This file finds the spec in a ```yaml block, the question cards in a ```questions
# block, and fixes the most common YAML slip models make (an unquoted value containing ": ") ^w^
"""Reading the model's answers: the spec in a ```yaml block, question cards in a ```questions block, and repairs for
the YAML slips models make (an unquoted value with a colon in it)."""

# Lazy type hints UwU
from __future__ import annotations

# json quotes strings safely (JSON strings are valid YAML strings); re finds the fenced blocks >w<
import json
import re

# PyYAML parses (safe_load only: never runs code from the text) OwO
import yaml

# The patterns *sniffs each one*:
#   YAML_BLOCK       a ```yaml or ```yml fenced block (re.S lets . match newlines; re.I ignores case)
#   SPEC_BLOCK       a spec in a fence with no language, recognised by its first line "apiVersion:"
#   SPEC_TEXT        any line that starts with "apiVersion:" (spec-looking text anywhere)
#   QUESTIONS_BLOCK  a ```questions fenced block
# Limits: at most 12 questions with at most 10 options each ^w^
YAML_BLOCK = re.compile(r"```(?:yaml|yml)\s*\n(.*?)```", re.S | re.I)
SPEC_BLOCK = re.compile(r"```[ \t]*\n(\s*apiVersion:.*?)```", re.S)     # a spec in a fence without a language
SPEC_TEXT = re.compile(r"^\s*apiVersion:", re.M)
QUESTIONS_BLOCK = re.compile(r"```questions\s*\n(.*?)```", re.S)
MAX_QUESTIONS, MAX_OPTIONS = 12, 10


# QUESTION_ITEM: a list item that starts a question ("- header:" or "- question:"), for answers where the model
# forgot the fence UwU
QUESTION_ITEM = re.compile(r"^-[ \t]+(?:header|question)[ \t]*:", re.M)
# what is left of a mangled opening fence before such a list: "```questions", "```yaml", "...below:questions"
QUESTIONS_LABEL = re.compile(r"(?:```[ \t]*(?:questions|ya?ml)?|(?<=[^\w\s])questions|(?:^|\n)[ \t]*questions)[ \t]*\Z")


# extract_questions: returns (questions or None, the text with the block removed) >w<
def extract_questions(text: str) -> tuple[list[dict] | None, str]:
    """(the questions of the last ```questions block, normalized; the text without the block). A list of questions
    that ends the answer counts too when the model dropped or mangled the opening fence (seen with Mistral)."""
    # The normal case: the last ```questions block wins *pounces on it*
    blocks = QUESTIONS_BLOCK.findall(text)
    if blocks:
        out = _questions(blocks[-1])
        # Not valid YAML -> keep the whole text as is (no questions). Valid but empty -> None, and the block is removed ^w^
        return (None, text) if out is None else ((out or None), QUESTIONS_BLOCK.sub("", text).strip())
    # No fence: look for a question list that ends the answer (some models drop or mangle the fence) OwO
    first_item = QUESTION_ITEM.search(text)
    if not first_item:
        return None, text
    # Take everything from the first question item on, dropping a lonely closing fence at the very end >w<
    body = re.sub(r"\n[ \t]*```[ \t]*\Z", "", text[first_item.start():].rstrip())
    # If another fence is in there, it isn't a plain question list; don't guess UwU
    out = _questions(body) if "```" not in body else None
    if not out:
        return None, text                       # shown as written
    # Return the questions, and the text before them minus whatever's left of a broken fence label ^w^
    return out, QUESTIONS_LABEL.sub("", text[:first_item.start()].rstrip()).rstrip()


# _questions: parse a questions block into clean question dicts (or None when it isn't YAML) *tidies up with tail*
def _questions(block: str) -> list[dict] | None:
    """The normalized questions of a questions block (None: not YAML)."""
    try:
        # repair_yaml first, so a stray colon doesn't throw the whole block away OwO
        data = yaml.safe_load(repair_yaml(block))
    except yaml.YAMLError:
        return None
    # Accept both `questions: [...]` and a bare list >w<
    if isinstance(data, dict):
        data = data.get("questions")
    out = []
    # Anything that isn't a list gives no questions ^w^
    for item in data if isinstance(data, list) else []:
        # A bare string becomes a question with no options UwU
        if isinstance(item, str):
            item = {"question": item}
        # Skip items with no question text *flicks tail*
        if not isinstance(item, dict) or not str(item.get("question") or "").strip():
            continue
        # Options must be a list; anything else counts as no options OwO
        raw_options = item.get("options") if isinstance(item.get("options"), list) else []
        # unique, trimmed, non-empty options, at most MAX_OPTIONS
        # dict.fromkeys keeps order while dropping duplicates; each option is cut to 200 chars ^w^
        options = list(dict.fromkeys(str(option).strip()[:200] for option in raw_options
                                     if str(option).strip()))[:MAX_OPTIONS]
        # The recommended option only counts if it's really one of the options >w<
        recommended = str(item.get("recommended") or "").strip()
        # The normalized question: a 1-based id, a short header (max 40 chars), the question (max 500), the options,
        # whether several may be picked (models say "multiple" or "multi"), and the recommended option UwU
        out.append({"id": len(out) + 1, "header": str(item.get("header") or "").strip()[:40],
                    "question": str(item["question"]).strip()[:500], "options": options,
                    "multiple": bool(item.get("multiple") or item.get("multi")),
                    "recommended": recommended if recommended in options else None})
        # Stop at MAX_QUESTIONS, so a runaway model can't flood the UI ^w^
        if len(out) == MAX_QUESTIONS:
            break
    return out


# extract_yaml: returns (the last yaml block, repaired, or None; the text without the blocks) OwO
def extract_yaml(text: str) -> tuple[str | None, str]:
    """(the last ```yaml block, repaired; the text without it)."""
    # Prefer real ```yaml fences; fall back to plain fences that hold a spec >w<
    fence = YAML_BLOCK if YAML_BLOCK.search(text) else SPEC_BLOCK
    blocks = fence.findall(text)
    if not blocks:
        return None, text.strip()
    # The last block wins (models often show a snippet first, then the full spec) ^w^
    return repair_yaml(blocks[-1].strip() + "\n"), fence.sub("", text).strip()


# PLAIN_VALUE: a "key: value" line (maybe a list item "- key: value") whose value is plain, i.e. not quoted and
# not starting with a YAML special character; an optional trailing "# comment" is kept apart UwU
PLAIN_VALUE = re.compile(r"^(\s*(?:-\s+)?[A-Za-z_][\w-]*:\s+)([^\s\"'\[{|>&*!#%@`].*?)(\s+#.*)?\s*$")


# repair_yaml: quote values like `description: Data tier: files` that YAML rejects >w<
def repair_yaml(text: str) -> str:
    """Quote the plain values that contain ": " (`description: Data tier: files`), the YAML slip models make most:
    a whole fix round with a slow local model otherwise. Only lines YAML rejects change, and only if it then parses."""
    lines = text.split("\n")
    # Up to 50 rounds: each round fixes the one line YAML complains about, then tries again ^w^
    for _ in range(50):
        try:
            # Parses? Done, return the (maybe repaired) text OwO
            yaml.safe_load("\n".join(lines))
            return "\n".join(lines)
        # Which line broke, and is it the "colon inside a value" error? *tilts head*
        except yaml.MarkedYAMLError as e:
            line = e.problem_mark.line if e.problem_mark else -1
            colon_in_value = "mapping values are not allowed" in (e.problem or "") and 0 <= line < len(lines)
            match = PLAIN_VALUE.match(lines[line]) if colon_in_value else None
            # Anything else wrong = give up and return the original text unchanged (the normal checks report it) UwU
            if not match:
                return text
            # key: value with: colon   ->   key: "value with: colon"   (a trailing comment stays)
            lines[line] = match[1] + json.dumps(match[2], ensure_ascii=False) + (match[3] or "")
        # Any other YAML error (no line mark) = give up too >w<
        except yaml.YAMLError:
            return text
    # Still broken after 50 rounds: return the original ^w^
    return text
