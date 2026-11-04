# *talks to Proxmox very politely* OwO valor/pve.py is THE Proxmox API client! ^w^
# This is the ONLY place in VALOR that calls the Proxmox API. It uses a least-privilege API token (it may only touch
# VMs in VALOR's pool, its storage and its bridges). Every request goes through call(), which turns errors into
# clear ValorErrors with hints. Long operations return a task id that wait_task() follows. Inside VMs, VALOR works
# ONLY through the QEMU guest agent (no SSH, no network access into ranges): exec, script, ps, write_file and
# file_read, with the agent's quirks hidden by the helpers below *wags tail*
"""Proxmox VE API client. The engine is the only component that calls the Proxmox API.

One PVE object wraps the API with the engine's least-privilege token (it may only touch VMs in VALOR's range pool,
its storage and its bridges). Every call goes through `call()`, which turns Proxmox and network errors into
ValorErrors with a readable message and, where it helps, a hint. Long operations (clone, power, snapshots) return
a task ID that `wait_task` follows until it ends.

Inside the VMs VALOR works only through the QEMU guest agent (no SSH, no network access to range VMs): `exec`,
`script` (bash), `ps` (PowerShell), `write_file` and `file_read`. The agent API has quirks that the helpers below
hide; each is described where it is handled.
"""

# Lazy type hints UwU
from __future__ import annotations

# json (the token file), re (error text), time (waiting), urllib.parse (URL-encoding), dataclass (ExecResult),
# Path (files) >w<
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

# proxmoxer: a small Proxmox API library (attribute access builds the URL: api.nodes(n).qemu(id)...);
# ResourceException = an HTTP error from Proxmox OwO
from proxmoxer import ProxmoxAPI
from proxmoxer.core import ResourceException

# ValorError ^w^
from .errors import ValorError


# ExecResult: what a command inside a VM produced: its exit code (None if it timed out), stdout, stderr UwU
@dataclass
class ExecResult:
    exitcode: int | None
    out: str
    err: str
    timed_out: bool = False

    # ok: exit code 0 and no timeout >w<
    @property
    def ok(self) -> bool:
        return self.exitcode == 0 and not self.timed_out

    # tail: the end of stderr + stdout, for error messages OwO
    def tail(self, limit: int = 1500) -> str:
        """The end of stderr + stdout, for error messages."""
        text = (self.err.strip() + "\n" + self.out.strip()).strip()
        return text[-limit:]


# AGENT_DOWN: the text Proxmox answers when the agent didn't reply to its ping. AGENT_RETRIES: how often to retry
# then. PS_START_WAIT: how long to look for a PowerShell script whose start timed out ^w^
AGENT_DOWN = "QEMU guest agent is not running"         # Proxmox's answer when its ping (3 s) gets no reply
AGENT_RETRIES = 3
PS_START_WAIT = 300                                     # s: for a script whose start request timed out


# The comment below explains a nasty quirk: Proxmox treats guest-agent text as Latin-1 (one byte per character).
# to_agent/from_agent convert UTF-8 text to/from that form, so emoji and accents survive the round trip UwU
# Proxmox's guest-agent API handles text one character per byte (Latin-1): command output and file contents come
# back with their UTF-8 bytes as separate characters ('●' arrives as 'â\x97\x8f'), stdin and file contents with a
# character above U+00FF fail (HTTP 500 "Wide character"), and a non-ASCII command argument reaches the agent as
# invalid JSON, which leaves it stuck until its parser is reset. So text goes to the agent as its UTF-8 bytes, comes
# back decoded as UTF-8, and command arguments stay ASCII (anything else goes on stdin or into a file).
# to_agent: the UTF-8 bytes of the text, each as one Latin-1 character >w<
def to_agent(text: str) -> str:
    return text.encode("utf-8").decode("latin-1")


# from_agent: undo that; text that isn't valid UTF-8 (a Windows code page) is returned unchanged OwO
def from_agent(text: str) -> str:
    try:
        return text.encode("latin-1").decode("utf-8")
    except UnicodeError:            # not UTF-8 (e.g. a Windows code page): as it came
        return text


# _api_error: turn a failed request into a ValorError ^w^
def _api_error(e: Exception, what: str) -> ValorError:
    """A failed API call as a ValorError. `what` says what VALOR was doing ("clone template 9000 to 4001")."""
    if isinstance(e, ResourceException):
        # An HTTP error: status and message, the error details, and a hint for common cases UwU
        msg = f"{what}: HTTP {e.status_code} {e.status_message}"
        detail = {"errors": e.errors} if getattr(e, "errors", None) else {"content": str(e.content)[:500]}
        hint = None
        # 403 = the token isn't allowed (it's limited to VALOR's own pool/storage/bridges) >w<
        if e.status_code == 403:
            hint = ("The engine's API token is not allowed to do this. It may only act on VMs in its pool, "
                    "its storage and its bridges; check the spec targets those.")
        elif "command" in what and (e.status_code == 596 or "Permission denied" in str(e.content or e.errors or "")):
            # Proxmox cannot relay agent errors with non-ASCII text (HTTP 596); on RHEL-family guests that error is
            # usually SELinux confining the agent, which templates built by VALOR 0.2+ take care of
            hint = ("The guest agent refused to run the command (SELinux confines it on Rocky/Alma templates built "
                    "by older VALOR versions). Rebuild the template on a Proxmox node: "
                    "./install.sh --template <os> --rebuild")
        # Anything else (network errors) = Proxmox unreachable OwO
        return ValorError("proxmox_api", msg, details=detail, hint=hint)
    return ValorError("proxmox_unreachable", f"{what}: {e.__class__.__name__}: {e}")


# PVE: one client for the configured node ^w^
class PVE:
    def __init__(self, cfg):
        self.cfg = cfg
        self.node = cfg.node
        # Refuse to start without the required settings (with a hint to re-run the installer) UwU
        if cfg.missing():
            raise ValorError("not_configured", "VALOR is not configured: " + ", ".join(cfg.missing()) + " not set",
                             hint="The installer writes /etc/valor/config.toml; re-run it on a Proxmox node.")
        # Read the API token file ({"token_id": "user@realm!name", "secret": "..."}); clear errors when it's missing or
        # unreadable (wrong user) >w<
        try:
            token = json.loads(Path(cfg.token_file).read_text())
        except PermissionError:
            raise ValorError("credentials_unreadable", "the engine cannot read its API token",
                             hint="Run the engine as the 'valor' user: sudo -u valor /opt/valor/bin/valor ...")
        except FileNotFoundError:
            raise ValorError("credentials_missing", f"token file {cfg.token_file} not found")
        # Split "user!name" and create the API client (90 s timeout per request) OwO
        user, name = token["token_id"].split("!", 1)
        self.api = ProxmoxAPI(cfg.api_host, port=cfg.api_port, user=user, token_name=name,
                              token_value=token["secret"], verify_ssl=cfg.verify_ssl, timeout=90)
        # The same token as an Authorization header, for raw requests (uploads, console websockets) ^w^
        # for the few requests made without proxmoxer (uploads, console websockets)
        self._auth = f"PVEAPIToken={token['token_id']}={token['secret']}"

    # ---- Helpers ----

    # call: run ONE API request; ANY failure becomes a ValorError naming what VALOR was doing UwU
    def call(self, what: str, fn, *args, **kwargs):
        """Run one API request; any failure becomes a ValorError that says what VALOR was doing."""
        try:
            return fn(*args, **kwargs)
        except (ResourceException, OSError) as e:
            raise _api_error(e, what)
        except Exception as e:  # requests / proxmoxer transport errors
            raise _api_error(e, what)

    # node_api: /nodes/<node>; vm(): /nodes/<node>/qemu/<vmid> >w<
    @property
    def node_api(self):
        """/nodes/<range node> in the API."""
        return self.api.nodes(self.node)

    def vm(self, vmid: int):
        return self.node_api.qemu(vmid)

    # wait_task: poll a task every 1.5 s until it stops; a failed task raises with the last 15 log lines; too long =
    # a timeout error OwO
    def wait_task(self, upid: str, what: str, timeout: int = 900) -> None:
        """Follow a Proxmox task until it stops; a failed task raises with the end of its log."""
        end = time.time() + timeout
        while time.time() < end:
            status = self.call(f"{what} (task status)", self.node_api.tasks(upid).status.get)
            if status.get("status") == "stopped":
                if status.get("exitstatus") != "OK":
                    log = self.call("task log", self.node_api.tasks(upid).log.get, limit=500)
                    lines = [entry.get("t", "") for entry in log][-15:]
                    raise ValorError("task_failed", f"{what} failed: {status.get('exitstatus')}", details=lines)
                return
            time.sleep(1.5)
        raise ValorError("task_timeout", f"{what} did not finish within {timeout}s")

    # ---- Cluster and node: version, node status (CPU, memory), storage status, network config, every VM ^w^
    # ---- Cluster and node ----
    def version(self) -> dict:
        return self.call("read version", self.api.version.get)

    def node_status(self) -> dict:
        return self.call("read node status", self.node_api.status.get)

    def storage_status(self) -> dict:
        return self.call("read storage status", self.node_api.storage(self.cfg.storage).status.get)

    def network(self) -> list[dict]:
        return self.call("read node network", self.node_api.network.get)

    def resources(self) -> list[dict]:
        return self.call("list VMs", self.api.cluster.resources.get, type="vm")

    # vmid_is_free: Proxmox's nextid endpoint errors when a given id is taken UwU
    def vmid_is_free(self, vmid: int) -> bool:
        try:
            self.api.cluster.nextid.get(vmid=vmid)
            return True
        except ResourceException:
            return False

    # allocate_vmid: the lowest free id in VALOR's block, skipping ids this build already gave out >w<
    def allocate_vmid(self, taken: set[int]) -> int:
        """The lowest free VMID in VALOR's block (taken: VMIDs this build already handed out)."""
        for vmid in range(self.cfg.vmid_min, self.cfg.vmid_max + 1):
            if vmid not in taken and self.vmid_is_free(vmid):
                return vmid
        raise ValorError("vmid_exhausted", f"no free VMID between {self.cfg.vmid_min} and {self.cfg.vmid_max}")

    # ---- VMs ----

    # ---- VMs: read config, read status, create an empty VM (for ISO installs, in VALOR's pool) OwO
    def vm_config(self, vmid: int) -> dict:
        return self.call(f"read config of VM {vmid}", self.vm(vmid).config.get)

    def vm_status(self, vmid: int) -> dict:
        return self.call(f"read status of VM {vmid}", self.vm(vmid).status.current.get)

    def create_vm(self, vmid: int, **params) -> None:
        """A new, empty VM (hosts installed from an ISO); the pool comes from the config like clones."""
        upid = self.call(f"create VM {vmid}", self.node_api.qemu.post, vmid=vmid, pool=self.cfg.pool, **params)
        self.wait_task(upid, f"create VM {vmid}")

    # cpu_type: the virtual CPU model: the catalog's minimum if it has one, else x86-64-v2 with AES only when the
    # node's CPU has AES-NI (a VM asking for AES on a CPU without it won't start!) ^w^
    def cpu_type(self, minimum: str | None = None) -> str:
        """CPU model for a new VM: the catalog minimum, else x86-64-v2 (+AES when the node has AES-NI)."""
        if minimum:
            return minimum
        flags = set(str(self.node_status().get("cpuinfo", {}).get("flags", "")).split())
        return "x86-64-v2-AES" if "aes" in flags else "x86-64-v2"

    # clone: a LINKED clone (full=0: shares the template's disk, so it's fast and small) into VALOR's pool UwU
    def clone(self, template: int, newid: int, name: str, description: str) -> None:
        upid = self.call(f"clone template {template} to {newid}", self.vm(template).clone.post,
                         newid=newid, name=name, pool=self.cfg.pool, full=0, description=description)
        self.wait_task(upid, f"clone {name}")

    # update_config: change settings; SSH keys must be URL-encoded inside the form value >w<
    def update_config(self, vmid: int, **params) -> None:
        if "sshkeys" in params:  # PVE expects the key list URL-encoded inside the form value
            params["sshkeys"] = urllib.parse.quote(params["sshkeys"], safe="")
        self.call(f"configure VM {vmid}", self.vm(vmid).config.put, **params)

    # resize: grow a disk to size_gib OwO
    def resize(self, vmid: int, disk: str, size_gib: int) -> None:
        self.call(f"resize disk of VM {vmid}", self.vm(vmid).resize.put, disk=disk, size=f"{size_gib}G")

    # power: start/stop/shutdown/... a VM and wait for the task (extra params like timeout/forceStop go to the API) ^w^
    def power(self, vmid: int, action: str, wait: int = 300, **params) -> None:
        """params go to the API (e.g. shutdown: timeout=180, forceStop=1); wait bounds the task wait."""
        request = getattr(self.vm(vmid).status, action).post
        upid = self.call(f"{action} VM {vmid}", request, **params)
        self.wait_task(upid, f"{action} VM {vmid}", wait)

    # ---- Snapshots (disk only, never RAM) UwU
    # ---- Snapshots (disk only, no RAM state) ----

    # "current" in the list is the live state, not a snapshot, so it's skipped >w<
    def snapshots(self, vmid: int) -> list[dict]:
        return [snap for snap in self.call(f"list snapshots of VM {vmid}", self.vm(vmid).snapshot.get)
                if snap.get("name") != "current"]       # "current" is the live state, not a snapshot

    # snapshot / rollback / delete_snapshot: each starts a task and waits up to 15 min OwO
    def snapshot(self, vmid: int, name: str, description: str = "") -> None:
        upid = self.call(f"snapshot VM {vmid}", self.vm(vmid).snapshot.post, snapname=name,
                         description=description, vmstate=0)
        self.wait_task(upid, f"snapshot {name} of VM {vmid}", 900)

    def rollback(self, vmid: int, name: str, start: bool = True) -> None:
        upid = self.call(f"roll back VM {vmid}", self.vm(vmid).snapshot(name).rollback.post, start=int(start))
        self.wait_task(upid, f"roll back VM {vmid} to {name}", 900)

    def delete_snapshot(self, vmid: int, name: str) -> None:
        upid = self.call(f"delete snapshot of VM {vmid}", self.vm(vmid).snapshot(name).delete)
        self.wait_task(upid, f"delete snapshot {name} of VM {vmid}", 900)

    # destroy: stop the VM if it runs, then delete it with its disks (purge also removes it from backup jobs and
    # HA) ^w^ *gulps*
    def destroy(self, vmid: int) -> None:
        if self.vm_status(vmid).get("status") == "running":
            self.power(vmid, "stop")
        upid = self.call(f"delete VM {vmid}", self.vm(vmid).delete, purge=1,
                         **{"destroy-unreferenced-disks": 1})
        self.wait_task(upid, f"delete VM {vmid}")

    # ---- Storage content (the ISO library) UwU
    # ---- Storage content (ISO library) ----

    # List a storage's ISOs; download a URL straight onto a storage (Proxmox checks the TLS certificate and, when
    # given, the checksum); delete a volume >w<
    def storage_content(self, storage: str, content: str) -> list[dict]:
        return self.call(f"list {content} on {storage}", self.node_api.storage(storage).content.get, content=content)

    def download_url(self, storage: str, url: str, filename: str, checksum: str | None = None,
                     algorithm: str | None = None) -> str:
        params = {"content": "iso", "url": url, "filename": filename, "verify-certificates": 1}
        if checksum:
            params.update({"checksum": checksum, "checksum-algorithm": algorithm})
        return self.call(f"download {filename} to {storage}", self.node_api.storage(storage)("download-url").post, **params)

    def delete_volume(self, storage: str, volid: str) -> None:
        upid = self.call(f"delete {volid}", self.node_api.storage(storage).content(volid).delete)
        if upid:
            self.wait_task(upid, f"delete {volid}")

    # task: a task's status with its log lines (for the ISO progress list) OwO
    def task(self, upid: str) -> dict:
        status = self.call("read task status", self.node_api.tasks(upid).status.get)
        log = self.call("read task log", self.node_api.tasks(upid).log.get, limit=500)
        status["log"] = [entry.get("t", "") for entry in log]
        return status

    # upload_iso: stream a local file to Proxmox (multipart, never all in memory), with an optional checksum, and
    # return the import task's id ^w^
    def upload_iso(self, storage: str, path: str, filename: str, checksum: str | None = None,
                   algorithm: str | None = None) -> str:
        """Streams a local file to the storage (multi-GB safe); returns the UPID of Proxmox's import task."""
        import requests
        from .isos import MultipartFile
        fields = {"content": "iso"}
        if checksum:      # Proxmox's multipart parser expects exactly this order: content, checksum-algorithm, checksum
            fields.update({"checksum-algorithm": algorithm, "checksum": checksum})
        body = MultipartFile(fields, "filename", filename, path)
        url = f"https://{self.cfg.api_host}:{self.cfg.api_port}/api2/json/nodes/{self.node}/storage/{storage}/upload"
        try:
            response = requests.post(url, data=body, timeout=(15, 3600), verify=self.cfg.verify_ssl,
                                     headers={"Authorization": self._auth, "Content-Type": body.content_type,
                                              "Content-Length": str(len(body))})
        finally:
            body.close()
        if response.status_code != 200:
            raise ValorError("upload_failed",
                             f"upload of {filename} failed: HTTP {response.status_code} {response.text[:300]}")
        return response.json()["data"]

    # ---- Consoles (the web service relays them) UwU
    # ---- Consoles (relayed by the web service) ----

    # vncproxy: start a VNC console session; termproxy: start a serial console session >w<
    def vncproxy(self, vmid: int) -> dict:
        """A VNC console session: {port, ticket, user, ...}. The ticket doubles as the VNC password."""
        return self.call(f"open VNC console of VM {vmid}", self.vm(vmid).vncproxy.post, websocket=1)

    def termproxy(self, vmid: int, serial: str = "serial0") -> dict:
        return self.call(f"open serial console of VM {vmid}", self.vm(vmid).termproxy.post, serial=serial)

    # console_websocket: where and how to connect to a console session: the wss:// URL, the token header, and a TLS
    # context that trusts the cluster's CA (or the system CAs, or nothing when verification is off) OwO
    def console_websocket(self, vmid: int, port: int | str, ticket: str) -> tuple[str, dict, object]:
        """(URL, headers, SSL context) for connecting to a console session's websocket with the API token."""
        import ssl
        url = (f"wss://{self.cfg.api_host}:{self.cfg.api_port}/api2/json/nodes/{self.node}/qemu/{vmid}/vncwebsocket"
               f"?port={port}&vncticket={urllib.parse.quote(ticket, safe='')}")
        if isinstance(self.cfg.verify_ssl, str):            # path to the cluster's CA certificate
            context = ssl.create_default_context(cafile=self.cfg.verify_ssl)
        elif self.cfg.verify_ssl:
            context = ssl.create_default_context()
        else:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return url, {"Authorization": self._auth}, context

    # ---- Guest agent ^w^
    # ---- Guest agent ----

    # agent_ping: does the agent answer? UwU
    def agent_ping(self, vmid: int) -> bool:
        try:
            self.vm(vmid).agent.ping.post()
            return True
        except Exception:
            return False

    # wait_agent: ping every 3 s until it answers or the timeout passes >w<
    def wait_agent(self, vmid: int, timeout: int = 300) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.agent_ping(vmid):
                return
            time.sleep(3)
        raise ValorError("agent_timeout", f"guest agent of VM {vmid} did not respond within {timeout}s",
                         hint="The VM may not have booted; check its console in the Proxmox UI.")

    # agent_call: an agent request with retries: when the agent briefly didn't answer Proxmox's ping (or, for
    # requests safe to send twice, timed out), wait for the agent and try again OwO
    def agent_call(self, vmid: int, what: str, fn, resend: bool = False, **params):
        """A guest-agent request. Proxmox pings the agent (3 s) before each command and sends nothing when the ping
        fails, so a guest that is briefly too busy to answer (Windows on one core right after setup) is retried once
        its agent answers again. Requests that are safe to send twice (resend) are also retried after a timeout."""
        for attempt in range(AGENT_RETRIES + 1):
            try:
                return self.call(what, fn, **params)
            except ValorError as e:
                text = f"{e.message} {e.details}"
                if attempt == AGENT_RETRIES or not (AGENT_DOWN in text or (resend and "got timeout" in text)):
                    raise
            self.wait_agent(vmid, 300)

    # exec: start a program in the guest (ASCII arguments only; other text goes on stdin) and wait for it ^w^
    def exec(self, vmid: int, command: list[str], input_data: str | None = None, timeout: int = 900) -> ExecResult:
        """Run a program inside the guest and wait for it (see to_agent for why arguments must be ASCII)."""
        if not all(arg.isascii() for arg in command):
            raise ValorError("agent_non_ascii", f"a command for VM {vmid} has non-ASCII arguments",
                             hint="Proxmox would leave the guest agent stuck: pass such text on stdin or in a file.")
        params: dict = {"command": command}
        if input_data is not None:
            params["input-data"] = to_agent(input_data)
        started = self.agent_call(vmid, f"run command in VM {vmid}", self.vm(vmid).agent.exec.post, **params)
        return self.exec_wait(vmid, started["pid"], timeout)

    # exec_wait: poll the process's status (0.5 s at first, growing to 3 s) until it exits; collect its output UwU
    def exec_wait(self, vmid: int, pid: int, timeout: int = 900) -> ExecResult:
        """Wait for a process the agent started and collect its output (the agent keeps it until it is read)."""
        end = time.time() + timeout
        delay = 0.5
        while time.time() < end:
            try:
                status = self.agent_call(vmid, f"read command status in VM {vmid}",
                                         self.vm(vmid).agent("exec-status").get, resend=True, pid=pid)
            except ValorError as e:
                # "PID does not exist" means the agent restarted under us (e.g. a package upgrade) >w<
                if not re.search(r"PID \S+ does not exist", f"{e.message} {e.details}"):
                    raise
                # the agent restarted (a package upgrade restarts qemu-guest-agent) and forgot - or killed - the process
                raise ValorError("agent_exec_lost", f"the guest agent of VM {vmid} restarted while a command ran",
                                 details=e.details, hint="Apply the range again to run the step again.") from e
            if status.get("exited"):
                return ExecResult(status.get("exitcode"), from_agent(status.get("out-data", "") or ""),
                                  from_agent(status.get("err-data", "") or ""))
            time.sleep(delay)
            delay = min(delay * 1.5, 3)                # quick commands return fast; long ones aren't polled hard
        return ExecResult(None, "", f"timed out after {timeout}s", timed_out=True)

    # script: run a bash script as root, passed on stdin (so it never appears on a command line) OwO
    def script(self, vmid: int, body: str, timeout: int = 900) -> ExecResult:
        """Run a bash script (passed on stdin) as root inside the guest."""
        return self.exec(vmid, ["/bin/bash", "-s"], input_data=body, timeout=timeout)

    # write_file: write a file in the guest (max 60 KiB); safe to resend ^w^
    def write_file(self, vmid: int, path: str, content: str) -> None:
        data = to_agent(content)
        if len(data) > 60_000:
            raise ValorError("script_too_large", f"file for VM {vmid} exceeds the guest agent's 60 KiB limit")
        self.agent_call(vmid, f"write file in VM {vmid}", self.vm(vmid).agent("file-write").post, resend=True,
                        file=path, content=data)                # opened "wb": writing it again is harmless

    # file_read: read a small file (None when it can't be read) UwU
    def file_read(self, vmid: int, path: str) -> str | None:
        """A small file's content through the agent; None when it cannot be read (e.g. it does not exist yet)."""
        try:
            return from_agent(self.agent_call(vmid, f"read file in VM {vmid}", self.vm(vmid).agent("file-read").get,
                                              resend=True, file=path).get("content", ""))
        except ValorError:
            return None

    # ps: run a PowerShell script as SYSTEM >w<
    def ps(self, vmid: int, body: str, timeout: int = 900) -> ExecResult:
        """Run a PowerShell script as SYSTEM inside a Windows guest. The script goes to a temporary file over the
        agent (it may carry secrets: never on a command line), runs with -File and is deleted afterwards.

        Proxmox gives the agent 5 s to accept a command; a busy Windows guest (logon right after a reboot) can miss
        that although it runs the command. Starting the script again could run it twice, so its first line records
        its process ID, and after such a timeout VALOR follows that process instead."""
        # A random temp script path and a pid file next to it OwO
        import secrets
        path = f"C:\\Windows\\Temp\\valor-{secrets.token_hex(8)}.ps1"
        pidfile = path + ".pid"
        # The script starts by writing its own process id to the pid file, sets UTF-8 output and stop-on-error, then the
        # body (with Windows line endings) ^w^
        # the BOM makes Windows PowerShell read the script as UTF-8 (not ANSI); its output is UTF-8 as well
        self.write_file(vmid, path, f"\ufeffSet-Content -LiteralPath '{pidfile}' -Value $PID -Encoding ascii\r\n"
                        "try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}\r\n"
                        "$ErrorActionPreference = 'Stop'\r\n" + body.replace("\r\n", "\n").replace("\n", "\r\n"))
        # Run it with -File. If STARTING it timed out, it may still have started: look for the pid file for up to 5 min
        # and follow that process instead of starting it twice UwU
        try:
            try:
                return self.exec(vmid, ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                        "-File", path], timeout=timeout)
            except ValorError as e:
                if "got timeout" not in f"{e.message} {e.details}":
                    raise
            end = time.time() + PS_START_WAIT
            while time.time() < end:
                pid = (self.file_read(vmid, pidfile) or "").strip()
                if pid.isdigit():
                    return self.exec_wait(vmid, int(pid), timeout)
                time.sleep(5)
            raise ValorError("agent_exec_lost", f"a command in VM {vmid} timed out and never started",
                             hint="The guest was too busy to answer its agent. Apply the range again to retry.")
        # Always delete the script and pid file afterwards (ignoring errors) >w< *tidies the guest*
        finally:
            try:
                self.exec(vmid, ["cmd.exe", "/c", "del", "/f", "/q", path, pidfile], timeout=60)
            except ValorError:
                pass
