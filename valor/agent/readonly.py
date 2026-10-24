"""Whether a range chat's tool call only reads - then it runs at once, also in Question mode.

Decided conservatively: a command counts as read-only when every part of it is on an allowlist of commands that only
report state (with conditions on their arguments where needed); anything VALOR cannot parse, any redirection into a
file, command substitution or unknown command needs the person's approval.
"""

from __future__ import annotations

import re
import shlex

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
    skip = False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if a in ("-o", "--output") and nxt == "/dev/null" or a in ("--output=/dev/null", "-o/dev/null"):
            skip = a in ("-o", "--output")                  # the body thrown away (e.g. -w '%{http_code}' checks)
            continue
        if a.startswith("-") and not a.startswith("--") and len(a) > 2 and a.endswith("o") and nxt == "/dev/null" \
                and not set(a[1:-1]) & set("oOdFTXKcDJ"):
            skip = True                                     # -so /dev/null
            continue
        if a.split("=", 1)[0] in bad:
            return False
        if a.startswith("-") and not a.startswith("--") and len(a) > 2 and set(a[1:]) & set("oOdFTXKcDJ"):
            return False                # combined short flags such as -sSo
    return True


def _wget(args):
    """Only printing a page (-O- / -qO-) or throwing it away (-O /dev/null): no files, posts or recursion."""
    bad = {"--post-data", "--post-file", "--method", "--body-data", "--body-file", "-r", "--recursive", "-m",
           "--mirror", "-P", "--directory-prefix", "-o", "--output-file", "-a", "--append-output", "-N", "-c",
           "--continue", "-x", "-e", "--execute", "-i", "--input-file", "-B", "--save-cookies", "--save-headers",
           "--delete-after", "-k", "--convert-links", "-p", "--page-requisites"}
    out = None
    for i, a in enumerate(args):
        if a.split("=", 1)[0] in bad:
            return False
        if a in ("-O", "--output-document"):
            out = args[i + 1] if i + 1 < len(args) else None
        elif a.startswith("--output-document="):
            out = a.split("=", 1)[1]
        elif a.startswith("-") and not a.startswith("--") and "O" in a[1:]:
            flags, rest = a[1:a.index("O")], a[a.index("O") + 1:]
            if set(flags) & set("rmPoacxeiNBkp"):
                return False
            out = rest or (args[i + 1] if i + 1 < len(args) else None)
        elif a.startswith("-") and not a.startswith("--") and set(a[1:]) & set("rmPoacxeiNBkp"):
            return False
    return out in ("-", "/dev/null")


def _mysqladmin(args):
    """mysqladmin ping / status / version / variables (no password on the command line)."""
    words, i = [], 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "-P", "-u", "-S", "--host", "--port", "--user", "--socket"):
            i += 2
            continue
        if a.startswith("-p") or a.startswith("--password"):
            return False
        if not a.startswith("-"):
            words.append(a)
        i += 1
    return bool(words) and all(w in ("ping", "status", "version", "variables") for w in words)


REDIS_READ = {"ping", "info", "dbsize", "role", "time", "lastsave", "get", "mget", "exists", "type", "ttl", "pttl",
              "strlen", "llen", "lrange", "scard", "smembers", "hlen", "hget", "hgetall", "zcard", "zrange", "keys", "scan"}


def _redis_cli(args):
    words, i = [], 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "-p", "-n", "-a", "--user", "--pass", "-u", "-s"):
            i += 2
            continue
        if a.startswith("-"):
            if a in ("--eval", "-x", "--pipe", "--rdb", "--functions-rdb", "-r", "--replica", "--bigkeys", "--memkeys"):
                return False
            i += 1
            continue
        words.append(a.lower())
        i += 1
    if not words:
        return False                                        # an interactive shell
    two = words[:2]
    return words[0] in REDIS_READ or two in (["config", "get"], ["client", "list"], ["client", "info"],
                                               ["memory", "stats"], ["memory", "usage"], ["slowlog", "get"])


SED_PRINT = re.compile(r"((\d+|\$|/[^/;]*/)(,(\d+|\$|/[^/;]*/))?!?[pdq]|[pdq=])(\s*;\s*((\d+|\$|/[^/;]*/)(,(\d+|\$|/[^/;]*/))?!?[pdq]|[pdq=]))*")


def _sed(args):
    """sed that only prints or skips lines (sed -n '1,120p', '/x/d', '5q'): no -i, no s/// (w and e flags), no files written."""
    scripts, i = [], 0
    while i < len(args):
        a = args[i]
        if a in ("-n", "--quiet", "--silent", "-E", "-r", "--regexp-extended", "-u", "--unbuffered", "-z"):
            i += 1
            continue
        if a in ("-e", "--expression"):
            scripts.append(args[i + 1] if i + 1 < len(args) else "")
            i += 2
            continue
        if a.startswith("-"):
            return False                # -i (in place), -f (script file), -s ...
        if not scripts:
            scripts.append(a)           # the script; the rest are files to read
        i += 1
    return bool(scripts) and all(SED_PRINT.fullmatch(x.strip()) for x in scripts)


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
    "command": lambda args: bool(args) and args[0] in ("-v", "-V"),   # command -v X: is X installed
    "sed": _sed,
    "wget": _wget,
    "redis-cli": _redis_cli,
    "fail2ban-client": _first_in("status", "ping", "version", "get", "banned"),
    "pg_isready": None,                                     # does PostgreSQL accept connections
    "psql": lambda args: bool(args) and all(a in ("-l", "--list", "-w", "--no-password", "-X") or
                                            re.fullmatch(r"(-[hpUd]|--(host|port|username|dbname))(=\S+)?|[\w.@/-]+", a)
                                            for a in args) and any(a in ("-l", "--list") for a in args),
    "mysqladmin": _mysqladmin,
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


NET_DEVICE = re.compile(r"/dev/(tcp|udp)/[A-Za-z0-9.-]+/\d{1,5}")
NET = "\x00net"                         # marks a command that opens a /dev/tcp|udp connection


def linux_readonly(command: str, depth: int = 0) -> bool:
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
        if tok in (";", "|", "||", "&&", "|&") or set(tok) <= set("()"):
            segments.append([])         # ( a || b ) | head: a group only groups - every command in it is checked
        elif set(tok) <= set("<>&()"):
            if tok in ("&", ">>", "&>>"):
                return False            # background jobs, appending to files
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if tok in ("<", ">", "<>") and NET_DEVICE.fullmatch(target):
                if segments[-1] and segments[-1][-1].isdigit():
                    segments[-1].pop()  # 3<>/dev/tcp/10.0.0.5/5432: a connection test ...
                segments[-1].append(NET)            # ... only for exec / echo / true (no data sent out)
                i += 2
                continue
            fd = segments[-1].pop() if segments[-1] and segments[-1][-1].isdigit() and tok.startswith(">") else ""
            if tok != "<" and f"{fd}{tok}{target}" not in SAFE_REDIRECTS:
                return False            # only 2>/dev/null, 2>&1 ... (and reading input with <)
            i += 2
            continue
        else:
            segments[-1].append(tok)
        i += 1
    return all(_linux_segment_ok(s, depth) for s in segments if s) and any(segments)


def _linux_segment_ok(words: list[str], depth: int = 0) -> bool:
    if NET in words:
        return [w for w in words if w != NET] in ([], ["exec"], ["echo"], ["true"], [":"])
    words = list(words)
    while words and words[0] in WRAPPERS:           # sudo X, timeout 10 X, nice -n 5 X: judge X
        w = words.pop(0)
        while words and (words[0].startswith("-") or w in ("timeout", "nice") and re.fullmatch(r"[\d.]+[smhd]?", words[0])):
            words.pop(0)
    if not words or "=" in words[0]:
        return False                    # nothing left, or an environment assignment (FOO=bar cmd)
    cmd, args = words[0].rsplit("/", 1)[-1], words[1:]
    if cmd == "su" and depth < 2:                   # su [-] USER -c 'cmd': judge cmd
        rest = [w for w in args if w not in ("-", "-l", "--login")]
        return len(rest) == 3 and rest[1] == "-c" and linux_readonly(rest[2], depth + 1)
    if cmd == "runuser" and len(args) >= 4 and args[0] == "-u" and args[2] == "--" and depth < 2:
        return _linux_segment_ok(args[3:], depth + 1)       # runuser -u USER -- cmd
    if cmd == "exec" and not args:
        return True                     # exec 3<>/dev/tcp/...: only its (checked) redirections
    if cmd in ("bash", "sh") and len(args) == 2 and args[0] == "-c" and depth < 2:
        return linux_readonly(args[1], depth + 1)       # bash -c '<a read-only script>'
    if args == ["--version"] and re.fullmatch(r"[a-z][a-z0-9._+-]*", cmd):
        return True                     # any program's version
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
