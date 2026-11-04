# *opens a toolbox for the AI* OwO valor/agent/tools.py: the tools the AI may use in a range chat! ^w^
# run_command / read_file / write_file work INSIDE the range's VMs (over the QEMU guest agent); power and snapshot
# run as normal VALOR jobs; range_status reports the state; update_spec records changes in the spec's draft.
# Every call goes: validate() (problems go back to the model) -> read-only? (runs at once; otherwise the person must
# approve it in the chat) -> execute(). The model never touches the Proxmox API itself *wags tail*
"""The tools a range chat's model may call, and how VALOR runs them: commands and files inside the range's VMs (QEMU
guest agent), power and snapshots (VALOR jobs), the range's status and its spec.

A call goes through three steps (driven by web/chats/turn.py): `validate` it (a problem goes back to the model),
decide whether it only reads (readonly.py: then it runs at once, otherwise the person approves it first), and
`execute` it. The model never touches the Proxmox API itself.
"""

# Lazy type hints UwU
from __future__ import annotations

# json (status output), re (checks, colour codes), time (durations, waiting), dataclass/field (Context) >w<
import json
import re
import time
from dataclasses import dataclass, field

# jobs (wait for jobs), lifecycle (power actions, snapshot list), ops (start jobs), ValorError OwO
from .. import jobs, lifecycle, ops
from ..errors import ValorError

# Limits ^w^: a command's output kept for the model (16,000 chars); default and maximum command timeouts (2 min /
# 15 min); how long to wait for a power/snapshot job (30 min)
OUTPUT_LIMIT = 16_000           # characters of a command's output kept for the model and the feed
DEFAULT_TIMEOUT, MAX_TIMEOUT = 120, 900
JOB_WAIT = 1800                 # seconds to wait for a power or snapshot job

# HOST: the shared schema of the "host" argument UwU
HOST = {"type": "string", "description": "the host name in the range's spec ('rtr' is the router)"}
# TOOLS: every tool's definition as the model sees it: a description (the model reads it to decide when to use
# the tool) and a JSON Schema for its input. additionalProperties False = no extra arguments >w<
TOOLS = {
    # run_command: run a command or short script (bash on Linux, PowerShell on Windows), with a purpose sentence
    # for the approval card and an optional timeout OwO
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
    # read_file: read a text file inside a VM ^w^
    "read_file": {
        "description": "Read a text file inside a VM (at most 16,000 characters are returned).",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["host", "path"],
                         "properties": {"host": HOST, "path": {"type": "string"}}}},
    # write_file: replace a text file inside a VM (at most 60 KB, the guest agent's limit); needs approval UwU
    "write_file": {
        "description": "Write a text file inside a VM (replaces it; at most 60 KB). Needs the person's approval.",
        "input_schema": {"type": "object", "additionalProperties": False,
                         "required": ["host", "path", "content", "purpose"],
                         "properties": {"host": HOST, "path": {"type": "string"}, "content": {"type": "string"},
                                        "purpose": {"type": "string"}}}},
    # power: start / shutdown / reboot / stop some or all VMs; needs approval >w<
    "power": {
        "description": "Start, shut down (clean, forced after 3 minutes), reboot or stop VMs of the range. Needs "
                       "the person's approval.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["action", "purpose"],
                         "properties": {"action": {"type": "string", "enum": list(lifecycle.POWER_ACTIONS)},
                                        "hosts": {"type": "array", "items": {"type": "string"},
                                                  "description": "default: every VM of the range"},
                                        "purpose": {"type": "string"}}}},
    # snapshot: list, create, or roll back; the name must look like a snapshot name OwO
    "snapshot": {
        "description": "List the range's snapshots, take one of every VM (disk only), or roll every VM back to one "
                       "(VALOR verifies the range afterwards). Taking and rolling back need the person's approval.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["action"],
                         "properties": {"action": {"type": "string", "enum": ["list", "create", "rollback"]},
                                        "name": {"type": "string", "pattern": "^[a-zA-Z][a-zA-Z0-9_-]{1,39}$"},
                                        "description": {"type": "string"}, "purpose": {"type": "string"}}}},
    # range_status: the range's VMs, last build/verification and draft (no arguments) ^w^
    "range_status": {
        "description": "The range's VMs (power state, OS, address), its last build and verification, its draft.",
        "input_schema": {"type": "object", "additionalProperties": False, "properties": {}}},
    # update_spec: save a complete updated spec as the draft (validated first; built only after the person approves
    # the plan) UwU
    "update_spec": {
        "description": "Save the complete updated range spec as the range's draft, to record a change so a rebuild "
                       "reproduces it. VALOR validates it first (problems come back as the result) and shows the "
                       "person the diff to approve; the draft is built only when they approve VALOR's plan.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["yaml", "summary"],
                         "properties": {"yaml": {"type": "string", "description": "the complete spec"},
                                        "summary": {"type": "string", "description": "what changed and why"}}}},
}
# READ_TOOLS: the tools that can only look. Question mode gets only those; Code mode gets them all >w<
READ_TOOLS = ("run_command", "read_file", "range_status")
MODE_TOOLS = {"question": READ_TOOLS, "code": tuple(TOOLS)}


# tool_specs: the tool list for a mode, in the format sent to the model OwO
def tool_specs(mode: str) -> list[dict]:
    """The tool definitions sent to the model in a chat mode (Question mode: read-only tools only)."""
    return [{"name": name, **TOOLS[name]} for name in MODE_TOOLS.get(mode, ())]


# Context: what a tool call needs ^w^
#   cfg/pve      the engine config and Proxmox client
#   range        the range's name; username = who is chatting (jobs are tagged "chat:<user>")
#   vms          host -> its VM; families = host -> OS family
#   status / check_spec / save_spec  callbacks provided by the chat turn (they need the web side's helpers)
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


# validate: what's wrong with a call (a sentence for the model), or None when it's fine UwU
def validate(name: str, args: dict | None, ctx: Context) -> str | None:
    """A problem with the call (returned to the model as an error), or None."""
    # Unknown tool, or arguments that weren't valid JSON >w<
    if name not in TOOLS:
        return f"unknown tool '{name}'"
    if args is None:
        return "the tool input was not valid JSON; send the call again"
    # Required arguments must be present and non-empty OwO
    schema = TOOLS[name]["input_schema"]
    missing = [key for key in schema.get("required", []) if not str(args.get(key) or "").strip()]
    if missing:
        return f"missing {', '.join(missing)}"
    # A host must be one of this range's hosts (the message lists them, so the model can correct itself) ^w^
    if "host" in schema["properties"] and "host" in args and args["host"] not in ctx.vms:
        return f"there is no host '{args['host']}' in this range (hosts: {', '.join(sorted(ctx.vms))})"
    # power: a valid action, known hosts UwU
    if name == "power":
        if args.get("action") not in lifecycle.POWER_ACTIONS:
            return f"action must be one of {', '.join(lifecycle.POWER_ACTIONS)}"
        unknown = [host for host in args.get("hosts") or [] if host not in ctx.vms]
        if unknown:
            return f"unknown hosts: {', '.join(unknown)}"
    # snapshot: a valid action, and a valid name for create/rollback >w<
    if name == "snapshot":
        if args.get("action") not in ("list", "create", "rollback"):
            return "action must be list, create or rollback"
        if args["action"] != "list" and not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{1,39}", str(args.get("name") or "")):
            return "name must be 2-40 letters, digits, '-' or '_', starting with a letter"
    # write_file: content within the guest agent's 60 KB limit OwO
    if name == "write_file" and len(str(args.get("content", ""))) > 60_000:
        return "content exceeds the guest agent's 60 KB limit; split it or generate it with a command"
    return None


# summary: one line describing a call, for the live feed and the approval card ^w^
def summary(name: str, args: dict) -> str:
    """One line for the feed and the approval card."""
    args = args or {}
    # run_command: "host: first line … (+N lines)" (the card shows the whole command) UwU
    if name == "run_command":
        lines = str(args.get("command") or "").strip().splitlines()
        more = f" … (+{len(lines) - 1} line{'' if len(lines) == 2 else 's'})" if len(lines) > 1 else ""
        return f"{args.get('host')}: {lines[0][:200] if lines else ''}{more}"     # the card says when there is more
    # Files, power, snapshots and spec updates get their own short wording >w<
    if name in ("read_file", "write_file"):
        return f"{args.get('host')}: {'read' if name == 'read_file' else 'write'} {args.get('path')}"
    if name == "power":
        return f"{args.get('action')} {', '.join(args.get('hosts') or []) or 'every VM'}"
    if name == "snapshot":
        return f"snapshot {args.get('action')}{' ' + args['name'] if args.get('name') else ''}"
    if name == "update_spec":
        return f"update the spec: {str(args.get('summary', ''))[:200]}"
    return name.replace("_", " ")


# _clip: clean a command's output for the model: remove terminal colour codes (escape sequences), and keep only
# the LAST OUTPUT_LIMIT characters (the end is usually what matters), noting how much was cut OwO
def _clip(text: str) -> str:
    """Output for the model: terminal colour codes removed, at most OUTPUT_LIMIT characters (the end is kept)."""
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text or "")
    if len(text) <= OUTPUT_LIMIT:
        return text
    return f"[... {len(text) - OUTPUT_LIMIT:,} earlier characters cut]\n" + text[-OUTPUT_LIMIT:]


# execute: run a validated (and approved, if needed) call. Engine errors become an error result for the model
# instead of crashing the chat ^w^
def execute(name: str, args: dict, ctx: Context) -> dict:
    """Run a validated (and, where needed, approved) call. Returns {content, error, output?, exit?, meta?}."""
    run = _RUNNERS.get(name)
    try:
        if run is None:
            raise ValorError("unknown_tool", f"unknown tool '{name}'")
        return run(args, ctx)
    except ValorError as e:
        return {"content": f"{e.message}" + (f" ({e.hint})" if e.hint else ""), "error": True}


# _vm: the host's VM, which must be running (the guest agent only answers then) UwU
def _vm(ctx: Context, host: str):
    vm = ctx.vms[host]
    if vm.status != "running":
        raise ValorError("vm_stopped", f"{host} is {vm.status}", hint="start it with the power tool first")
    return vm


# ---- One function per tool >w<
# ---- One function per tool ----

# _run_command: run it and report the exit code and output OwO
def _run_command(args: dict, ctx: Context) -> dict:
    vm = _vm(ctx, args["host"])
    # Clamp the timeout to 5..MAX_TIMEOUT seconds ^w^
    timeout = max(5, min(MAX_TIMEOUT, int(args.get("timeout") or DEFAULT_TIMEOUT)))
    started = time.time()
    # Windows: run as a PowerShell script block, errors merged in, formatted 220 columns wide UwU
    if ctx.families.get(args["host"]) == "windows":
        result = ctx.pve.ps(vm.vmid, f"& {{\n{args['command']}\n}} 2>&1 | Out-String -Width 220", timeout=timeout)
    else:
        # Linux: a fixed PATH, UTF-8, no pager (PAGER=cat), a dumb terminal (no colours), and stdin from /dev/null so
        # nothing waits for input; the command sits in a { } group >w<
        # A predictable environment: no pager or colours waiting for a terminal, and no stdin to wait on.
        result = ctx.pve.script(vm.vmid, "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "
                                         "LC_ALL=C.UTF-8 PAGER=cat SYSTEMD_PAGER= TERM=dumb\n"
                                         f"{{\n{args['command']}\n}} </dev/null\n", timeout=timeout)
    # Add stderr under a "[stderr]" heading when there is some OwO
    output = result.out + (("\n[stderr]\n" + result.err) if result.err.strip() else "")
    seconds = round(time.time() - started, 1)
    # A timeout is reported as an error (the command may still be running in the VM!) ^w^
    if result.timed_out:
        return {"content": _clip(output) + f"\n[timed out after {timeout} s; the command may still be running]",
                "error": True, "output": _clip(output), "exit": None, "seconds": seconds}
    # Otherwise: the exit code first, then the output; a non-zero exit counts as an error UwU
    clipped = _clip(output)
    return {"content": f"exit code {result.exitcode}\n{clipped}", "error": result.exitcode != 0, "output": clipped,
            "exit": result.exitcode, "seconds": seconds}


# _read_file: read a file (None = missing or unreadable) >w<
def _read_file(args: dict, ctx: Context) -> dict:
    vm = _vm(ctx, args["host"])
    text = ctx.pve.file_read(vm.vmid, args["path"])
    if text is None:
        return {"content": f"{args['path']} could not be read (missing or not readable)", "error": True}
    return {"content": _clip(text), "error": False, "output": _clip(text)}


# _write_file: write a file and say how much was written OwO
def _write_file(args: dict, ctx: Context) -> dict:
    vm = _vm(ctx, args["host"])
    ctx.pve.write_file(vm.vmid, args["path"], args["content"])
    return {"content": f"wrote {len(args['content']):,} characters to {args['path']}", "error": False}


# _range_status: the status callback's data as JSON ^w^
def _range_status(args: dict, ctx: Context) -> dict:
    return {"content": json.dumps(ctx.status(), indent=1, default=str), "error": False}


# _power: a power job UwU
def _power(args: dict, ctx: Context) -> dict:
    return _run_job(ctx, "power", {"range": ctx.range, "action": args["action"], "hosts": args.get("hosts") or None})


# _snapshot: list directly; create or roll back as jobs (rollback never deletes newer snapshots from a chat) >w<
def _snapshot(args: dict, ctx: Context) -> dict:
    if args["action"] == "list":
        snaps = lifecycle.list_snapshots(ctx.pve, ctx.range)
        lines = [f"{snap.get('name')}: {snap.get('description', '')}" for snap in snaps]
        return {"content": "\n".join(lines) or "no snapshots", "error": False}
    if args["action"] == "create":
        return _run_job(ctx, "snapshot", {"range": ctx.range, "name": args["name"],
                                          "description": args.get("description", "")})
    return _run_job(ctx, "rollback", {"range": ctx.range, "name": args["name"], "delete_newer": False})


# _run_job: start an ordinary VALOR job (engine lock, task log, audit) and wait for it to finish OwO
def _run_job(ctx: Context, kind: str, target: dict) -> dict:
    """Power and snapshot actions run as ordinary VALOR jobs (the engine lock, the task log); wait for the result."""
    job = ops.start(ctx.cfg, kind, target, f"chat:{ctx.username}", background=True)
    status = wait_job(ctx.cfg, job["job"])
    result = status.get("result") or {}
    detail = result.get("message") or result.get("error") or ""
    return {"content": f"job {job['job']} {status.get('state')}" + (f": {detail}" if detail else ""),
            "error": status.get("state") != "succeeded", "meta": {"job": job["job"]}}


# _update_spec: validate the model's spec; on success save it as the draft and report what the draft changes ^w^
def _update_spec(args: dict, ctx: Context) -> dict:
    """Validate the model's spec, then save it as the range's draft (built only after the person approves)."""
    problems, diff, _ = ctx.check_spec(args["yaml"])
    if problems:
        return {"content": "VALOR could not use that spec:\n" + "\n".join(f"- {p}" for p in problems), "error": True}
    saved = ctx.save_spec(args["yaml"])
    counts = ", ".join(f"{count} {action}" for action, count in (saved.get("draft") or {}).items()) or "no VM changes"
    return {"content": f"Saved as the range's draft ({counts}). It is built when the person approves VALOR's plan.",
            "error": False, "meta": {"draft": saved.get("draft"), "diff": saved.get("diff", "")}}


# _RUNNERS: which function runs which tool UwU
_RUNNERS = {"run_command": _run_command, "read_file": _read_file, "write_file": _write_file,
            "range_status": _range_status, "power": _power, "snapshot": _snapshot, "update_spec": _update_spec}


# wait_job: poll a job every 2 s until it succeeds or fails, or until `limit` seconds pass >w< *taps paw impatiently*
def wait_job(cfg, job_id: str, limit: int = JOB_WAIT) -> dict:
    end = time.time() + limit
    status: dict = {}
    while time.time() < end:
        status = jobs.status(cfg, job_id, tail=0)
        if status.get("state") in ("succeeded", "failed"):
            return status
        time.sleep(2)
    return {**status, "state": status.get("state", "unknown"),
            "result": {"message": f"still {status.get('state')} after {limit} s"}}
