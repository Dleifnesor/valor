# *types commands very carefully* OwO installer/sh.py runs COMMANDS, especially Proxmox's CLI tools! ^w^
# run() runs any command (and can keep secrets out of debug output); pvesh() calls the Proxmox API through the
# `pvesh` command with JSON output, so the installer needs no API token of its own *wags tail*
"""Running commands and the Proxmox CLI (pvesh / pveum / qm) with JSON output."""

# Lazy type hints UwU
from __future__ import annotations

# json parses pvesh's output; subprocess runs commands >w<
import json
import subprocess

# ui prints debug lines OwO
from . import ui


# CommandError: a command that failed: the command, its exit code and output. Its message shows the command (long
# arguments shortened) and the end of its error output ^w^
class CommandError(Exception):
    def __init__(self, cmd: list[str], code: int, out: str, err: str):
        self.cmd, self.code, self.out, self.err = cmd, code, out, err
        shown = " ".join(arg if len(arg) < 80 else arg[:77] + "..." for arg in cmd)
        super().__init__(f"command failed ({code}): {shown}\n{(err or out).strip()[-1500:]}")


# run(): run a command and capture its output. check=True raises CommandError on failure; input = text for stdin;
# quiet_secret = the command carries a secret, so it's never printed and is redacted in errors UwU
def run(cmd: list[str], check: bool = True, input: str | None = None, timeout: float | None = None,
        quiet_secret: bool = False) -> subprocess.CompletedProcess:
    """quiet_secret: never echo this command (it carries a secret) in debug output."""
    ui.debug(("$ " + " ".join(cmd)) if not quiet_secret else "$ <command with a secret>")
    res = subprocess.run(cmd, text=True, capture_output=True, input=input, timeout=timeout)
    if check and res.returncode != 0:
        if quiet_secret:
            cmd = cmd[:3] + ["<redacted>"]
        raise CommandError(cmd, res.returncode, res.stdout, res.stderr)
    return res


# _args(): turn keyword parameters into "--key value" pairs (None skipped, lists repeated, booleans as 0/1) >w<
def _args(params: dict) -> list[str]:
    out: list[str] = []
    for key, value in params.items():
        if value is None:
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            if isinstance(item, bool):
                item = int(item)
            out += [f"--{key}", str(item)]
    return out


# pvesh(): call the API: method get/create/set/delete, a path, and parameters (the leading underscores keep these
# names from clashing with an API parameter that's literally called "path") OwO
def pvesh(_method: str, _path: str, _quiet_secret: bool = False, **params):
    """_method: get | create | set | delete; params become --key value (an API parameter may itself be 'path').
    Returns parsed JSON (or None)."""
    cmd = ["pvesh", _method, _path, *_args(params), "--output-format", "json"]
    res = run(cmd, quiet_secret=_quiet_secret)
    # Empty output = None; JSON when possible; otherwise the raw text ^w^
    text = res.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
