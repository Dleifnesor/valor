# *decorates the terminal* OwO installer/ui.py: the installer's terminal OUTPUT and PROMPTS! ^w^
# Colored step lines, ✓/!/✗ marks, tables, and questions. On a non-terminal (a log file, CI) it prints plain text and
# never asks: every question takes its default *wags tail*
"""Terminal output and prompts. Plain text when not on a terminal (logs, CI)."""

# Lazy type hints UwU
from __future__ import annotations

# os (the debug switch) and sys (terminal checks, stderr) >w<
import os
import sys

# TTY: are we printing to a terminal? DEBUG: VALOR_DEBUG set? ASSUME_YES: --yes was given (questions take their
# defaults) OwO
TTY = sys.stdout.isatty()
DEBUG = bool(os.environ.get("VALOR_DEBUG"))
ASSUME_YES = False          # set by --yes / unattended runs: prompts take their default


# _c(): wrap text in an ANSI color code (only on a terminal) ^w^
def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


# bold() / dim(): text styles UwU
def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


# say(): a plain line. step(): a "==> Step" heading. ok/info/warn/fail: indented lines with ✓, nothing, ! or ✗
# (failures go to stderr) >w<
def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    print(_c("1;36", "==> ") + bold(msg), flush=True)


def ok(msg: str) -> None:
    print(_c("32", "  ✓ ") + msg, flush=True)


def info(msg: str) -> None:
    print("    " + msg, flush=True)


def warn(msg: str) -> None:
    print(_c("33", "  ! ") + msg, flush=True)


def fail(msg: str) -> None:
    print(_c("31", "  ✗ ") + msg, file=sys.stderr, flush=True)


# debug(): a dim line, only with VALOR_DEBUG OwO
def debug(msg: str) -> None:
    if DEBUG:
        print(dim("    " + msg), file=sys.stderr, flush=True)


# table(): label/value rows, labels padded to line up ^w^
def table(rows: list[tuple[str, str]], indent: int = 4) -> None:
    width = max((len(label) for label, _ in rows), default=0)
    for label, value in rows:
        print(" " * indent + dim(label.ljust(width)) + "  " + value, flush=True)


# ask(): a free-text question; Enter keeps the default; `validate` can reject an answer (then it asks again) UwU
def ask(question: str, default: str = "", validate=None) -> str:
    """Free-text question; Enter keeps the default. validate(value) returns an error message or None."""
    if ASSUME_YES or not sys.stdin.isatty():
        return default
    while True:
        hint = f" [{default}]" if default != "" else ""
        try:
            value = input(f"  {question}{hint}: ").strip()
        except EOFError:
            value = ""
        value = value or default
        problem = validate(value) if validate else None
        if not problem:
            return value
        fail(problem)


# choose(): pick from a numbered list (by number or by value); the default is starred >w<
def choose(question: str, options: list[tuple[str, str]], default: str) -> str:
    """options: (value, description)."""
    if ASSUME_YES or not sys.stdin.isatty():
        return default
    say(f"  {question}")
    for i, (value, desc) in enumerate(options, 1):
        mark = "*" if value == default else " "
        say(f"   {mark}{i}) {value:<18} {dim(desc)}")
    while True:
        try:
            raw = input(f"  Choice [{default}]: ").strip()
        except EOFError:
            raw = ""
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        if raw in [o[0] for o in options]:
            return raw
        fail("pick a number or a value from the list")


# confirm(): a yes/no question (Y/n or y/N depending on the default) OwO *nods yes*
def confirm(question: str, default: bool = True) -> bool:
    """Yes/no question; Enter keeps the default. --yes answers yes (it accepts confirmations, whatever the
    default); with no terminal to ask on and no --yes, the default is the answer."""
    if ASSUME_YES:
        return True
    if not sys.stdin.isatty():
        return default
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"  {question} [{hint}]: ").strip().lower()
    except EOFError:
        raw = ""
    return default if not raw else raw in ("y", "yes")
