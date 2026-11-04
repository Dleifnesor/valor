"""OS templates from official cloud images: download, verify (signature + checksum), bake in the guest agent.

A template is built once per OS: the cloud image is downloaded, its checksum list's GPG signature checked against
the publisher key shipped in templates/, the image's checksum checked, then a VM is created from the image and
booted once with VALOR's vendor-data (installs the guest agent and updates, then powers off) and turned into a
template. Ranges clone it (linked clones). Windows templates are built from ISOs instead (windows.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import tomllib
from pathlib import Path

from . import SOURCE, ui
from .answers import Answers
from .discover import Facts
from .record import Record
from .sh import CommandError, pvesh, run

VENDOR_SNIPPET = "valor-template-vendor.yaml"


def catalog() -> dict:
    with open(SOURCE / "templates" / "catalog.toml", "rb") as fh:
        return tomllib.load(fh)


def os_tag(os_name: str) -> str:
    return "os-" + os_name.replace(".", "-")


V3_FLAGS = {"avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "movbe", "xsave"}


def cpu_type(facts: Facts, entry: dict | None = None) -> str:
    """CPU model for VMs: x86-64-v2(-AES) by default (AES-NI is missing on some older servers); OSes that need
    x86-64-v3 (e.g. the RHEL 10 family) get it when the host supports it."""
    if (entry or {}).get("cpu_min") == "x86-64-v3":
        missing = V3_FLAGS - facts.cpu_flags
        if missing:
            raise SystemExit(f"this OS needs an x86-64-v3 CPU; the host lacks {sorted(missing)}")
        return "x86-64-v3"
    return "x86-64-v2-AES" if facts.has_aes else "x86-64-v2"


def existing(facts: Facts, settings: Answers, os_name: str) -> dict | None:
    """The newest template of this OS that this installation built on this node, if any."""
    found = _same_os(facts, settings, os_name)
    return max(found, key=lambda template: int(template["vmid"])) if found else None


def _fetch(url: str, dest: Path) -> None:
    run(["curl", "-fsSL", "--retry", "3", "--max-time", "3600", "-o", str(dest), url])


def _image_url(entry: dict, sums_text: str) -> str:
    """Fixed image_url, or the newest file matching image_pattern in the checksum list (versioned names)."""
    if entry.get("image_url"):
        return entry["image_url"]
    from valor.isos import _natural, parse_checksums
    names = sorted((name for name in parse_checksums(sums_text) if re.fullmatch(entry["image_pattern"], name)),
                   key=_natural)
    if not names:
        raise SystemExit(f"no image matching {entry['image_pattern']} in {entry['checksums_url']}")
    return entry["image_base"] + names[-1]


def _verify(entry: dict, image: Path, sums: Path, work: Path) -> None:
    """Refuse the image unless (where the publisher signs it) the checksum list carries a valid signature by the
    key shipped with VALOR, and the image matches its checksum in that list."""
    if entry.get("checksums_sig_url"):
        fingerprint = entry["signing_key_fingerprint"].replace(" ", "").upper()
        gnupg = work / "gnupg"                           # a keyring holding only the publisher's key
        gnupg.mkdir(mode=0o700)
        gpg = ["gpg", "--homedir", str(gnupg), "--batch", "--no-tty"]
        run([*gpg, "-q", "--import", str(SOURCE / "templates" / entry["signing_key_file"])])
        imported = [line.split(":")[9] for line in run([*gpg, "--with-colons", "--fingerprint"]).stdout.splitlines()
                    if line.startswith("fpr:")]
        if fingerprint not in imported:
            raise SystemExit(f"the shipped signing key does not have the expected fingerprint {fingerprint}")
        signature = work / "sums.sig"
        _fetch(entry["checksums_sig_url"], signature)
        result = run([*gpg, "--status-fd", "1", "--verify", str(signature), str(sums)], check=False)
        if f"VALIDSIG {fingerprint}" not in result.stdout:
            raise SystemExit("the checksum list's signature is NOT valid - refusing this image")
        ui.ok(f"checksum list signed by {fingerprint[-16:]}")
    from valor.isos import parse_checksums
    name = entry["image_url"].rsplit("/", 1)[1]
    expected = parse_checksums(sums.read_text()).get(name)          # GNU and BSD style lists
    if not expected:
        raise SystemExit(f"{name} is not in the checksum list")
    digest = hashlib.new(entry.get("checksum_type", "sha256"))
    with image.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise SystemExit(f"checksum mismatch for {name} - refusing this image")
    ui.ok(f"{entry.get('checksum_type', 'sha256')} checksum matches")


def _free_vmid(settings: Answers, facts: Facts, skip: set[int] = frozenset()) -> int:
    first, last = settings.vmid_templates
    in_use = {int(vm["vmid"]) for vm in pvesh("get", "/cluster/resources", type="vm")}
    used = facts.used_vmids | in_use | skip
    for vmid in range(first, last + 1):
        if vmid not in used:
            return vmid
    raise SystemExit(f"no free VMID left for templates in {first}-{last}")


def _create(settings: Answers, facts: Facts, command) -> int:
    """Create a template VM on a free VMID. `qm create` claims the id atomically; if another installer run took the
    same id a moment earlier, take the next one."""
    skip: set[int] = set()
    for _ in range(10):
        vmid = _free_vmid(settings, facts, skip)
        result = run(command(vmid), check=False)
        if result.returncode == 0:
            return vmid
        if "already exists" not in result.stderr:
            raise CommandError(command(vmid)[:3], result.returncode, result.stdout, result.stderr)
        skip.add(vmid)
    raise SystemExit("could not claim a free template VMID (another installer run keeps taking them)")


def ensure_snippets(settings: Answers, facts: Facts, rec: Record) -> str:
    """Make sure the snippet storage allows snippets; put the template vendor-data there. Returns its volume id."""
    storage = facts.storage(settings.snippets_storage)
    if "snippets" not in storage.content:
        content = ",".join(sorted(storage.content | {"snippets"}))
        pvesh("set", f"/storage/{storage.id}", content=content)
        rec.set("snippets_enabled_on", storage.id)
        ui.ok(f"storage {storage.id} now also holds cloud-init snippets")
    target = Path(storage.path) / "snippets" / VENDOR_SNIPPET
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SOURCE / "templates" / "vendor-data.yaml", target)
    rec.set("snippet", str(target))
    return f"{storage.id}:snippets/{VENDOR_SNIPPET}"


def build(settings: Answers, facts: Facts, rec: Record, os_name: str, snippet: str) -> int:
    """Build one Linux template from its cloud image; returns its VMID."""
    entry = catalog()[os_name]
    scratch = Path(facts.storage(settings.snippets_storage).path) / "valor-tmp"     # room for multi-GB images
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        image, entry = _download_verified(entry, Path(tmp))
        vmid = _create_template_vm(settings, facts, os_name, entry, image, snippet)
    rec.add_template({"os": os_name, "vmid": vmid, "built": True})
    _first_boot(vmid)
    run(["qm", "set", str(vmid), "--delete", "cicustom,ipconfig0,nameserver"])
    run(["qm", "template", str(vmid)])
    ui.ok(f"template {vmid} ({os_name}) ready")
    return vmid


def _download_verified(entry: dict, work: Path) -> tuple[Path, dict]:
    """Download the image and verify it. Returns (the disk image, the catalog entry with the image's URL)."""
    sums, image = work / "sums", work / "image.download"
    _fetch(entry["checksums_url"], sums)
    entry = {**entry, "image_url": _image_url(entry, sums.read_text())}
    ui.info(f"downloading {entry['image_url']}")
    _fetch(entry["image_url"], image)
    _verify(entry, image, sums, work)
    if entry.get("archive") == "tar.xz":                        # e.g. Kali: a raw disk inside a tarball
        unpacked = work / "unpacked"
        unpacked.mkdir()
        run(["tar", "-xJf", str(image), "-C", str(unpacked)])
        disks = sorted(path for path in unpacked.rglob("*")
                       if path.is_file() and path.suffix in (".raw", ".qcow2", ".img"))
        if not disks:
            raise SystemExit("the image archive contains no disk image")
        image.unlink()
        image = disks[0]
    image_format = json.loads(run(["qemu-img", "info", "--output=json", str(image)]).stdout)["format"]
    if image_format not in ("qcow2", "raw"):
        raise SystemExit(f"unexpected image format {image_format}")
    return image, entry


def _create_template_vm(settings: Answers, facts: Facts, os_name: str, entry: dict, image: Path,
                        snippet: str) -> int:
    """A VM with the image imported as its disk and VALOR's vendor-data for its first boot."""
    range_storage = facts.storage(settings.range_storage)
    disk = (f"{range_storage.id}:0,import-from={image},discard=on,ssd=1,iothread=1"
            + (",format=qcow2" if range_storage.file_based else ""))
    net = f"virtio,bridge={settings.vm_bridge}" + (f",tag={settings.vm_vlan}" if settings.vm_vlan else "")
    # the first boot needs internet: it uses the VALOR VM's network settings (its address is free now)
    ip_config = "ip=dhcp" if settings.vm_ip == "dhcp" else f"ip={settings.vm_ip},gw={settings.vm_gateway}"
    description = (f"VALOR {settings.id} template for {os_name}: {entry.get('description', '')}. Built "
                   f"{time.strftime('%Y-%m-%d %H:%M %Z')} from {entry['image_url']} (verified). Managed by the VALOR "
                   "installer; do not edit.")
    name = "tpl-" + os_name.replace(".", "")

    def command(vmid: int) -> list[str]:
        return ["qm", "create", str(vmid), "--name", name, "--pool", settings.pool_templates, "--ostype", "l26",
                "--memory", "2048", "--cores", "2", "--cpu", cpu_type(facts, entry), "--scsihw", "virtio-scsi-single",
                "--scsi0", disk, "--ide2", f"{range_storage.id}:cloudinit", "--boot", "order=scsi0",
                "--serial0", "socket", "--vga", "serial0", "--agent", "enabled=1,fstrim_cloned_disks=1",
                "--net0", net, "--ipconfig0", ip_config,
                *(["--nameserver", " ".join(settings.vm_dns)] if settings.vm_ip != "dhcp" and settings.vm_dns else []),
                "--ciuser", "valor", "--ciupgrade", "0", "--cicustom", f"vendor={snippet}",
                "--tags", f"valor-template;{os_tag(os_name)}", "--description", description]

    vmid = _create(settings, facts, command)
    ui.info(f"created template VM {vmid} ({name}) on {range_storage.id}")
    return vmid


def _first_boot(vmid: int) -> None:
    """Boot once: the vendor-data installs the guest agent and updates, then powers the VM off."""
    run(["qm", "disk", "resize", str(vmid), "scsi0", "8G"])
    ui.info("first boot: installing the guest agent and updates; the VM powers itself off when done (a few minutes)")
    run(["qm", "start", str(vmid)])
    end = time.time() + 1800
    while time.time() < end:
        if run(["qm", "status", str(vmid)], check=False).stdout.strip().endswith("stopped"):
            return
        time.sleep(5)
    raise SystemExit(f"template VM {vmid} did not power off within 30 minutes; check its console in Proxmox")


def iso_catalog() -> dict:
    with open(SOURCE / "templates" / "isos.toml", "rb") as fh:
        return tomllib.load(fh)


def ensure(settings: Answers, facts: Facts, rec: Record, wanted: list[str], rebuild: bool = False) -> dict[str, int]:
    """rebuild: build a fresh template even if one exists (new point release, current updates, fixed preparation)
    and then retire the old ones. Ranges always clone the newest template of an OS."""
    from . import windows
    out: dict[str, int] = {}
    snippet = None                      # the vendor-data snippet, set up once, only when a Linux image is built
    cat = catalog()
    for os_name in wanted:
        have = existing(facts, settings, os_name)
        if have and not rebuild:
            out[os_name] = int(have["vmid"])
            ui.ok(f"{os_name}: reusing template {have['vmid']}")
            continue
        entry = cat[os_name]
        if entry.get("family") == "windows":
            out[os_name] = windows.build(settings, facts, rec, os_name, entry, iso_catalog(), cpu_type(facts, entry))
        else:
            if snippet is None:
                snippet = ensure_snippets(settings, facts, rec)
            out[os_name] = build(settings, facts, rec, os_name, snippet)
        if rebuild:
            for old in _same_os(facts, settings, os_name):
                if int(old["vmid"]) != out[os_name]:
                    retire(rec, int(old["vmid"]), os_name)
    return out


def _same_os(facts: Facts, settings: Answers, os_name: str) -> list[dict]:
    """This installation's templates of an OS on this node (found by tag, like the engine does)."""
    return [template for template in facts.valor_templates
            if template.get("node") == facts.node and template.get("pool") == settings.pool_templates
            and os_tag(os_name) in re.split(r"[;, ]", template.get("tags") or "")]


def retire(rec: Record, vmid: int, os_name: str) -> None:
    """Remove an old template this installer built. Proxmox refuses while linked clones still use its disks."""
    if not any(template["vmid"] == vmid and template.get("built") for template in rec.objects.get("templates", [])):
        ui.info(f"old {os_name} template {vmid} was not built by the installer: left in place (ranges use the newest)")
        return
    result = run(["qm", "destroy", str(vmid), "--purge", "1"], check=False)
    if result.returncode:
        ui.warn(f"old {os_name} template {vmid} kept: {(result.stderr or result.stdout).strip().splitlines()[-1:]}")
        return
    rec.remove_template(vmid)
    ui.ok(f"old {os_name} template {vmid} removed")


def remove_built(rec: Record) -> None:
    """Uninstall: remove the templates this installer built (reused ones are not ours) and the snippet."""
    for template in rec.objects.get("templates", []):
        if not template.get("built"):
            continue
        try:
            run(["qm", "destroy", str(template["vmid"]), "--purge", "1"])
            ui.ok(f"template {template['vmid']} ({template['os']}) removed")
        except CommandError as e:
            ui.warn(f"template {template['vmid']} kept: {str(e).splitlines()[-1]}")
    snippet = rec.objects.get("snippet")
    if snippet and os.path.exists(snippet):
        os.unlink(snippet)
