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
