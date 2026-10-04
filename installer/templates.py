"""OS templates from official cloud images: download, verify (signature + checksum), bake in the guest agent."""

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


def existing(facts: Facts, a: Answers, os_name: str) -> dict | None:
    found = [t for t in facts.valor_templates
             if t.get("node") == facts.node and t.get("pool") == a.pool_templates
             and os_tag(os_name) in re.split(r"[;, ]", t.get("tags") or "")]
    return max(found, key=lambda t: int(t["vmid"])) if found else None


def _fetch(url: str, dest: Path) -> None:
    run(["curl", "-fsSL", "--retry", "3", "--max-time", "3600", "-o", str(dest), url])


def _image_url(entry: dict, sums_text: str) -> str:
    """Fixed image_url, or the newest file matching image_pattern in the checksum list (versioned names)."""
    if entry.get("image_url"):
        return entry["image_url"]
    from valor.isos import _natural, parse_checksums
    names = sorted((n for n in parse_checksums(sums_text) if re.fullmatch(entry["image_pattern"], n)), key=_natural)
    if not names:
        raise SystemExit(f"no image matching {entry['image_pattern']} in {entry['checksums_url']}")
    return entry["image_base"] + names[-1]


def _verify(entry: dict, image: Path, sums: Path, work: Path) -> None:
    if entry.get("checksums_sig_url"):
        fpr = entry["signing_key_fingerprint"].replace(" ", "").upper()
        gnupg = work / "gnupg"
        gnupg.mkdir(mode=0o700)
        env_cmd = ["gpg", "--homedir", str(gnupg), "--batch", "--no-tty"]
        run([*env_cmd, "-q", "--import", str(SOURCE / "templates" / entry["signing_key_file"])])
        fprs = [l.split(":")[9] for l in run([*env_cmd, "--with-colons", "--fingerprint"]).stdout.splitlines()
                if l.startswith("fpr:")]
        if fpr not in fprs:
            raise SystemExit(f"the shipped signing key does not have the expected fingerprint {fpr}")
        sig = work / "sums.sig"
        _fetch(entry["checksums_sig_url"], sig)
        res = run([*env_cmd, "--status-fd", "1", "--verify", str(sig), str(sums)], check=False)
        if f"VALIDSIG {fpr}" not in res.stdout:
            raise SystemExit("the checksum list's signature is NOT valid - refusing this image")
        ui.ok(f"checksum list signed by {fpr[-16:]}")
    from valor.isos import parse_checksums
    name = entry["image_url"].rsplit("/", 1)[1]
    want = parse_checksums(sums.read_text()).get(name)          # GNU and BSD style lists
    if not want:
        raise SystemExit(f"{name} is not in the checksum list")
    h = hashlib.new(entry.get("checksum_type", "sha256"))
    with image.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != want:
        raise SystemExit(f"checksum mismatch for {name} - refusing this image")
    ui.ok(f"{entry.get('checksum_type', 'sha256')} checksum matches")


def _free_vmid(a: Answers, facts: Facts, skip: set[int] = frozenset()) -> int:
    lo, hi = a.vmid_templates
    used = facts.used_vmids | set(int(x["vmid"]) for x in pvesh("get", "/cluster/resources", type="vm")) | skip
    for v in range(lo, hi + 1):
        if v not in used:
            return v
    raise SystemExit(f"no free VMID left for templates in {lo}-{hi}")


def _create(a: Answers, facts: Facts, command) -> int:
    """Create a template VM on a free VMID. `qm create` claims the id atomically; if another installer run took the
    same id a moment earlier, take the next one."""
    skip: set[int] = set()
    for _ in range(10):
        vmid = _free_vmid(a, facts, skip)
        res = run(command(vmid), check=False)
        if res.returncode == 0:
            return vmid
        if "already exists" not in res.stderr:
            raise CommandError(command(vmid)[:3], res.returncode, res.stdout, res.stderr)
        skip.add(vmid)
    raise SystemExit("could not claim a free template VMID (another installer run keeps taking them)")


def ensure_snippets(a: Answers, facts: Facts, rec: Record) -> str:
    """Make sure the snippet storage allows snippets; put the template vendor-data there. Returns its volume id."""
    st = facts.storage(a.snippets_storage)
    if "snippets" not in st.content:
        content = ",".join(sorted(st.content | {"snippets"}))
        pvesh("set", f"/storage/{st.id}", content=content)
        rec.set("snippets_enabled_on", st.id)
        ui.ok(f"storage {st.id} now also holds cloud-init snippets")
    target = Path(st.path) / "snippets" / VENDOR_SNIPPET
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SOURCE / "templates" / "vendor-data.yaml", target)
    rec.set("snippet", str(target))
    return f"{st.id}:snippets/{VENDOR_SNIPPET}"


def build(a: Answers, facts: Facts, rec: Record, os_name: str, snippet: str) -> int:
    entry = catalog()[os_name]
    st = facts.storage(a.snippets_storage)
    scratch = Path(st.path) / "valor-tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        work = Path(tmp)
        sums, image = work / "sums", work / "image.download"
        _fetch(entry["checksums_url"], sums)
        entry = {**entry, "image_url": _image_url(entry, sums.read_text())}
        ui.info(f"downloading {entry['image_url']}")
        _fetch(entry["image_url"], image)
        _verify(entry, image, sums, work)
        if entry.get("archive") == "tar.xz":                        # e.g. Kali: a raw disk inside a tarball
            out = work / "unpacked"
            out.mkdir()
            run(["tar", "-xJf", str(image), "-C", str(out)])
            disks = sorted(p for p in out.rglob("*") if p.is_file() and p.suffix in (".raw", ".qcow2", ".img"))
            if not disks:
                raise SystemExit("the image archive contains no disk image")
            image.unlink()
            image = disks[0]
        fmt = json.loads(run(["qemu-img", "info", "--output=json", str(image)]).stdout)["format"]
        if fmt not in ("qcow2", "raw"):
            raise SystemExit(f"unexpected image format {fmt}")
        rs = facts.storage(a.range_storage)
        disk = f"{rs.id}:0,import-from={image},discard=on,ssd=1,iothread=1" + (",format=qcow2" if rs.file_based else "")
        net = f"virtio,bridge={a.vm_bridge}" + (f",tag={a.vm_vlan}" if a.vm_vlan else "")
        ipcfg = "ip=dhcp" if a.vm_ip == "dhcp" else f"ip={a.vm_ip},gw={a.vm_gateway}"   # the VALOR VM's address is free now
        desc = (f"VALOR {a.id} template for {os_name}: {entry.get('description', '')}. Built "
                f"{time.strftime('%Y-%m-%d %H:%M %Z')} from {entry['image_url']} (verified). Managed by the VALOR "
                "installer; do not edit.")
        name = "tpl-" + os_name.replace(".", "")
        vmid = _create(a, facts, lambda vmid: ["qm", "create", str(vmid), "--name", name, "--pool", a.pool_templates, "--ostype", "l26",
             "--memory", "2048", "--cores", "2", "--cpu", cpu_type(facts, entry), "--scsihw", "virtio-scsi-single",
             "--scsi0", disk, "--ide2", f"{rs.id}:cloudinit", "--boot", "order=scsi0", "--serial0", "socket",
             "--vga", "serial0", "--agent", "enabled=1,fstrim_cloned_disks=1", "--net0", net, "--ipconfig0", ipcfg,
             *(["--nameserver", " ".join(a.vm_dns)] if a.vm_ip != "dhcp" and a.vm_dns else []),
             "--ciuser", "valor", "--ciupgrade", "0", "--cicustom", f"vendor={snippet}",
             "--tags", f"valor-template;{os_tag(os_name)}", "--description", desc])
        ui.info(f"created template VM {vmid} ({name}) on {rs.id}")
    rec.add_template({"os": os_name, "vmid": vmid, "built": True})
    run(["qm", "disk", "resize", str(vmid), "scsi0", "8G"])
    ui.info("first boot: installing the guest agent and updates; the VM powers itself off when done (a few minutes)")
    run(["qm", "start", str(vmid)])
    end = time.time() + 1800
    while time.time() < end:
        if run(["qm", "status", str(vmid)], check=False).stdout.strip().endswith("stopped"):
            break
        time.sleep(5)
    else:
        raise SystemExit(f"template VM {vmid} did not power off within 30 minutes; check its console in Proxmox")
    run(["qm", "set", str(vmid), "--delete", "cicustom,ipconfig0,nameserver"])
    run(["qm", "template", str(vmid)])
    ui.ok(f"template {vmid} ({os_name}) ready")
    return vmid


def iso_catalog() -> dict:
    with open(SOURCE / "templates" / "isos.toml", "rb") as fh:
        return tomllib.load(fh)


def ensure(a: Answers, facts: Facts, rec: Record, wanted: list[str]) -> dict[str, int]:
    from . import windows
    out: dict[str, int] = {}
    snippet = None
    cat = catalog()
    for os_name in wanted:
        have = existing(facts, a, os_name)
        if have:
            out[os_name] = int(have["vmid"])
            ui.ok(f"{os_name}: reusing template {have['vmid']}")
            continue
        entry = cat[os_name]
        if entry.get("family") == "windows":
            out[os_name] = windows.build(a, facts, rec, os_name, entry, iso_catalog(), cpu_type(facts, entry))
            continue
        if snippet is None:
            snippet = ensure_snippets(a, facts, rec)
        out[os_name] = build(a, facts, rec, os_name, snippet)
    return out


def remove_built(rec: Record) -> None:
    for t in rec.objects.get("templates", []):
        if not t.get("built"):
            continue
        try:
            run(["qm", "destroy", str(t["vmid"]), "--purge", "1"])
            ui.ok(f"template {t['vmid']} ({t['os']}) removed")
        except CommandError as e:
            ui.warn(f"template {t['vmid']} kept: {str(e).splitlines()[-1]}")
    snippet = rec.objects.get("snippet")
    if snippet and os.path.exists(snippet):
        os.unlink(snippet)
