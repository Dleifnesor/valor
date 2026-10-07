"""Tools the AI uses in a range chat: commands and files inside the range's VMs (QEMU guest agent), power and
snapshots (VALOR jobs), the range's status and its spec.

Reads run at once. Everything else needs the person's approval in the chat (unless they allowed all actions for that
chat), and Question mode gets the read-only tools only. Whether a command only reads is decided here,
conservatively: a command counts as read-only when every part of it is on an allowlist of commands that only report
state; anything VALOR cannot parse, any redirection into a file, command substitution or unknown command needs approval.
"""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass, field

from . import jobs, lifecycle, ops
from .errors import ValorError

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


# ---------------------------------------------------------------------- read-only commands
# Linux commands that only report state, with the extra condition (on their arguments) some of them need.
POSITIONAL = lambda args: [a for a in args if not a.startswith("-")]          # noqa: E731


def _first_in(*names, bare_ok=False):
    allowed = set(names)
    return lambda args: (not POSITIONAL(args) and bare_ok) or bool(POSITIONAL(args)) and POSITIONAL(args)[0] in allowed


def _flags_in(*flags, need=()):
    allowed = set(flags)
    return lambda args: all(a in allowed or not a.startswith("-") for a in args) and \
        (not need or any(a in need for a in args))


def _only_flags(*flags):
    allowed = set(flags)
    return lambda args: bool(args) and all(a in allowed for a in args)


def _no_flags(*flags):
    bad = set(flags)
    return lambda args: not any(a.split("=", 1)[0] in bad for a in args)


def _ip(args):
    pos = POSITIONAL(args)
    return bool(pos) and pos[0] in {"a", "addr", "address", "r", "route", "l", "link", "n", "neigh", "neighbor",
                                    "neighbour", "rule", "maddr", "netns"} \
        and (len(pos) == 1 or pos[1] in ("show", "list", "ls", "get", "lst") or pos[0] in ("r", "route") and pos[1] == "get")


def _curl(args):
    bad = {"-o", "--output", "-O", "--remote-name", "-d", "--data", "--data-binary", "--data-raw", "--data-urlencode",
           "-F", "--form", "-T", "--upload-file", "-X", "--request", "-K", "--config", "-c", "--cookie-jar", "--json",
           "-D", "--dump-header", "--remote-name-all", "-J"}
    for a in args:
        if a.split("=", 1)[0] in bad:
            return False
        if a.startswith("-") and not a.startswith("--") and len(a) > 2 and set(a[1:]) & set("oOdFTXKcDJ"):
            return False                # combined short flags such as -sSo
    return True


def _rpm(args):
    return bool(args) and (args[0].startswith("-q") or args[0] in ("--query", "-V", "--verify"))


def _dpkg(args):
    return bool(args) and args[0] in ("-l", "-L", "-s", "-S", "-p", "--list", "--listfiles", "--status", "--search",
                                      "--print-avail", "--print-architecture", "--get-selections", "-c", "--contents",
                                      "--info", "-I", "--verify", "-V", "--audit", "-C")


LINUX_READ = {
    **{c: None for c in (
        "cat", "head", "tail", "grep", "egrep", "fgrep", "zgrep", "zcat", "wc", "sort", "uniq", "cut", "tr", "column",
        "ls", "stat", "file", "df", "du", "free", "uptime", "uname", "id", "whoami", "w", "who", "last", "lastlog",
        "getent", "ps", "pgrep", "pidof", "netstat", "lsblk", "blkid", "findmnt", "lscpu", "lsmod", "lspci", "lsusb",
        "lsof", "printenv", "echo", "printf", "true", "false", "test", "[", "which", "readlink", "realpath", "basename",
        "dirname", "md5sum", "sha1sum", "sha256sum", "diff", "cmp", "strings", "tree", "getenforce", "sestatus", "dig",
        "nslookup", "host", "tracepath", "traceroute", "nproc", "named-checkconf", "named-checkzone", "testparm",
        "dmidecode", "lshw", "base64", "xxd", "od", "sleep", "pwd", "cd", "klist", "locale", "groups", "users",
        "lslogins", "ldd", "getfacl", "sha512sum", "nl", "rev", "fold", "expand", "seq", "hexdump")},
    "sort": _no_flags("-o", "--output"),
    "find": _no_flags("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"),
    "hostname": lambda args: all(a.startswith("-") and a not in ("-F", "--file", "-b", "--boot") for a in args),
    "date": lambda args: all(a.startswith("+") or a in ("-u", "-R", "-I", "--utc", "--rfc-3339=seconds") or
                             a.startswith("-d") or a.startswith("--date") or a.startswith("-I") for a in args),
    "sysctl": lambda args: not any("=" in a or a in ("-w", "--write", "-p", "--load", "--system") for a in args),
    "dmesg": _no_flags("-c", "-C", "--clear", "--read-clear", "-D", "-E", "-n", "--console-off", "--console-on",
                       "--console-level"),
    "journalctl": _no_flags("--vacuum-size", "--vacuum-time", "--vacuum-files", "--rotate", "--flush", "--setup-keys",
                            "--sync", "--relinquish-var", "--smart-relinquish-var", "--update-catalog"),
    "ss": _no_flags("-K", "--kill"),
    "arp": lambda args: all(a in ("-a", "-n", "-v", "-e") or not a.startswith("-") for a in args) and
                        not any(a in ("-d", "-s", "-f") for a in args),
    "route": lambda args: all(a.startswith("-") and a in ("-n", "-e", "-v", "-ee", "-4", "-6") for a in args),
    "ping": lambda args: any(a.startswith("-c") for a in args),
    "nc": lambda args: any(a.startswith("-") and not a.startswith("--") and "z" in a for a in args) and not any(
        a in ("--exec", "--sh-exec", "--listen") or a.startswith("-") and not a.startswith("--") and set(a[1:]) & set("ecl")
        for a in args),
    "curl": _curl,
    "ip": _ip,
    "env": lambda args: not args,
    "systemctl": _first_in("status", "is-active", "is-enabled", "is-failed", "list-units", "list-unit-files", "show",
                           "cat", "list-timers", "list-sockets", "list-dependencies", "get-default", bare_ok=True),
    "timedatectl": _first_in("status", "show", "timesync-status", "show-timesync", bare_ok=True),
    "hostnamectl": _first_in("status", bare_ok=True),
    "chronyc": _first_in("sources", "sourcestats", "tracking", "activity", "clients", "serverstats", "ntpdata"),
    "apt": _first_in("list", "show", "policy", "search"),
    "apt-cache": _first_in("show", "policy", "search", "depends", "rdepends", "showpkg", "madison"),
    "dnf": _first_in("list", "info", "repolist", "search", "provides", "repoquery"),
    "yum": _first_in("list", "info", "repolist", "search", "provides"),
    "docker": _first_in("ps", "images", "inspect", "logs", "version", "info", "port", "top", "stats"),
    "podman": _first_in("ps", "images", "inspect", "logs", "version", "info", "port", "top", "stats"),
    "ufw": _first_in("status"),
    "nft": lambda args: not any(a in ("-f", "--file", "-i", "--interactive") for a in args)
                        and _first_in("list")(args),
    "wg": _first_in("show"),
    "realm": _first_in("list", "discover"),
    "rpm": _rpm,
    "dpkg": _dpkg,
    "dpkg-query": _no_flags(),
    "iptables": _flags_in("-L", "-S", "--list", "--list-rules", "-n", "--numeric", "-v", "--verbose", "-x",
                          "--line-numbers", "-t", "--table", need=("-L", "-S", "--list", "--list-rules")),
    "ip6tables": _flags_in("-L", "-S", "--list", "--list-rules", "-n", "--numeric", "-v", "--verbose", "-x",
                           "--line-numbers", "-t", "--table", need=("-L", "-S", "--list", "--list-rules")),
    "firewall-cmd": lambda args: bool(args) and all(
        re.fullmatch(r"--(state|list-[a-z-]+|get-[a-z-]+|query-[a-z-]+|info-[a-z-]+=?\S*|zone=\S+|permanent)", a)
        for a in args),
    "semanage": lambda args: any(a in ("-l", "--list") for a in args) and all(
        a in ("-l", "--list", "-n", "-C", "--noheading", "--locallist") or not a.startswith("-") for a in args),
    "postconf": _no_flags("-e", "-X", "-#", "-M", "-F", "-P", "-c"),
    "sshd": _only_flags("-T", "-t"),
    "nginx": _only_flags("-t", "-T", "-V", "-v"),
    "apachectl": _only_flags("-t", "-S", "-M", "-V", "configtest"),
    "apache2ctl": _only_flags("-t", "-S", "-M", "-V", "configtest"),
    "httpd": _only_flags("-t", "-S", "-M", "-V"),
}
SAFE_REDIRECTS = {">/dev/null", "1>/dev/null", "2>/dev/null", "&>/dev/null", "2>&1", "1>&2", ">&2"}
WRAPPERS = ("sudo", "timeout", "nice", "stdbuf")


def linux_readonly(command: str) -> bool:
    text = command.strip()
    if not text or any(s in text for s in ("$(", "`", "<(", ">(", "<<")):
        return False
    try:
        lex = shlex.shlex(text.replace("\n", " ; "), posix=True, punctuation_chars=";&|<>()")
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return False                    # unbalanced quotes
    segments: list[list[str]] = [[]]
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in (";", "|", "||", "&&", "|&"):
            segments.append([])
        elif set(tok) <= set("<>&()"):
            if tok in ("(", ")", "&", ">>", "&>>"):
                return False            # subshells, background jobs, appending to files
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            fd = segments[-1].pop() if segments[-1] and segments[-1][-1].isdigit() and tok.startswith(">") else ""
            if tok != "<" and f"{fd}{tok}{target}" not in SAFE_REDIRECTS:
                return False            # only 2>/dev/null, 2>&1 ... (and reading input with <)
            i += 2
            continue
        else:
            segments[-1].append(tok)
        i += 1
    return all(_linux_segment_ok(s) for s in segments if s) and any(segments)


def _linux_segment_ok(words: list[str]) -> bool:
    words = list(words)
    while words and words[0] in WRAPPERS:           # sudo X, timeout 10 X, nice -n 5 X: judge X
        w = words.pop(0)
        while words and (words[0].startswith("-") or w in ("timeout", "nice") and re.fullmatch(r"[\d.]+[smhd]?", words[0])):
            words.pop(0)
    if not words or "=" in words[0]:
        return False                    # nothing left, or an environment assignment (FOO=bar cmd)
    cmd, args = words[0].rsplit("/", 1)[-1], words[1:]
    if cmd not in LINUX_READ:
        return False
    check = LINUX_READ[cmd]
    return check is None or bool(check(args))


# PowerShell: reading cmdlet verbs, executables/aliases that only report (with their allowed arguments), and methods
PS_VERBS = {"Get", "Test", "Resolve", "Measure", "Select", "Where", "Sort", "Format", "Group", "Compare", "Find",
            "ConvertTo", "ConvertFrom"}
PS_OK = {"Out-String", "Out-Host", "Out-Null", "Write-Output", "Write-Host"}
PS_ALIASES = {"dir", "ls", "cat", "type", "gc", "gci", "gi", "gp", "gsv", "gps", "gwmi", "gcim", "select", "where",
              "sort", "ft", "fl", "fw", "gm", "measure", "group", "?", "echo", "pwd", "gl", "gdr", "gal", "gcm"}


def _all_slash_in(*opts):
    allowed = {o.lower() for o in opts}
    return lambda args: all(a.split(":")[0] in allowed for a in args if a.startswith("/"))


PS_EXES = {
    **{c: None for c in ("whoami", "hostname", "systeminfo", "netstat", "nslookup", "ping", "tracert", "getmac",
                         "tasklist", "quser", "driverquery", "ver", "findstr", "dsquery", "dcdiag", "nltest")},
    "ipconfig": lambda args: all(a in ("/all", "/displaydns") for a in args),
    "route": lambda args: args[:1] == ["print"],
    "arp": lambda args: all(a in ("-a", "-g", "-v") or not a.startswith("-") for a in args),
    "sc.exe": lambda args: bool(args) and args[0] in ("query", "qc", "queryex", "qdescription", "qfailure",
                                                      "getdisplayname", "sdshow"),
    "net": lambda args: bool(args) and args[0] in ("user", "users", "localgroup", "group", "share", "accounts", "view",
                                                   "config", "session", "statistics", "start", "time")
                        and not any(a.startswith("/") for a in args)
                        and (len(args) == 1 or args[0] not in ("start", "session", "time") and len(args) == 2),
    "repadmin": lambda args: bool(args) and args[0] in ("/showrepl", "/replsummary", "/showobjmeta", "/showattr",
                                                        "/queue", "/showconn", "/bridgeheads", "/istg", "/viewlist",
                                                        "/showutdvec"),
    "w32tm": lambda args: bool(args) and args[0] in ("/query", "/stripchart", "/tz", "/monitor"),
    "auditpol": lambda args: bool(args) and args[0] in ("/get", "/list"),
    "gpresult": _all_slash_in("/r", "/z", "/v", "/scope", "/user", "/s", "/u"),
    "wevtutil": lambda args: bool(args) and args[0] in ("qe", "query-events", "gl", "get-log", "el", "enum-logs",
                                                        "gli", "get-log-info", "gp", "get-publisher", "ep",
                                                        "enum-publishers"),
    "klist": lambda args: all(a in ("tickets", "tgt", "sessions", "-li", "0x3e7") for a in args),
    "certutil": lambda args: bool(args) and args[0] in ("-store", "-dump", "-verify", "-viewstore"),
    "icacls": lambda args: not any(a.startswith("/") for a in args),
    "query": lambda args: bool(args) and args[0] in ("user", "session", "process"),
}
SAFE_METHODS = {"ToString", "ToLower", "ToUpper", "Trim", "TrimEnd", "TrimStart", "Split", "Substring", "Contains",
                "StartsWith", "EndsWith", "GetType", "IndexOf", "Replace", "Equals", "ToShortDateString",
                "ToLongDateString", "ToUniversalTime", "PadLeft", "PadRight", "Join", "Format", "GetValue",
                "GetValueNames", "GetSubKeyNames"}
PS_TOKEN = re.compile(r"""'(?:[^']|'')*'|"(?:[^"`]|`.)*"|[|;{}()]|[^\s|;{}()'"]+""")


def powershell_readonly(command: str) -> bool:
    text = command.strip()
    if not text or re.search(r"::|\$\(|@['\"]|`|#|\binvoke-|\biex\b|\bicm\b|\bstart-", text, re.I):
        return False                    # static .NET calls, subexpressions, here-strings, comments, Invoke-*
    if re.search(r"(^|[\s;|{(=])&\s*\S", text) or re.search(r"(^|[;|{(])\s*\.\s+\S", text):
        return False                    # call operator, dot-sourcing
    if re.search(r"\d?>>?(?!\s*\$null|&1)", text):
        return False                    # redirection into a file (> $null and 2>&1 are fine)
    for m in re.finditer(r"\.\s*([A-Za-z_]\w*)\s*\(", text):
        if m.group(1) not in SAFE_METHODS:
            return False                # e.g. (Get-Service x).Stop()
    tokens = PS_TOKEN.findall(text)
    if any(tok.startswith('"') and "$" in tok for tok in tokens):
        return False                    # expandable strings can run code
    start, current = True, None
    for tok in tokens + [";"]:
        if tok in ("|", ";", "{", "}", "(", ")", "="):
            if current and not _ps_command_ok(current):
                return False
            current, start = None, tok in ("|", ";", "{", "(", "=")
            continue
        if "=" in tok:
            return False                # assignments inside tokens ($x=rm ...), hashtables: let the person look
        if re.fullmatch(r"[A-Za-z]+-[A-Za-z]+", tok) and tok not in PS_OK and tok.split("-")[0].title() not in PS_VERBS:
            return False                # any changing cmdlet anywhere (script blocks included)
        if start:
            start = False
            if tok[0] in "$'\"-[" or re.fullmatch(r"[\d.,]+", tok):
                continue                # an expression, not a command
            current = [tok]
        elif current is not None:
            current.append(tok)
    return True


def _ps_command_ok(words: list[str]) -> bool:
    cmd = words[0]
    if re.fullmatch(r"[A-Za-z]+-[A-Za-z]+", cmd):
        return cmd in PS_OK or cmd.split("-")[0].title() in PS_VERBS
    low = cmd.lower()
    if low in PS_ALIASES:
        return True
    exe = low if low in PS_EXES else low[:-4] if low.endswith(".exe") and low[:-4] in PS_EXES else None
    if exe is None:
        return False
    check = PS_EXES[exe]
    return check is None or bool(check([a.lower() for a in words[1:]]))


def is_readonly(name: str, args: dict, family: str) -> bool:
    if name in ("read_file", "range_status"):
        return True
    if name == "snapshot":
        return args.get("action") == "list"
    if name == "run_command":
        cmd = str(args.get("command") or "")
        return powershell_readonly(cmd) if family == "windows" else linux_readonly(cmd)
    return False


# ---------------------------------------------------------------------- execution
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
        return f"{a.get('host')}: {a.get('command', '').strip().splitlines()[0][:200] if a.get('command') else ''}"
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
        import json
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
