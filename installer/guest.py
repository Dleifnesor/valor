# *whispers through the guest agent* OwO installer/guest.py talks to a VM through the QEMU GUEST AGENT! ^w^
# Run scripts, copy files in, read files out, all via pvesh, over the agent's virtio channel. So the installer never
# needs SSH or even network access to the VALOR VM *wags tail*
"""Talking to a VM through the QEMU guest agent (pvesh): run scripts, copy files in and out.

The installer never needs SSH or network access to the VALOR VM: everything goes over the agent's virtio
channel, so provisioning works even when the node cannot reach the VM's network.
"""

# Lazy type hints UwU
from __future__ import annotations

# base64 (file chunks), hashlib (checking copies), time (waiting) >w<
import base64
import hashlib
import time

# CommandError and pvesh from sh.py OwO
from .sh import CommandError, pvesh

# CHUNK: bytes per file-write call (base64 makes 45,000 bytes into 60,000 characters, the API's limit) ^w^
CHUNK = 45_000          # bytes per file-write call (base64 grows it to 60,000 characters: the API limit is 60 KiB)


# ping(): does the agent answer? UwU
def ping(node: str, vmid: int) -> bool:
    try:
        pvesh("create", f"/nodes/{node}/qemu/{vmid}/agent/ping")
        return True
    except CommandError:
        return False


# wait_agent(): ping every 3 s until it answers or the timeout passes >w<
def wait_agent(node: str, vmid: int, timeout: float = 300) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if ping(node, vmid):
            return
        time.sleep(3)
    raise TimeoutError(f"the QEMU guest agent in VM {vmid} did not answer within {timeout:.0f}s")


# start_script(): start bash with the script on stdin (max 60 KB); returns the guest's process id OwO
def start_script(node: str, vmid: int, script: str) -> int:
    """Start `bash` with the script on stdin; returns the guest PID."""
    if len(script.encode()) > 60_000:
        raise ValueError("script too long for one agent call")
    res = pvesh("create", f"/nodes/{node}/qemu/{vmid}/agent/exec", command="/bin/bash", **{"input-data": script})
    return int(res["pid"])


# status(): a started process's status (exited? exit code, output) ^w^
def status(node: str, vmid: int, pid: int) -> dict:
    return pvesh("get", f"/nodes/{node}/qemu/{vmid}/agent/exec-status", pid=pid)


# script(): run a script and wait for it; returns (exit code, stdout, stderr) UwU
def script(node: str, vmid: int, body: str, timeout: float = 120, poll: float = 1.5) -> tuple[int, str, str]:
    pid = start_script(node, vmid, body)
    end = time.time() + timeout
    while time.time() < end:
        state = status(node, vmid, pid)
        if state.get("exited"):
            return int(state.get("exitcode", -1)), state.get("out-data", ""), state.get("err-data", "")
        time.sleep(poll)
    raise TimeoutError(f"script in VM {vmid} still running after {timeout:.0f}s")


# check(): like script(), but a non-zero exit raises; returns stdout >w<
def check(node: str, vmid: int, body: str, timeout: float = 120) -> str:
    code, out, err = script(node, vmid, body, timeout)
    if code != 0:
        raise RuntimeError(f"command in VM {vmid} failed ({code}): {(err or out).strip()[-1500:]}")
    return out


# write_file(): copy bytes into the guest in chunks, then join them, CHECK the SHA-256 on the other side, set the
# permissions, and move the file into place (umask 077: nothing is readable by others while copying) OwO
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


# read_file(): read a file through the agent (refusing a truncated read) ^w^
def read_file(node: str, vmid: int, path: str) -> str:
    res = pvesh("get", f"/nodes/{node}/qemu/{vmid}/agent/file-read", file=path)
    if res.get("truncated"):
        raise RuntimeError(f"{path} is too large to read through the guest agent")
    return res.get("content", "")


# ipv4(): the guest's first non-loopback IPv4 address (None when the agent can't tell yet) UwU
def ipv4(node: str, vmid: int) -> str | None:
    """First non-loopback IPv4 address the guest reports."""
    try:
        res = pvesh("get", f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
    except CommandError:
        return None
    for iface in res.get("result", []):
        if iface.get("name") == "lo":
            continue
        for address in iface.get("ip-addresses", []):
            if address.get("ip-address-type") == "ipv4" and not address["ip-address"].startswith("127."):
                return address["ip-address"]
    return None
