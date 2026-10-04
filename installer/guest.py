"""Talking to a VM through the QEMU guest agent (pvesh): run scripts, copy files in and out.

The installer never needs SSH or network access to the VALOR VM: everything goes over the agent's virtio
channel, so provisioning works even when the node cannot reach the VM's network.
"""

from __future__ import annotations

import base64
import hashlib
import time

from .sh import CommandError, pvesh

CHUNK = 45_000          # bytes per file-write call (base64 grows it to 60,000 characters: the API limit is 60 KiB)


def ping(node: str, vmid: int) -> bool:
    try:
        pvesh("create", f"/nodes/{node}/qemu/{vmid}/agent/ping")
        return True
    except CommandError:
        return False


def wait_agent(node: str, vmid: int, timeout: float = 300) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if ping(node, vmid):
            return
        time.sleep(3)
    raise TimeoutError(f"the QEMU guest agent in VM {vmid} did not answer within {timeout:.0f}s")


def start_script(node: str, vmid: int, script: str) -> int:
    """Start `bash` with the script on stdin; returns the guest PID."""
    if len(script.encode()) > 60_000:
        raise ValueError("script too long for one agent call")
    res = pvesh("create", f"/nodes/{node}/qemu/{vmid}/agent/exec", command="/bin/bash", **{"input-data": script})
    return int(res["pid"])


def status(node: str, vmid: int, pid: int) -> dict:
    return pvesh("get", f"/nodes/{node}/qemu/{vmid}/agent/exec-status", pid=pid)


def script(node: str, vmid: int, body: str, timeout: float = 120, poll: float = 1.5) -> tuple[int, str, str]:
    pid = start_script(node, vmid, body)
    end = time.time() + timeout
    while time.time() < end:
        st = status(node, vmid, pid)
        if st.get("exited"):
            return int(st.get("exitcode", -1)), st.get("out-data", ""), st.get("err-data", "")
        time.sleep(poll)
    raise TimeoutError(f"script in VM {vmid} still running after {timeout:.0f}s")


def check(node: str, vmid: int, body: str, timeout: float = 120) -> str:
    code, out, err = script(node, vmid, body, timeout)
    if code != 0:
        raise RuntimeError(f"command in VM {vmid} failed ({code}): {(err or out).strip()[-1500:]}")
    return out


def write_file(node: str, vmid: int, path: str, data: bytes | str, mode: str = "0600") -> None:
    """Copy bytes into the guest (in chunks), then check the SHA-256 on the other side."""
    if isinstance(data, str):
        data = data.encode()
    parts = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
    tmp = f"{path}.part"
    check(node, vmid, f"set -e; umask 077; mkdir -p \"$(dirname '{path}')\"; rm -f '{tmp}'.*")
    for i, part in enumerate(parts):
        pvesh("create", f"/nodes/{node}/qemu/{vmid}/agent/file-write", _quiet_secret=True,
              file=f"{tmp}.{i:05d}", content=base64.b64encode(part).decode(), encode=0)
    digest = hashlib.sha256(data).hexdigest()
    check(node, vmid, f"""set -e; umask 077
cat '{tmp}'.* > '{tmp}'; rm -f '{tmp}'.0*
echo '{digest}  {tmp}' | sha256sum -c --quiet -
chmod {mode} '{tmp}'; mv -f '{tmp}' '{path}'""")


def read_file(node: str, vmid: int, path: str) -> str:
    res = pvesh("get", f"/nodes/{node}/qemu/{vmid}/agent/file-read", file=path)
    if res.get("truncated"):
        raise RuntimeError(f"{path} is too large to read through the guest agent")
    return res.get("content", "")


def ipv4(node: str, vmid: int) -> str | None:
    """First non-loopback IPv4 address the guest reports."""
    try:
        res = pvesh("get", f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
    except CommandError:
        return None
    for iface in res.get("result", []):
        if iface.get("name") == "lo":
            continue
        for a in iface.get("ip-addresses", []):
            if a.get("ip-address-type") == "ipv4" and not a["ip-address"].startswith("127."):
                return a["ip-address"]
    return None


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
