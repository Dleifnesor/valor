"""Proxmox VE API client. The engine is the only component that calls the Proxmox API."""

from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from proxmoxer import ProxmoxAPI
from proxmoxer.core import ResourceException

from .errors import ValorError


@dataclass
class ExecResult:
    exitcode: int | None
    out: str
    err: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exitcode == 0 and not self.timed_out

    def tail(self, n: int = 1500) -> str:
        text = (self.err.strip() + "\n" + self.out.strip()).strip()
        return text[-n:]


def _api_error(e: Exception, what: str) -> ValorError:
    if isinstance(e, ResourceException):
        msg = f"{what}: HTTP {e.status_code} {e.status_message}"
        detail = {"errors": e.errors} if getattr(e, "errors", None) else {"content": str(e.content)[:500]}
        hint = None
        if e.status_code == 403:
            hint = ("The engine's API token is not allowed to do this. It may only act on VMs in its pool, "
                    "its storage and its bridges; check the spec targets those.")
        elif "command" in what and (e.status_code == 596 or "Permission denied" in str(e.content or e.errors or "")):
            # Proxmox cannot relay agent errors with non-ASCII text (HTTP 596); on RHEL-family guests that error is
            # usually SELinux confining the agent, which templates built by VALOR 0.2+ take care of
            hint = ("The guest agent refused to run the command (SELinux confines it on Rocky/Alma templates built "
                    "by older VALOR versions). Rebuild the template on a Proxmox node: "
                    "./install.sh --template <os> --rebuild")
        return ValorError("proxmox_api", msg, details=detail, hint=hint)
    return ValorError("proxmox_unreachable", f"{what}: {e.__class__.__name__}: {e}")


class PVE:
    def __init__(self, cfg):
        self.cfg = cfg
        self.node = cfg.node
        if cfg.missing():
            raise ValorError("not_configured", "VALOR is not configured: " + ", ".join(cfg.missing()) + " not set",
                             hint="The installer writes /etc/valor/config.toml; re-run it on a Proxmox node.")
        try:
            tok = json.loads(Path(cfg.token_file).read_text())
        except PermissionError:
            raise ValorError("credentials_unreadable", "the engine cannot read its API token",
                             hint="Run the engine as the 'valor' user: sudo -u valor /opt/valor/bin/valor ...")
        except FileNotFoundError:
            raise ValorError("credentials_missing", f"token file {cfg.token_file} not found")
        user, name = tok["token_id"].split("!", 1)
        self.api = ProxmoxAPI(cfg.api_host, port=cfg.api_port, user=user, token_name=name,
                              token_value=tok["secret"], verify_ssl=cfg.verify_ssl, timeout=90)
        self._auth = f"PVEAPIToken={tok['token_id']}={tok['secret']}"

    # ------------------------------------------------------------------ helpers
    def call(self, what: str, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except (ResourceException, OSError) as e:
            raise _api_error(e, what)
        except Exception as e:  # requests / proxmoxer transport errors
            raise _api_error(e, what)

    @property
    def n(self):
        return self.api.nodes(self.node)

    def vm(self, vmid: int):
        return self.n.qemu(vmid)

    def wait_task(self, upid: str, what: str, timeout: int = 900) -> None:
        end = time.time() + timeout
        while time.time() < end:
            st = self.call(f"{what} (task status)", self.n.tasks(upid).status.get)
            if st.get("status") == "stopped":
                if st.get("exitstatus") != "OK":
                    log = self.call("task log", self.n.tasks(upid).log.get, limit=500)
                    lines = [l.get("t", "") for l in log][-15:]
                    raise ValorError("task_failed", f"{what} failed: {st.get('exitstatus')}", details=lines)
                return
            time.sleep(1.5)
        raise ValorError("task_timeout", f"{what} did not finish within {timeout}s")

    # ------------------------------------------------------------------ cluster / node
    def version(self) -> dict:
        return self.call("read version", self.api.version.get)

    def node_status(self) -> dict:
        return self.call("read node status", self.n.status.get)

    def storage_status(self) -> dict:
        return self.call("read storage status", self.n.storage(self.cfg.storage).status.get)

    def network(self) -> list[dict]:
        return self.call("read node network", self.n.network.get)

    def resources(self) -> list[dict]:
        return self.call("list VMs", self.api.cluster.resources.get, type="vm")

    def vmid_is_free(self, vmid: int) -> bool:
        try:
            self.api.cluster.nextid.get(vmid=vmid)
            return True
        except ResourceException:
            return False

    def allocate_vmid(self, taken: set[int]) -> int:
        for v in range(self.cfg.vmid_min, self.cfg.vmid_max + 1):
            if v not in taken and self.vmid_is_free(v):
                return v
        raise ValorError("vmid_exhausted", f"no free VMID between {self.cfg.vmid_min} and {self.cfg.vmid_max}")

    # ------------------------------------------------------------------ VMs
    def vm_config(self, vmid: int) -> dict:
        return self.call(f"read config of VM {vmid}", self.vm(vmid).config.get)

    def vm_status(self, vmid: int) -> dict:
        return self.call(f"read status of VM {vmid}", self.vm(vmid).status.current.get)

    def create_vm(self, vmid: int, **params) -> None:
        """A new, empty VM (hosts installed from an ISO); the pool comes from the config like clones."""
        upid = self.call(f"create VM {vmid}", self.n.qemu.post, vmid=vmid, pool=self.cfg.pool, **params)
        self.wait_task(upid, f"create VM {vmid}")

    def cpu_type(self, minimum: str | None = None) -> str:
        """CPU model for a new VM: the catalog minimum, else x86-64-v2 (+AES when the node has AES-NI)."""
        if minimum:
            return minimum
        flags = set(str(self.node_status().get("cpuinfo", {}).get("flags", "")).split())
        return "x86-64-v2-AES" if "aes" in flags else "x86-64-v2"

    def clone(self, template: int, newid: int, name: str, description: str) -> None:
        upid = self.call(f"clone template {template} to {newid}", self.vm(template).clone.post,
                         newid=newid, name=name, pool=self.cfg.pool, full=0, description=description)
        self.wait_task(upid, f"clone {name}")

    def update_config(self, vmid: int, **params) -> None:
        if "sshkeys" in params:  # PVE expects the key list URL-encoded inside the form value
            params["sshkeys"] = urllib.parse.quote(params["sshkeys"], safe="")
        self.call(f"configure VM {vmid}", self.vm(vmid).config.put, **params)

    def resize(self, vmid: int, disk: str, size_gib: int) -> None:
        self.call(f"resize disk of VM {vmid}", self.vm(vmid).resize.put, disk=disk, size=f"{size_gib}G")

    def power(self, vmid: int, action: str, wait: int = 300, **params) -> None:
        """params go to the API (e.g. shutdown: timeout=180, forceStop=1); wait bounds the task wait."""
        fn = getattr(self.vm(vmid).status, action).post
        upid = self.call(f"{action} VM {vmid}", fn, **params)
        self.wait_task(upid, f"{action} VM {vmid}", wait)

    # ------------------------------------------------------------------ snapshots (disk only, no RAM state)
    def snapshots(self, vmid: int) -> list[dict]:
        return [s for s in self.call(f"list snapshots of VM {vmid}", self.vm(vmid).snapshot.get)
                if s.get("name") != "current"]

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

    def destroy(self, vmid: int) -> None:
        st = self.vm_status(vmid).get("status")
        if st == "running":
            self.power(vmid, "stop")
        upid = self.call(f"delete VM {vmid}", self.vm(vmid).delete, purge=1,
                         **{"destroy-unreferenced-disks": 1})
        self.wait_task(upid, f"delete VM {vmid}")

    # ------------------------------------------------------------------ storage content (ISO library)
    def storage_content(self, storage: str, content: str) -> list[dict]:
        return self.call(f"list {content} on {storage}", self.n.storage(storage).content.get, content=content)

    def download_url(self, storage: str, url: str, filename: str, checksum: str | None = None,
                     algorithm: str | None = None) -> str:
        params = {"content": "iso", "url": url, "filename": filename, "verify-certificates": 1}
        if checksum:
            params.update({"checksum": checksum, "checksum-algorithm": algorithm})
        return self.call(f"download {filename} to {storage}", self.n.storage(storage)("download-url").post, **params)

    def delete_volume(self, storage: str, volid: str) -> None:
        upid = self.call(f"delete {volid}", self.n.storage(storage).content(volid).delete)
        if upid:
            self.wait_task(upid, f"delete {volid}")

    def task(self, upid: str) -> dict:
        st = self.call("read task status", self.n.tasks(upid).status.get)
        log = self.call("read task log", self.n.tasks(upid).log.get, limit=500)
        st["log"] = [l.get("t", "") for l in log]
        return st

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
            r = requests.post(url, data=body, timeout=(15, 3600), verify=self.cfg.verify_ssl,
                              headers={"Authorization": self._auth, "Content-Type": body.content_type,
                                       "Content-Length": str(len(body))})
        finally:
            body.close()
        if r.status_code != 200:
            raise ValorError("upload_failed", f"upload of {filename} failed: HTTP {r.status_code} {r.text[:300]}")
        return r.json()["data"]

    # ------------------------------------------------------------------ consoles (relayed by the web service)
    def vncproxy(self, vmid: int) -> dict:
        """A VNC console session: {port, ticket, user, ...}. The ticket doubles as the VNC password."""
        return self.call(f"open VNC console of VM {vmid}", self.vm(vmid).vncproxy.post, websocket=1)

    def termproxy(self, vmid: int, serial: str = "serial0") -> dict:
        return self.call(f"open serial console of VM {vmid}", self.vm(vmid).termproxy.post, serial=serial)

    def console_websocket(self, vmid: int, port: int | str, ticket: str) -> tuple[str, dict, object]:
        """(URL, headers, SSL context) for connecting to a console session's websocket with the API token."""
        import ssl
        url = (f"wss://{self.cfg.api_host}:{self.cfg.api_port}/api2/json/nodes/{self.node}/qemu/{vmid}/vncwebsocket"
               f"?port={port}&vncticket={urllib.parse.quote(ticket, safe='')}")
        if isinstance(self.cfg.verify_ssl, str):
            ctx = ssl.create_default_context(cafile=self.cfg.verify_ssl)
        elif self.cfg.verify_ssl:
            ctx = ssl.create_default_context()
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return url, {"Authorization": self._auth}, ctx

    # ------------------------------------------------------------------ guest agent
    def agent_ping(self, vmid: int) -> bool:
        try:
            self.vm(vmid).agent.ping.post()
            return True
        except Exception:
            return False

    def wait_agent(self, vmid: int, timeout: int = 300) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.agent_ping(vmid):
                return
            time.sleep(3)
        raise ValorError("agent_timeout", f"guest agent of VM {vmid} did not respond within {timeout}s",
                         hint="The VM may not have booted; check its console in the Proxmox UI.")

    def exec(self, vmid: int, command: list[str], input_data: str | None = None, timeout: int = 900) -> ExecResult:
        params: dict = {"command": command}
        if input_data is not None:
            params["input-data"] = input_data
        res = self.call(f"run command in VM {vmid}", self.vm(vmid).agent.exec.post, **params)
        pid = res["pid"]
        end = time.time() + timeout
        delay = 0.5
        while time.time() < end:
            st = self.call(f"read command status in VM {vmid}", self.vm(vmid).agent("exec-status").get, pid=pid)
            if st.get("exited"):
                return ExecResult(st.get("exitcode"), st.get("out-data", "") or "", st.get("err-data", "") or "")
            time.sleep(delay)
            delay = min(delay * 1.5, 3)
        return ExecResult(None, "", f"timed out after {timeout}s", timed_out=True)

    def script(self, vmid: int, body: str, timeout: int = 900) -> ExecResult:
        """Run a bash script (passed on stdin) as root inside the guest."""
        return self.exec(vmid, ["/bin/bash", "-s"], input_data=body, timeout=timeout)

    def write_file(self, vmid: int, path: str, content: str) -> None:
        if len(content) > 60_000:
            raise ValorError("script_too_large", f"file for VM {vmid} exceeds the guest agent's 60 KiB limit")
        self.call(f"write file in VM {vmid}", self.vm(vmid).agent("file-write").post, file=path, content=content)

    def ps(self, vmid: int, body: str, timeout: int = 900) -> ExecResult:
        """Run a PowerShell script as SYSTEM inside a Windows guest. The script goes to a temporary file over the
        agent (it may carry secrets: never on a command line), runs with -File and is deleted afterwards."""
        import secrets as _s
        path = f"C:\\Windows\\Temp\\valor-{_s.token_hex(8)}.ps1"
        self.write_file(vmid, path, "$ErrorActionPreference = 'Stop'\r\n" + body.replace("\r\n", "\n").replace("\n", "\r\n"))
        try:
            return self.exec(vmid, ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                    "-File", path], timeout=timeout)
        finally:
            try:
                self.exec(vmid, ["cmd.exe", "/c", "del", "/f", "/q", path], timeout=60)
            except ValorError:
                pass
