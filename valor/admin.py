"""valor-admin: root-only setup tasks that need more rights than the engine's API token.

    valor-admin template list
    valor-admin template build <os> [--force]
    valor-admin template test <os>
    valor-admin bridge create <name> [--node NODE] [--comment TEXT]
    valor-admin deploy

The engine itself (user 'valor', pool-scoped API token) can only *use* templates and
bridges. Creating them is an administrator action, kept here on purpose.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .config import load_config

IMPORT_DIR = Path("/var/lib/vz/import")
SNIPPET_DIR = Path("/var/lib/vz/snippets")
VENDOR_SNIPPET = "valor-template-vendor.yaml"


def run(cmd: list[str], check: bool = True, capture: bool = True, **kw) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, text=True, capture_output=capture, **kw)
    if check and res.returncode != 0:
        raise SystemExit(f"command failed ({res.returncode}): {' '.join(cmd)}\n{(res.stderr or '').strip()[-2000:]}")
    return res


def say(msg: str) -> None:
    print(f"[valor-admin] {msg}", flush=True)


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("valor-admin must run as root")


def load_catalog(cfg) -> dict:
    from .cluster import load_catalog as _load
    return _load(cfg)


def template_vmid(cfg, os_name: str) -> int:
    """The existing VALOR template for this OS (by tag, in the template pool), else the next free VMID >= 9000."""
    tag = "os-" + os_name.replace(".", "-")
    res = json.loads(run(["pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json"]).stdout)
    for r in res:
        tags = (r.get("tags") or "").replace(",", ";").split(";")
        if r.get("template") and r.get("pool") == cfg.template_pool and tag in tags and "valor-template" in tags:
            return int(r["vmid"])
    used = {int(r["vmid"]) for r in res}
    return next(v for v in range(9000, 10000) if v not in used)


def vm_exists(vmid: int) -> bool:
    return run(["qm", "status", str(vmid)], check=False).returncode == 0


def vm_tags(vmid: int) -> list[str]:
    out = run(["qm", "config", str(vmid)]).stdout
    for line in out.splitlines():
        if line.startswith("tags:"):
            return [t for t in line.split(":", 1)[1].strip().replace(",", ";").split(";") if t]
    return []


def wait_status(vmid: int, want: str, timeout: int) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        st = run(["qm", "status", str(vmid)], check=False).stdout.strip()
        if st.endswith(want):
            return True
        time.sleep(5)
    return False


def wait_agent(vmid: int, timeout: int) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if run(["qm", "agent", str(vmid), "ping"], check=False).returncode == 0:
            return True
        time.sleep(3)
    return False


# --------------------------------------------------------------------------- templates
def ensure_ssh_key(cfg) -> Path:
    pub = Path(cfg.ssh_public_key)
    priv = pub.with_suffix("")
    if not pub.exists():
        priv.parent.mkdir(parents=True, exist_ok=True)
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "valor-engine", "-f", str(priv)])
        shutil.chown(priv, "valor", "valor")
        shutil.chown(pub, "valor", "valor")
        os.chmod(priv, 0o600)
        say(f"created SSH key pair {priv} (used for cloud-init break-glass access)")
    return pub


def fetch(url: str, dest: Path) -> None:
    run(["curl", "-fsSL", "--retry", "3", "--max-time", "1800", "-o", str(dest), url])


def verify_signature(entry: dict, sums: Path, work: Path) -> None:
    sig_url = entry.get("checksums_sig_url")
    if not sig_url:
        say("no signature published for this checksum list; relying on HTTPS + checksum")
        return
    fpr = entry["signing_key_fingerprint"].replace(" ", "").upper()
    gnupg = work / "gnupg"
    gnupg.mkdir(mode=0o700)
    env = dict(os.environ, GNUPGHOME=str(gnupg))
    key = work / "key.asc"
    fetch(f"https://keyserver.ubuntu.com/pks/lookup?op=get&options=mr&search=0x{fpr}", key)
    run(["gpg", "-q", "--import", str(key)], env=env)
    got = run(["gpg", "--with-colons", "--fingerprint"], env=env).stdout
    fprs = [l.split(":")[9] for l in got.splitlines() if l.startswith("fpr:")]
    if fpr not in fprs:
        raise SystemExit(f"signing key fingerprint mismatch: expected {fpr}, got {fprs}")
    sig = work / "sums.sig"
    fetch(sig_url, sig)
    res = run(["gpg", "--status-fd", "1", "--verify", str(sig), str(sums)], env=env, check=False)
    if f"VALIDSIG {fpr}" not in res.stdout:
        raise SystemExit("checksum list signature is NOT valid - refusing to use this image")
    say(f"checksum list signature valid (key {fpr})")


def verify_checksum(entry: dict, image: Path, sums: Path) -> None:
    name = entry["image_url"].rsplit("/", 1)[1]
    algo = entry.get("checksum_type", "sha256")
    want = None
    for line in sums.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name:
            want = parts[0].lower()
    if not want:
        raise SystemExit(f"{name} not found in checksum list")
    h = hashlib.new(algo)
    with image.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != want:
        raise SystemExit(f"{algo} mismatch for {name}")
    say(f"{algo} checksum OK for {name}")


def template_build(cfg, os_name: str, force: bool) -> None:
    require_root()
    cat = load_catalog(cfg)
    if os_name not in cat:
        raise SystemExit(f"unknown os '{os_name}'. Known: {', '.join(cat)}")
    entry = cat[os_name]
    vmid = template_vmid(cfg, os_name)
    if vm_exists(vmid):
        if not force:
            raise SystemExit(f"VM {vmid} already exists (use --force to rebuild the template)")
        if "valor-template" not in vm_tags(vmid):
            raise SystemExit(f"VM {vmid} exists but is not a VALOR template - refusing to touch it")
        say(f"removing old template {vmid}")
        run(["qm", "destroy", str(vmid), "--purge", "1"])

    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    image = IMPORT_DIR / f"valor-{os_name}.qcow2"
    with tempfile.TemporaryDirectory(prefix="valor-tpl-") as tmp:
        work = Path(tmp)
        sums = work / "sums"
        fetch(entry["checksums_url"], sums)
        verify_signature(entry, sums, work)
        dl = work / "image"
        say(f"downloading {entry['image_url']}")
        fetch(entry["image_url"], dl)
        verify_checksum(entry, dl, sums)
        fmt = json.loads(run(["qemu-img", "info", "--output=json", str(dl)]).stdout)["format"]
        if fmt != "qcow2":
            raise SystemExit(f"unexpected image format {fmt}")
        shutil.move(str(dl), image)
    say(f"verified image stored as {image}")

    SNIPPET_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(Path(cfg.project_dir) / "templates" / "vendor-data.yaml", SNIPPET_DIR / VENDOR_SNIPPET)
    pub = ensure_ssh_key(cfg)

    name = "tpl-" + os_name.replace(".", "")
    desc = (f"VALOR template for {os_name}: {entry.get('description', '')}. "
            f"Built {time.strftime('%Y-%m-%d %H:%M %Z')} from {entry['image_url']} (signature/checksum verified). "
            "Managed by valor-admin; do not edit.")
    say(f"creating VM {vmid} ({name}) on {cfg.storage}")
    run(["qm", "create", str(vmid), "--name", name, "--pool", cfg.template_pool, "--ostype", "l26",
         "--memory", "2048", "--cores", "2", "--cpu", "x86-64-v2-AES",
         "--scsihw", "virtio-scsi-single",
         "--scsi0", f"{cfg.storage}:0,import-from=local:import/{image.name},discard=on,ssd=1,iothread=1",
         "--ide2", f"{cfg.storage}:cloudinit", "--boot", "order=scsi0",
         "--serial0", "socket", "--vga", "serial0", "--agent", "enabled=1,fstrim_cloned_disks=1",
         "--net0", f"virtio,bridge={cfg.uplink_bridge}", "--ipconfig0", "ip=dhcp",
         "--ciuser", cfg.guest_user, "--ciupgrade", "0", "--sshkeys", str(pub),
         "--cicustom", f"vendor=local:snippets/{VENDOR_SNIPPET}",
         "--tags", f"valor-template;os-{os_name.replace('.', '-')}", "--description", desc])
    run(["qm", "disk", "resize", str(vmid), "scsi0", "8G"])
    say("first boot: installing qemu-guest-agent and updates (the VM powers itself off when done)")
    run(["qm", "start", str(vmid)])
    if not wait_status(vmid, "stopped", 1800):
        raise SystemExit(f"template VM {vmid} did not power off within 30 min - check its console")
    run(["qm", "set", str(vmid), "--delete", "cicustom"])
    run(["qm", "template", str(vmid)])
    say(f"template {vmid} ({os_name}) ready")


def template_test(cfg, os_name: str) -> None:
    require_root()
    tpl = template_vmid(cfg, os_name)
    test_id = tpl + 90
    if vm_exists(test_id):
        raise SystemExit(f"test VMID {test_id} is in use")
    t0 = time.time()
    run(["qm", "clone", str(tpl), str(test_id), "--name", f"tpltest-{os_name.replace('.', '')}",
         "--pool", cfg.template_pool])
    try:
        run(["qm", "set", str(test_id), "--tags", "valor-template-test"])
        run(["qm", "start", str(test_id)])
        if not wait_agent(test_id, 300):
            raise SystemExit("guest agent did not respond within 5 minutes")
        boot = time.time() - t0
        out = run(["qm", "guest", "exec", str(test_id), "--", "sh", "-c",
                   ". /etc/os-release; echo $PRETTY_NAME; systemctl is-active qemu-guest-agent"]).stdout
        data = json.loads(out)
        say(f"clone booted and guest agent answered after {boot:.0f}s: {data.get('out-data', '').strip()!r}")
    finally:
        run(["qm", "stop", str(test_id)], check=False)
        run(["qm", "destroy", str(test_id), "--purge", "1"], check=False)


def template_list(cfg) -> None:
    cat = load_catalog(cfg)
    for os_name, entry in cat.items():
        vmid = template_vmid(cfg, os_name)
        state = "missing"
        if vm_exists(vmid):
            state = "template" if "template: 1" in run(["qm", "config", str(vmid)]).stdout else "VM (not a template!)"
        print(f"{os_name:14} vmid {vmid}  {state:10}  {entry.get('description', '')}")


# --------------------------------------------------------------------------- bridges
def bridge_create(name: str, node: str, comment: str) -> None:
    """Add a port-less, VLAN-aware bridge with backup, diff check and an automatic rollback timer."""
    require_root()
    if Path(f"/sys/class/net/{name}").exists():
        raise SystemExit(f"{name} already exists")
    backup = Path(f"/root/net-backup-{time.strftime('%Y%m%d-%H%M%S')}")
    backup.mkdir()
    shutil.copy2("/etc/network/interfaces", backup / "interfaces")
    shutil.copytree("/etc/network/interfaces.d", backup / "interfaces.d")
    Path("/etc/network/interfaces.new").unlink(missing_ok=True)
    run(["pvesh", "create", f"/nodes/{node}/network", "--iface", name, "--type", "bridge",
         "--bridge_vlan_aware", "1", "--autostart", "1", "--comments", comment])
    diff = run(["diff", "/etc/network/interfaces", "/etc/network/interfaces.new"], check=False).stdout
    removed = [l for l in diff.splitlines() if l.startswith("<") and l[1:].strip() and not l[1:].strip().startswith("iface nic")]
    if removed:
        Path("/etc/network/interfaces.new").unlink(missing_ok=True)
        raise SystemExit("pending network change would remove or alter existing lines - aborted:\n" + "\n".join(removed))
    unit = f"valor-net-rollback-{name}"
    run(["systemd-run", "--quiet", "--on-active=300", f"--unit={unit}", "/bin/sh", "-c",
         f"cp {backup}/interfaces /etc/network/interfaces && rm -f /etc/network/interfaces.new && ifreload -a"])
    say(f"rollback armed (5 min, unit {unit}); applying")
    run(["pvesh", "set", f"/nodes/{node}/network"])
    ok = run(["ping", "-c", "2", "-W", "2", run(["ip", "-4", "route", "show", "default"]).stdout.split()[2]],
             check=False).returncode == 0 and Path(f"/sys/class/net/{name}").exists()
    if not ok:
        raise SystemExit("verification failed - leaving the rollback timer armed (restores in 5 minutes)")
    run(["systemctl", "stop", f"{unit}.timer"], check=False)
    say(f"{name} created and verified; rollback cancelled (backup in {backup})")


# --------------------------------------------------------------------------- deploy
WRAPPERS = {
    "/opt/valor/bin/valor": "#!/bin/sh\nexec /opt/valor/venv/bin/valor \"$@\"\n",
    "/opt/valor/bin/valor-mcp": "#!/bin/sh\nexec /opt/valor/venv/bin/valor-mcp \"$@\"\n",
    "/usr/local/sbin/valor-admin": "#!/bin/sh\nexec /opt/valor/venv/bin/valor-admin \"$@\"\n",
}
SUDOERS = """# VALOR: the operator account may run the engine (CLI and MCP server) as the 'valor' user,
# which alone can read the Proxmox API token. Managed by valor-admin deploy.
Defaults:valorop !requiretty
valorop ALL=(valor) NOPASSWD: /opt/valor/bin/valor, /opt/valor/bin/valor *, /opt/valor/bin/valor-mcp
"""


def deploy(cfg) -> None:
    require_root()
    src = Path(cfg.project_dir)
    run(["/opt/valor/venv/bin/pip", "install", "-q", "--force-reinstall", "--no-deps", str(src)], capture=False)
    for path, body in WRAPPERS.items():
        p = Path(path)
        p.write_text(body)
        os.chmod(p, 0o755)
    sudoers = Path("/etc/sudoers.d/valor")
    tmp = sudoers.with_suffix(".tmp")
    tmp.write_text(SUDOERS)
    os.chmod(tmp, 0o440)
    run(["visudo", "-cf", str(tmp)])
    tmp.replace(sudoers)
    for d in ("journals",):
        p = src / d
        p.mkdir(exist_ok=True)
        shutil.chown(p, "valor", "valor")
        os.chmod(p, 0o2775)
    say("engine installed to /opt/valor/venv; wrappers and sudo rule in place")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="valor-admin", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("template")
    tsub = t.add_subparsers(dest="tcmd", required=True)
    tsub.add_parser("list")
    b = tsub.add_parser("build")
    b.add_argument("os")
    b.add_argument("--force", action="store_true")
    tt = tsub.add_parser("test")
    tt.add_argument("os")
    br = sub.add_parser("bridge")
    brs = br.add_subparsers(dest="bcmd", required=True)
    bc = brs.add_parser("create")
    bc.add_argument("name")
    bc.add_argument("--node", default=None)
    bc.add_argument("--comment", default="VALOR internal VLAN bridge (no physical port)")
    sub.add_parser("deploy")
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.cmd == "template":
        if args.tcmd == "list":
            template_list(cfg)
        elif args.tcmd == "build":
            template_build(cfg, args.os, args.force)
        elif args.tcmd == "test":
            template_test(cfg, args.os)
    elif args.cmd == "bridge":
        bridge_create(args.name, args.node or cfg.node, args.comment)
    elif args.cmd == "deploy":
        deploy(cfg)


if __name__ == "__main__":
    main()
