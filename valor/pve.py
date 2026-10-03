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
        return ValorError("proxmox_api", msg, details=detail, hint=hint)
    return ValorError("proxmox_unreachable", f"{what}: {e.__class__.__name__}: {e}")


class PVE:
    def __init__(self, cfg):
        self.cfg = cfg
        self.node = cfg.node
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

    def power(self, vmid: int, action: str, timeout: int = 300) -> None:
        fn = getattr(self.vm(vmid).status, action).post
        upid = self.call(f"{action} VM {vmid}", fn)
        self.wait_task(upid, f"{action} VM {vmid}", timeout)

    def destroy(self, vmid: int) -> None:
        st = self.vm_status(vmid).get("status")
        if st == "running":
            self.power(vmid, "stop")
        upid = self.call(f"delete VM {vmid}", self.vm(vmid).delete, purge=1,
                         **{"destroy-unreferenced-disks": 1})
        self.wait_task(upid, f"delete VM {vmid}")

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
