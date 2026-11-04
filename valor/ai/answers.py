"""Reading the model's answers: the spec in a ```yaml block, question cards in a ```questions block, and repairs for
the YAML slips models make (an unquoted value with a colon in it)."""

from __future__ import annotations

import json
import re

import yaml

YAML_BLOCK = re.compile(r"```(?:yaml|yml)\s*\n(.*?)```", re.S | re.I)
SPEC_BLOCK = re.compile(r"```[ \t]*\n(\s*apiVersion:.*?)```", re.S)     # a spec in a fence without a language
SPEC_TEXT = re.compile(r"^\s*apiVersion:", re.M)
QUESTIONS_BLOCK = re.compile(r"```questions\s*\n(.*?)```", re.S)
MAX_QUESTIONS, MAX_OPTIONS = 12, 10


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
    first_item = QUESTION_ITEM.search(text)
    if not first_item:
        return None, text
    body = re.sub(r"\n[ \t]*```[ \t]*\Z", "", text[first_item.start():].rstrip())
    out = _questions(body) if "```" not in body else None
    if not out:
        return None, text                       # shown as written
    return out, QUESTIONS_LABEL.sub("", text[:first_item.start()].rstrip()).rstrip()


def _questions(block: str) -> list[dict] | None:
    """The normalized questions of a questions block (None: not YAML)."""
    try:
        data = yaml.safe_load(repair_yaml(block))
    except yaml.YAMLError:
        return None
    if isinstance(data, dict):
        data = data.get("questions")
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, str):
            item = {"question": item}
        if not isinstance(item, dict) or not str(item.get("question") or "").strip():
            continue
        raw_options = item.get("options") if isinstance(item.get("options"), list) else []
        # unique, trimmed, non-empty options, at most MAX_OPTIONS
        options = list(dict.fromkeys(str(option).strip()[:200] for option in raw_options
                                     if str(option).strip()))[:MAX_OPTIONS]
        recommended = str(item.get("recommended") or "").strip()
        out.append({"id": len(out) + 1, "header": str(item.get("header") or "").strip()[:40],
                    "question": str(item["question"]).strip()[:500], "options": options,
                    "multiple": bool(item.get("multiple") or item.get("multi")),
                    "recommended": recommended if recommended in options else None})
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
            line = e.problem_mark.line if e.problem_mark else -1
            colon_in_value = "mapping values are not allowed" in (e.problem or "") and 0 <= line < len(lines)
            match = PLAIN_VALUE.match(lines[line]) if colon_in_value else None
            if not match:
                return text
            # key: value with: colon   ->   key: "value with: colon"   (a trailing comment stays)
            lines[line] = match[1] + json.dumps(match[2], ensure_ascii=False) + (match[3] or "")
        except yaml.YAMLError:
            return text
    return text
