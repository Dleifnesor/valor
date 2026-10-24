"""The tools a range chat's model may call, and how VALOR runs them: commands and files inside the range's VMs (QEMU
guest agent), power and snapshots (VALOR jobs), the range's status and its spec."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .. import jobs, lifecycle, ops
from ..errors import ValorError

OUTPUT_LIMIT = 16_000           # characters of a command's output kept for the model and the feed
DEFAULT_TIMEOUT, MAX_TIMEOUT = 120, 900
JOB_WAIT = 1800                 # seconds to wait for a power or snapshot job

HOST = {"type": "string", "description": "the host name in the range's spec ('rtr' is the router)"}
TOOLS = {
    "run_command": {
        "description": "Run a command inside one of the range's VMs as root (Linux: bash) or SYSTEM (Windows: "
                       "PowerShell). Read-only commands run at once; others wait for the person's approval. "
                       "Output is truncated to the last 16,000 characters.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["host", "command", "purpose"],
                         "properties": {
                             "host": HOST,
                             "command": {"type": "string", "description": "the command or a short script"},
                             "purpose": {"type": "string", "description": "one sentence for the person: what it does"},
                             "timeout": {"type": "integer", "minimum": 5, "maximum": MAX_TIMEOUT,
                                         "description": f"seconds (default {DEFAULT_TIMEOUT})"}}}},
    "read_file": {
        "description": "Read a text file inside a VM (at most 16,000 characters are returned).",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["host", "path"],
                         "properties": {"host": HOST, "path": {"type": "string"}}}},
    "write_file": {
        "description": "Write a text file inside a VM (replaces it; at most 60 KB). Needs the person's approval.",
        "input_schema": {"type": "object", "additionalProperties": False,
                         "required": ["host", "path", "content", "purpose"],
                         "properties": {"host": HOST, "path": {"type": "string"}, "content": {"type": "string"},
                                        "purpose": {"type": "string"}}}},
    "power": {
        "description": "Start, shut down (clean, forced after 3 minutes), reboot or stop VMs of the range. Needs "
                       "the person's approval.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["action", "purpose"],
                         "properties": {"action": {"type": "string", "enum": list(lifecycle.POWER_ACTIONS)},
                                        "hosts": {"type": "array", "items": {"type": "string"},
                                                  "description": "default: every VM of the range"},
                                        "purpose": {"type": "string"}}}},
    "snapshot": {
        "description": "List the range's snapshots, take one of every VM (disk only), or roll every VM back to one "
                       "(VALOR verifies the range afterwards). Taking and rolling back need the person's approval.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["action"],
                         "properties": {"action": {"type": "string", "enum": ["list", "create", "rollback"]},
                                        "name": {"type": "string", "pattern": "^[a-zA-Z][a-zA-Z0-9_-]{1,39}$"},
                                        "description": {"type": "string"}, "purpose": {"type": "string"}}}},
    "range_status": {
        "description": "The range's VMs (power state, OS, address), its last build and verification, its draft.",
        "input_schema": {"type": "object", "additionalProperties": False, "properties": {}}},
    "update_spec": {
        "description": "Save the complete updated range spec as the range's draft, to record a change so a rebuild "
                       "reproduces it. VALOR validates it first (problems come back as the result) and shows the "
                       "person the diff to approve; the draft is built only when they approve VALOR's plan.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["yaml", "summary"],
                         "properties": {"yaml": {"type": "string", "description": "the complete spec"},
                                        "summary": {"type": "string", "description": "what changed and why"}}}},
}
READ_TOOLS = ("run_command", "read_file", "range_status")
MODE_TOOLS = {"question": READ_TOOLS, "code": tuple(TOOLS)}


def tool_specs(mode: str) -> list[dict]:
    return [{"name": n, **TOOLS[n]} for n in MODE_TOOLS.get(mode, ())]


@dataclass
class Context:
    cfg: object
    pve: object
    range: str
    username: str
    vms: dict = field(default_factory=dict)          # host -> VMState
    families: dict = field(default_factory=dict)     # host -> debian | rhel | windows
    status: callable = None                          # () -> dict for range_status
    check_spec: callable = None                      # (yaml) -> (problems, diff, summary)
    save_spec: callable = None                       # (yaml) -> dict (draft summary, diff)


def validate(name: str, args: dict | None, ctx: Context) -> str | None:
    """A problem with the call (returned to the model as an error), or None."""
    if name not in TOOLS:
        return f"unknown tool '{name}'"
    if args is None:
        return "the tool input was not valid JSON; send the call again"
    schema = TOOLS[name]["input_schema"]
    missing = [k for k in schema.get("required", []) if not str(args.get(k) or "").strip()]
    if missing:
        return f"missing {', '.join(missing)}"
    if "host" in schema["properties"] and "host" in args and args["host"] not in ctx.vms:
        return f"there is no host '{args['host']}' in this range (hosts: {', '.join(sorted(ctx.vms))})"
    if name == "power":
        if args.get("action") not in lifecycle.POWER_ACTIONS:
            return f"action must be one of {', '.join(lifecycle.POWER_ACTIONS)}"
        unknown = [h for h in args.get("hosts") or [] if h not in ctx.vms]
        if unknown:
            return f"unknown hosts: {', '.join(unknown)}"
    if name == "snapshot":
        if args.get("action") not in ("list", "create", "rollback"):
            return "action must be list, create or rollback"
        if args["action"] != "list" and not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{1,39}", str(args.get("name") or "")):
            return "name must be 2-40 letters, digits, '-' or '_', starting with a letter"
    if name == "write_file" and len(str(args.get("content", ""))) > 60_000:
        return "content exceeds the guest agent's 60 KB limit; split it or generate it with a command"
    return None


def summary(name: str, args: dict) -> str:
    """One line for the feed and the approval card."""
    a = args or {}
    if name == "run_command":
        lines = str(a.get("command") or "").strip().splitlines()
        more = f" … (+{len(lines) - 1} line{'' if len(lines) == 2 else 's'})" if len(lines) > 1 else ""
        return f"{a.get('host')}: {lines[0][:200] if lines else ''}{more}"     # the card says when there is more
    if name in ("read_file", "write_file"):
        return f"{a.get('host')}: {'read' if name == 'read_file' else 'write'} {a.get('path')}"
    if name == "power":
        return f"{a.get('action')} {', '.join(a.get('hosts') or []) or 'every VM'}"
    if name == "snapshot":
        return f"snapshot {a.get('action')}{' ' + a['name'] if a.get('name') else ''}"
    if name == "update_spec":
        return f"update the spec: {str(a.get('summary', ''))[:200]}"
    return name.replace("_", " ")


def _clip(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text or "")
    return text if len(text) <= OUTPUT_LIMIT else f"[... {len(text) - OUTPUT_LIMIT:,} earlier characters cut]\n" + text[-OUTPUT_LIMIT:]


def execute(name: str, args: dict, ctx: Context) -> dict:
    """Run a validated (and, where needed, approved) call. Returns {content, error, output?, exit?, meta?}."""
    try:
        return _execute(name, args, ctx)
    except ValorError as e:
        return {"content": f"{e.message}" + (f" ({e.hint})" if e.hint else ""), "error": True}


def _vm(ctx: Context, host: str):
    vm = ctx.vms[host]
    if vm.status != "running":
        raise ValorError("vm_stopped", f"{host} is {vm.status}", hint="start it with the power tool first")
    return vm


def _execute(name: str, args: dict, ctx: Context) -> dict:
    if name == "run_command":
        vm = _vm(ctx, args["host"])
        timeout = max(5, min(MAX_TIMEOUT, int(args.get("timeout") or DEFAULT_TIMEOUT)))
        t0 = time.time()
        if ctx.families.get(args["host"]) == "windows":
            res = ctx.pve.ps(vm.vmid, f"& {{\n{args['command']}\n}} 2>&1 | Out-String -Width 220", timeout=timeout)
        else:
            res = ctx.pve.script(vm.vmid, "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "
                                          "LC_ALL=C.UTF-8 PAGER=cat SYSTEMD_PAGER= TERM=dumb\n"
                                          f"{{\n{args['command']}\n}} </dev/null\n", timeout=timeout)
        out = res.out + (("\n[stderr]\n" + res.err) if res.err.strip() else "")
        if res.timed_out:
            return {"content": _clip(out) + f"\n[timed out after {timeout} s; the command may still be running]",
                    "error": True, "output": _clip(out), "exit": None, "seconds": round(time.time() - t0, 1)}
        clipped = _clip(out)
        return {"content": f"exit code {res.exitcode}\n{clipped}", "error": res.exitcode != 0, "output": clipped,
                "exit": res.exitcode, "seconds": round(time.time() - t0, 1)}
    if name == "read_file":
        vm = _vm(ctx, args["host"])
        text = ctx.pve.file_read(vm.vmid, args["path"])
        if text is None:
            return {"content": f"{args['path']} could not be read (missing or not readable)", "error": True}
        return {"content": _clip(text), "error": False, "output": _clip(text)}
    if name == "write_file":
        vm = _vm(ctx, args["host"])
        ctx.pve.write_file(vm.vmid, args["path"], args["content"])
        return {"content": f"wrote {len(args['content']):,} characters to {args['path']}", "error": False}
    if name == "range_status":
        return {"content": json.dumps(ctx.status(), indent=1, default=str), "error": False}
    if name == "snapshot" and args["action"] == "list":
        snaps = lifecycle.list_snapshots(ctx.pve, ctx.range)
        return {"content": "\n".join(f"{s.get('name')}: {s.get('description', '')}" for s in snaps) or "no snapshots",
                "error": False}
    if name in ("power", "snapshot"):
        if name == "power":
            target = {"range": ctx.range, "action": args["action"], "hosts": args.get("hosts") or None}
            kind = "power"
        elif args["action"] == "create":
            target, kind = {"range": ctx.range, "name": args["name"], "description": args.get("description", "")}, "snapshot"
        else:
            target, kind = {"range": ctx.range, "name": args["name"], "delete_newer": False}, "rollback"
        job = ops.start(ctx.cfg, kind, target, f"chat:{ctx.username}", background=True)
        st = wait_job(ctx.cfg, job["job"])
        ok = st.get("state") == "succeeded"
        res = st.get("result") or {}
        detail = res.get("message") or res.get("error") or ""
        return {"content": f"job {job['job']} {st.get('state')}" + (f": {detail}" if detail else ""),
                "error": not ok, "meta": {"job": job["job"]}}
    if name == "update_spec":
        problems, diff, _ = ctx.check_spec(args["yaml"])
        if problems:
            return {"content": "VALOR could not use that spec:\n" + "\n".join(f"- {p}" for p in problems), "error": True}
        saved = ctx.save_spec(args["yaml"])
        counts = ", ".join(f"{v} {k}" for k, v in (saved.get("draft") or {}).items()) or "no VM changes"
        return {"content": f"Saved as the range's draft ({counts}). It is built when the person approves VALOR's plan.",
                "error": False, "meta": {"draft": saved.get("draft"), "diff": saved.get("diff", "")}}
    raise ValorError("unknown_tool", f"unknown tool '{name}'")


def wait_job(cfg, job_id: str, limit: int = JOB_WAIT) -> dict:
    end = time.time() + limit
    st: dict = {}
    while time.time() < end:
        st = jobs.status(cfg, job_id, tail=0)
        if st.get("state") in ("succeeded", "failed"):
            return st
        time.sleep(2)
    return {**st, "state": st.get("state", "unknown"), "result": {"message": f"still {st.get('state')} after {limit} s"}}
