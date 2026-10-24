"""Running commands and the Proxmox CLI (pvesh / pveum / qm) with JSON output."""

from __future__ import annotations

import json
import subprocess

from . import ui


class CommandError(Exception):
    def __init__(self, cmd: list[str], code: int, out: str, err: str):
        self.cmd, self.code, self.out, self.err = cmd, code, out, err
        shown = " ".join(c if len(c) < 80 else c[:77] + "..." for c in cmd)
        super().__init__(f"command failed ({code}): {shown}\n{(err or out).strip()[-1500:]}")


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


def _args(params: dict) -> list[str]:
    out: list[str] = []
    for k, v in params.items():
        if v is None:
            continue
        values = v if isinstance(v, (list, tuple)) else [v]
        for item in values:
            if isinstance(item, bool):
                item = int(item)
            out += [f"--{k}", str(item)]
    return out


def pvesh(_method: str, _path: str, _quiet_secret: bool = False, **params):
    """_method: get | create | set | delete; params become --key value (an API parameter may itself be 'path').
    Returns parsed JSON (or None)."""
    cmd = ["pvesh", _method, _path, *_args(params), "--output-format", "json"]
    res = run(cmd, quiet_secret=_quiet_secret)
    text = res.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
