# *tilts head* OwO what's this? valor/ai/__init__.py, the entrance to everything AI in VALOR!
# The docstring below explains the core promise: the model only produces TEXT and TOOL CALLS. VALOR itself pulls
# the YAML out, validates it, and only a person can turn it into a real range (plan -> approve). *wags tail*
# It also lists the four modules. This file re-exports their public names so the rest of VALOR can just write
# `from valor.ai import build, provider_from, ...` without knowing which file each thing lives in ^w^
"""AI for VALOR: describe an environment, a model writes the range spec; range chats look at and operate a range.

The model only ever produces text and tool calls: VALOR extracts the YAML, validates it against the schema and the
cluster, and asks the model to fix what fails. Nothing is saved or built without a person: a spec goes to the
editor or a draft, then plan -> approve like any other range.

    providers.py   talking to models (OpenAI-compatible servers, OpenRouter): streamed replies, limits, errors
    prompts.py     what the model is told (spec reference, cluster facts, rules, the chat modes)
    answers.py     reading answers: the spec, question cards, YAML repairs
    loop.py        the validate-and-fix loop (build)
"""

# From answers.py: pull question cards out of a reply (extract_questions), pull the YAML spec out of a reply
# (extract_yaml), and fix small YAML mistakes models make (repair_yaml) >w<
from .answers import extract_questions, extract_yaml, repair_yaml
# From loop.py: FIX_ROUNDS (how many times the model may retry a broken spec), MAX_CHARS (size cap for text
# sent back to the model), build (the whole validate-and-fix loop) and trim (shorten long text) UwU
from .loop import FIX_ROUNDS, MAX_CHARS, build, trim
# From prompts.py: SPEC_REFERENCE (the spec docs given to the model), builder_prompt / range_prompt (the system
# prompts for the chat builder and range chats) and reference (the reference text with cluster facts) *nuzzles*
from .prompts import SPEC_REFERENCE, builder_prompt, range_prompt, reference
# From providers.py: everything about talking to model servers. DEFAULTS/KIND_NAMES/PROVIDERS describe the
# supported kinds; TURN_MAX_TOTAL caps a whole turn; OpenAICompatible and OpenRouter are the two client classes;
# Provider is their base; Reply is one streamed answer; cut_off, split_thinking and text_tool_calls clean up
# replies (truncated answers, <think> blocks, tool calls written as plain text); provider_from builds a client
# from saved settings ^w^ *happy tail wags*
from .providers import (DEFAULTS, KIND_NAMES, PROVIDERS, TURN_MAX_TOTAL, OpenAICompatible, OpenRouter, Provider,
                        Reply, cut_off, provider_from, split_thinking, text_tool_calls)

# The public names, one list, so `import *` and linters know exactly what this package offers OwO
__all__ = ["DEFAULTS", "FIX_ROUNDS", "KIND_NAMES", "MAX_CHARS", "PROVIDERS", "SPEC_REFERENCE", "TURN_MAX_TOTAL",
           "OpenAICompatible", "OpenRouter", "Provider", "Reply", "build", "builder_prompt", "cut_off",
           "extract_questions", "extract_yaml", "provider_from", "range_prompt", "reference", "repair_yaml",
           "split_thinking", "text_tool_calls", "trim"]
