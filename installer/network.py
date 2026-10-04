"""The isolated range network: a VLAN-aware bridge without physical ports, added with a safety net.

The change is additive (one new bridge), made through the Proxmox API, checked against a backup before it is
applied, and protected by a rollback timer that restores the previous configuration unless the node still
reaches its gateway afterwards.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import ui
from .sh import CommandError, pvesh, run

ROLLBACK_SECONDS = 300


def _norm(text: str) -> list[str]:
    """Meaningful lines, compared without indentation, spacing or case (Proxmox rewrites hand-edited files with tabs
    and drops free comments; neither is a change to the network)."""
    out = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line and not line.startswith("#"):
            out.append(line.lower())
    return out


def _new_lines(before: str, after: str) -> tuple[list[str], list[str]]:
    b, a = _norm(before), _norm(after)
    removed = [l for l in b if l not in a]
    added = [l for l in a if l not in b]
    return removed, added


def _gateway_ok() -> bool:
    route = run(["ip", "-4", "route", "show", "default"], check=False).stdout.split()
    if "via" not in route:
        return False
    gw = route[route.index("via") + 1]
    if run(["ping", "-c", "2", "-W", "2", gw], check=False).returncode == 0:
        return True
    # Some gateways ignore ping: fall back to reaching the internet.
    return run(["curl", "-fsSI", "--max-time", "10", "-o", "/dev/null", "https://cloud-images.ubuntu.com/"],
               check=False).returncode == 0


def create_bridge(node: str, name: str, comment: str) -> str:
    """Returns the backup directory. Raises SystemExit (with the rollback armed) if verification fails."""
    if Path("/etc/network/interfaces.new").exists():
        raise SystemExit("this node has pending network changes (/etc/network/interfaces.new); apply or revert them "
                         "in the Proxmox UI first")
    if Path(f"/sys/class/net/{name}").exists():
        raise SystemExit(f"{name} already exists")
    backup = Path(f"/root/valor-net-backup-{time.strftime('%Y%m%d-%H%M%S')}")
    backup.mkdir(parents=True)
    shutil.copy2("/etc/network/interfaces", backup / "interfaces")
    if Path("/etc/network/interfaces.d").is_dir():
        shutil.copytree("/etc/network/interfaces.d", backup / "interfaces.d")
    before = Path("/etc/network/interfaces").read_text()
    pvesh("create", f"/nodes/{node}/network", iface=name, type="bridge", bridge_vlan_aware=1, autostart=1,
          comments=comment)
    after = Path("/etc/network/interfaces.new").read_text()
    removed, added = _new_lines(before, after)
    if removed or not any(l.startswith(f"iface {name}") or l.startswith(f"auto {name}") for l in added):
        Path("/etc/network/interfaces.new").unlink(missing_ok=True)
        raise SystemExit("the pending network change would alter existing configuration - aborted, nothing applied:\n"
                         + "\n".join(f"  - {l}" for l in removed))
    unit = f"valor-net-rollback-{name}"
    run(["systemd-run", "--quiet", f"--on-active={ROLLBACK_SECONDS}", f"--unit={unit}", "/bin/sh", "-c",
         f"cp {backup}/interfaces /etc/network/interfaces && rm -f /etc/network/interfaces.new && ifreload -a"])
    ui.info(f"rollback armed: the old network configuration comes back in {ROLLBACK_SECONDS // 60} minutes "
            f"unless the check below passes (backup in {backup})")
    pvesh("set", f"/nodes/{node}/network")
    time.sleep(3)
    if not (Path(f"/sys/class/net/{name}").exists() and _gateway_ok()):
        raise SystemExit(f"after adding {name} the node could not reach its gateway; the rollback timer restores the "
                         f"previous configuration within {ROLLBACK_SECONDS // 60} minutes")
    run(["systemctl", "stop", f"{unit}.timer"], check=False)
    ui.ok(f"bridge {name} created (VLAN-aware, no physical port); rollback cancelled")
    return str(backup)


def remove_bridge(node: str, name: str) -> None:
    """Delete a bridge VALOR created, if no VM uses it any more (same safety net)."""
    for vm in pvesh("get", "/cluster/resources", type="vm") or []:
        if vm.get("node") != node:
            continue
        cfg = pvesh("get", f"/nodes/{node}/qemu/{vm['vmid']}/config") if vm.get("type") == "qemu" else {}
        if any(k.startswith("net") and f"bridge={name}" in str(v) for k, v in (cfg or {}).items()):
            ui.warn(f"bridge {name} is still used by VM {vm['vmid']}; leaving it")
            return
    if Path("/etc/network/interfaces.new").exists():
        ui.warn(f"pending network changes on this node; not removing {name} (remove it in the Proxmox UI)")
        return
    backup = Path(f"/root/valor-net-backup-{time.strftime('%Y%m%d-%H%M%S')}")
    backup.mkdir(parents=True)
    shutil.copy2("/etc/network/interfaces", backup / "interfaces")
    try:
        pvesh("delete", f"/nodes/{node}/network/{name}")
    except CommandError as e:
        ui.warn(f"could not remove {name}: {e}")
        return
    unit = f"valor-net-rollback-rm-{name}"
    run(["systemd-run", "--quiet", f"--on-active={ROLLBACK_SECONDS}", f"--unit={unit}", "/bin/sh", "-c",
         f"cp {backup}/interfaces /etc/network/interfaces && rm -f /etc/network/interfaces.new && ifreload -a"])
    pvesh("set", f"/nodes/{node}/network")
    time.sleep(3)
    if _gateway_ok():
        run(["systemctl", "stop", f"{unit}.timer"], check=False)
        ui.ok(f"bridge {name} removed")
    else:
        ui.warn(f"gateway check failed after removing {name}; the previous configuration returns within 5 minutes")
