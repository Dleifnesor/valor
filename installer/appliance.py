"""The VALOR VM: create it from the Ubuntu template, then provision it over the guest agent.

Provisioning copies a release payload (engine, web service, built UI, roles, baselines, catalog) and the
installation's settings into the VM and runs appliance/setup.sh there. Secrets (API token, first admin password)
travel only over the agent channel and are deleted from the staging area when setup finishes.
"""

from __future__ import annotations

import io
import json
import secrets
import ssl
import string
import tarfile
import time
import urllib.request
from pathlib import Path

from . import SOURCE, VERSION, guest, ui
from .answers import Answers
from .discover import Facts
from .record import Record
from .sh import CommandError, pvesh, run
from .templates import cpu_type

STAGE = "/var/lib/valor-install"
PAYLOAD_DIRS = ["valor", "appliance", "roles", "baselines", "templates", "examples"]
PAYLOAD_FILES = ["pyproject.toml", "README.md"]


def payload() -> bytes:
    """tar.gz of everything the VM needs (no tests, no dev tooling, no node_modules)."""
    dist = SOURCE / "web" / "dist" / "index.html"
    if not dist.is_file():
        raise SystemExit("web/dist is missing: this checkout has no built web UI (see web/README.md)")
    buf = io.BytesIO()

    def skip(info: tarfile.TarInfo):
        parts = Path(info.name).parts
        if "__pycache__" in parts or info.name.endswith(".pyc"):
            return None
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        return info

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for folder in PAYLOAD_DIRS:
            tar.add(SOURCE / folder, arcname=folder, filter=skip)
        tar.add(SOURCE / "web" / "dist", arcname="web/dist", filter=skip)
        for name in PAYLOAD_FILES:
            if (SOURCE / name).is_file():
                tar.add(SOURCE / name, arcname=name, filter=skip)
        version = f"{VERSION}\n".encode()
        version_file = tarfile.TarInfo("VERSION")
        version_file.size = len(version)
        tar.addfile(version_file, io.BytesIO(version))
    return buf.getvalue()


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    return json.dumps(str(value))          # a JSON string is a valid TOML basic string


def config_toml(settings: Answers, facts: Facts, bridge: str, api_host: str, ca_pem: str | None, vm_ip: str) -> str:
    """/etc/valor/config.toml for the VALOR VM: what the engine may use on this cluster (read by valor/config.py and
    valor/web/config.py)."""
    first_vmid, last_vmid = settings.vmid_ranges
    url = f"https://{vm_ip}"
    sections = {
        "proxmox": {"api_host": api_host, "api_port": 8006,
                    "verify_ssl": "/etc/valor/pve-ca.pem" if ca_pem else True,
                    "token_file": "/etc/valor/token.json", "node": facts.node},
        "resources": {"pool": settings.pool_ranges, "template_pool": settings.pool_templates, "storage": settings.range_storage,
                      "segment_bridge": bridge, "uplink_bridge": settings.vm_bridge, "uplink_vlan": settings.vm_vlan,
                      "vmid_min": first_vmid, "vmid_max": last_vmid,
                      "vlan_min": settings.vlan_min, "vlan_max": settings.vlan_max,
                      "max_memory_fraction": 0.7, "reserved_networks": settings.reserved_networks,
                      "iso_storage": settings.iso_storage, "iso_storages": settings.iso_storages},
        "paths": {"content_dir": "/opt/valor/share", "data_dir": "/var/lib/valor/data",
                  "state_dir": "/var/lib/valor/state", "ssh_public_key": "/etc/valor/ssh/id_ed25519.pub"},
        "guest": {"default_os": "ubuntu-24.04", "nameservers": settings.nameservers, "internet_probe": settings.internet_probe,
                  "guest_user": "valor"},
        "web": {"db": "/var/lib/valor/web.sqlite3", "secret_key_file": "/etc/valor/secret.key",
                "instance_name": settings.instance_name, "tls_cert": "/etc/valor/tls/server.crt",
                "appliance_vmid": settings.vmid_vm, "public_url": url},
    }
    lines = [f"# VALOR {VERSION} configuration, written by the installer on {facts.node} "
             f"({time.strftime('%Y-%m-%d %H:%M %Z')}).",
             "# Re-run the installer (./install.sh --upgrade) instead of editing by hand.", ""]
    for name, values in sections.items():
        lines.append(f"[{name}]")
        lines += [f"{key} = {_toml(value)}" for key, value in values.items()]
        lines.append("")
    return "\n".join(lines)


def appliance_env(settings: Answers, facts: Facts, mode: str, api_host: str) -> str:
    """Settings for setup.sh (values were validated by the installer: CIDRs, IPs, DNS labels)."""
    ssh_from = sorted({node["ip"] for node in facts.nodes if node.get("ip")} | {facts.node_ip})    # break-glass SSH
    env = {
        "MODE": mode,
        "VALOR_VERSION": VERSION,
        "ALLOWED_NETWORKS": " ".join(settings.allowed_networks),
        "SSH_FROM": " ".join(ssh_from),
        "TLS_MODE": settings.tls,
        "VM_HOSTNAME": settings.vm_hostname,
        "VM_DOMAIN": settings.vm_domain,
        "ADMIN_USER": settings.admin_user,
        "PVE_HOST": api_host,
        "PVE_IP": facts.node_ip,
    }
    return "".join(f'{key}="{value}"\n' for key, value in env.items())


def new_password() -> str:
    """The first admin's one-time password: 4 groups of 6 letters/digits (changed at the first sign-in)."""
    alphabet = string.ascii_letters + string.digits
    return "-".join("".join(secrets.choice(alphabet) for _ in range(6)) for _ in range(4))


def _qm(*args: str) -> str:
    return run(["qm", *args]).stdout


def create(settings: Answers, facts: Facts, rec: Record, template: int) -> int:
    """Clone the Ubuntu template into the VALOR VM (a full clone: it must not depend on a template) and set its
    size, network, cloud-init user and break-glass SSH key. An existing VALOR VM (a resumed install) is kept."""
    vmid = settings.vmid_vm
    exists = run(["qm", "status", str(vmid)], check=False).returncode == 0
    if exists:
        config = _qm("config", str(vmid))
        if "valor-appliance" not in config:
            raise SystemExit(f"VM {vmid} exists and is not a VALOR VM; choose another vmid_start")
        ui.ok(f"VALOR VM {vmid} exists (resuming)")
        rec.set("vm", {"vmid": vmid, "name": settings.vm_hostname})
        return vmid
    ui.info(f"cloning template {template} into VM {vmid} on {settings.vm_storage}")
    run(["qm", "clone", str(template), str(vmid), "--name", settings.vm_hostname, "--full", "1", "--storage", settings.vm_storage,
         "--pool", settings.pool_system])
    rec.set("vm", {"vmid": vmid, "name": settings.vm_hostname})
    key = rec_key(settings)
    net = f"virtio,bridge={settings.vm_bridge}" + (f",tag={settings.vm_vlan}" if settings.vm_vlan else "")
    ip_config = "ip=dhcp" if settings.vm_ip == "dhcp" else f"ip={settings.vm_ip},gw={settings.vm_gateway}"
    description = (f"VALOR {VERSION} appliance (instance '{settings.id}'): engine + web UI. Created by the VALOR "
                   f"installer on {time.strftime('%Y-%m-%d')}. Manage with ./install.sh --status | --upgrade | --uninstall.")
    run(["qm", "set", str(vmid), "--cores", str(settings.vm_cores), "--memory", str(settings.vm_memory),
         "--cpu", cpu_type(facts), "--net0", net, "--ipconfig0", ip_config, "--ciuser", "valoradmin",
         "--sshkeys", str(key) + ".pub", "--onboot", "1", "--startup", "order=1",
         "--tags", f"valor-appliance;valor-{settings.id}",
         "--description", description, "--agent", "enabled=1,fstrim_cloned_disks=1",
         *(["--nameserver", " ".join(settings.vm_dns)] if settings.vm_ip != "dhcp" and settings.vm_dns else []),
         *(["--searchdomain", settings.vm_domain] if settings.vm_domain else [])])
    run(["qm", "disk", "resize", str(vmid), "scsi0", f"{settings.vm_disk}G"])
    ui.ok(f"VALOR VM {vmid} created ({settings.vm_cores} vCPU, {settings.vm_memory} MiB, {settings.vm_disk} GiB, {net})")
    return vmid


def rec_key(settings: Answers) -> Path:
    """Break-glass SSH key for the VALOR VM, kept cluster-wide and root-only."""
    from . import RECORD_DIR
    key = RECORD_DIR / f"{settings.id}-ssh_ed25519"
    if not key.exists():
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        tmp = Path("/root") / f".valor-{settings.id}-key"
        for leftover in (tmp, Path(str(tmp) + ".pub")):
            leftover.unlink(missing_ok=True)
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"valor-{settings.id}-breakglass", "-f", str(tmp)])
        key.write_bytes(tmp.read_bytes())
        Path(str(key) + ".pub").write_bytes(Path(str(tmp) + ".pub").read_bytes())
        tmp.unlink()
        Path(str(tmp) + ".pub").unlink()
    return key


def boot(facts: Facts, vmid: int) -> str:
    """Start the VM if needed and wait until it is usable (guest agent, cloud-init done, an IPv4 address)."""
    if "running" not in _qm("status", str(vmid)):
        run(["qm", "start", str(vmid)])
    ui.info("waiting for the VM to boot and its guest agent to answer")
    guest.wait_agent(facts.node, vmid, 420)
    guest.check(facts.node, vmid, "cloud-init status --wait >/dev/null 2>&1 || true", timeout=900)
    ip = None
    for _ in range(40):
        ip = guest.ipv4(facts.node, vmid)
        if ip:
            break
        time.sleep(3)
    if not ip:
        raise SystemExit(f"VM {vmid} has no IPv4 address (no DHCP lease?). Check its console, or use a static IP.")
    ui.ok(f"VALOR VM is up at {ip}")
    return ip


def provision(settings: Answers, facts: Facts, vmid: int, mode: str, files: dict[str, str | bytes]) -> None:
    """files: staging-relative name -> content (config, secrets). Runs setup.sh and streams its log."""
    node = facts.node
    data = payload()
    ui.info(f"copying VALOR {VERSION} into the VM ({len(data) // 1024} KiB over the guest agent)")
    guest.check(node, vmid, f"set -e; rm -rf {STAGE}; install -d -m 0700 {STAGE}")
    guest.write_file(node, vmid, f"{STAGE}/payload.tar.gz", data)
    for name, content in files.items():
        guest.write_file(node, vmid, f"{STAGE}/{name}", content)
    guest.check(node, vmid, f"set -e; mkdir -p {STAGE}/src; tar -xzf {STAGE}/payload.tar.gz -C {STAGE}/src")
    ui.info("running the appliance setup inside the VM (packages, TLS, firewall, services) - a few minutes")
    # setup.sh runs for minutes: start it in the background and follow its log (its "[time] step" lines)
    pid = guest.start_script(node, vmid, f"exec bash {STAGE}/src/appliance/setup.sh {mode} > /var/log/valor-setup.log 2>&1")
    shown, end = 0, time.time() + 2400
    while time.time() < end:
        time.sleep(4)
        try:
            log = guest.read_file(node, vmid, "/var/log/valor-setup.log")
        except CommandError:
            log = ""
        lines = log.splitlines()
        for line in lines[shown:]:
            if line.startswith("[") or line.startswith("ERROR") or line.startswith("WARN"):
                ui.info(ui.dim(line))
        shown = len(lines)
        status = guest.status(node, vmid, pid)
        if status.get("exited"):
            if int(status.get("exitcode", 1)) != 0:
                tail = "\n".join(lines[-25:])
                raise SystemExit(f"appliance setup failed (exit {status.get('exitcode')}). Last lines of "
                                 f"/var/log/valor-setup.log in the VM:\n{tail}")
            ui.ok("appliance setup finished")
            return
    raise SystemExit("appliance setup did not finish within 40 minutes; see /var/log/valor-setup.log in the VM")


JOBS_DIR = "/var/lib/valor/state/jobs"


def busy_jobs(facts: Facts, vmid: int) -> list[str]:
    """Jobs running or queued in the VALOR VM (an upgrade restarts the worker, which first finishes its job)."""
    code, out, _ = guest.script(facts.node, vmid, f"""
for f in {JOBS_DIR}/*/job.json; do
  [ -f "$f" ] && grep -qE '"state": "(running|queued)"' "$f" && basename "$(dirname "$f")"
done
true
""", timeout=60)
    return out.split() if code == 0 else []


def wait_jobs(facts: Facts, vmid: int, timeout: float = 3 * 3600) -> None:
    end = time.time() + timeout
    while time.time() < end:
        busy = busy_jobs(facts, vmid)
        if not busy:
            return
        ui.info(f"... waiting for {len(busy)} job(s): {', '.join(busy)}")
        time.sleep(30)
    raise SystemExit("VALOR jobs are still running; try the upgrade again later")


def ca_pem(facts: Facts, vmid: int) -> str:
    return guest.read_file(facts.node, vmid, "/etc/valor/tls/ca.crt")


def health(ip: str, ca: str | None) -> bool:
    """Does the web UI answer over HTTPS with a certificate that verifies (against VALOR's own CA when given)?"""
    context = ssl.create_default_context(cadata=ca) if ca else ssl.create_default_context()
    try:
        with urllib.request.urlopen(f"https://{ip}/api/health", context=context, timeout=15) as response:
            return json.load(response).get("ok") is True
    except Exception as e:
        ui.debug(f"health check: {e}")
        return False


def fingerprint(pem: str) -> str:
    der = ssl.PEM_cert_to_DER_cert(pem)
    import hashlib
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))      # AB:CD:EF... as browsers show it


def destroy(vmid: int) -> None:
    try:
        config = _qm("config", str(vmid))
    except CommandError:
        return
    if "valor-appliance" not in config:            # never delete a VM the installer didn't make
        ui.warn(f"VM {vmid} is not a VALOR VM; leaving it")
        return
    run(["qm", "stop", str(vmid), "--skiplock", "1"], check=False)
    run(["qm", "destroy", str(vmid), "--purge", "1", "--destroy-unreferenced-disks", "1"])
    ui.ok(f"VALOR VM {vmid} removed")


def pool_vms(pool: str) -> list[dict]:
    try:
        members = (pvesh("get", f"/pools/{pool}") or {}).get("members", [])
        return [member for member in members if member.get("type") == "qemu"]
    except CommandError:
        return []
