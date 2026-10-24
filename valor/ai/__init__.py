"""AI for VALOR: describe an environment, a model writes the range spec; range chats look at and operate a range.

The model only ever produces text and tool calls: VALOR extracts the YAML, validates it against the schema and the
cluster, and asks the model to fix what fails. Nothing is saved or built without a person: a spec goes to the
editor or a draft, then plan -> approve like any other range.

    providers.py   talking to models (OpenAI-compatible servers, OpenRouter): streamed replies, limits, errors
    prompts.py     what the model is told (spec reference, cluster facts, rules, the chat modes)
    answers.py     reading answers: the spec, question cards, YAML repairs
    loop.py        the validate-and-fix loop (build)
"""

from .answers import extract_questions, extract_yaml, repair_yaml
from .loop import FIX_ROUNDS, MAX_CHARS, build, trim
from .prompts import SPEC_REFERENCE, builder_prompt, range_prompt, reference
from .providers import (DEFAULTS, KIND_NAMES, PROVIDERS, TURN_MAX_TOTAL, OpenAICompatible, OpenRouter, Provider,
                        Reply, cut_off, provider_from, split_thinking, text_tool_calls)

__all__ = ["DEFAULTS", "FIX_ROUNDS", "KIND_NAMES", "MAX_CHARS", "PROVIDERS", "SPEC_REFERENCE", "TURN_MAX_TOTAL",
           "OpenAICompatible", "OpenRouter", "Provider", "Reply", "build", "builder_prompt", "cut_off",
           "extract_questions", "extract_yaml", "provider_from", "range_prompt", "reference", "repair_yaml",
           "split_thinking", "text_tool_calls", "trim"]
