"""Terminal output and prompts. Plain text when not on a terminal (logs, CI)."""

from __future__ import annotations

import os
import sys

TTY = sys.stdout.isatty()
DEBUG = bool(os.environ.get("VALOR_DEBUG"))
ASSUME_YES = False          # set by --yes / unattended runs: prompts take their default


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


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


def debug(msg: str) -> None:
    if DEBUG:
        print(dim("    " + msg), file=sys.stderr, flush=True)


def table(rows: list[tuple[str, str]], indent: int = 4) -> None:
    width = max((len(k) for k, _ in rows), default=0)
    for k, v in rows:
        print(" " * indent + dim(k.ljust(width)) + "  " + v, flush=True)


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


def confirm(question: str, default: bool = True) -> bool:
    if ASSUME_YES or not sys.stdin.isatty():
        return default
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"  {question} [{hint}]: ").strip().lower()
    except EOFError:
        raw = ""
    return default if not raw else raw in ("y", "yes")
