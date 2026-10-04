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


def cpu_type(facts: Facts) -> str:
    # x86-64-v2-AES needs AES-NI on the host; some older servers lack it.
    return "x86-64-v2-AES" if facts.has_aes else "x86-64-v2"


def existing(facts: Facts, a: Answers, os_name: str) -> dict | None:
    found = [t for t in facts.valor_templates
             if t.get("node") == facts.node and t.get("pool") == a.pool_templates
             and os_tag(os_name) in re.split(r"[;, ]", t.get("tags") or "")]
    return max(found, key=lambda t: int(t["vmid"])) if found else None


def _fetch(url: str, dest: Path) -> None:
    run(["curl", "-fsSL", "--retry", "3", "--max-time", "3600", "-o", str(dest), url])


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
    name = entry["image_url"].rsplit("/", 1)[1]
    want = next((l.split()[0].lower() for l in sums.read_text().splitlines()
                 if len(l.split()) >= 2 and l.split()[-1].lstrip("*") == name), None)
    if not want:
        raise SystemExit(f"{name} is not in the checksum list")
    h = hashlib.new(entry.get("checksum_type", "sha256"))
    with image.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != want:
        raise SystemExit(f"checksum mismatch for {name} - refusing this image")
    ui.ok(f"{entry.get('checksum_type', 'sha256')} checksum matches")


def _free_vmid(a: Answers, facts: Facts) -> int:
    lo, hi = a.vmid_templates
    used = facts.used_vmids | set(int(x["vmid"]) for x in pvesh("get", "/cluster/resources", type="vm"))
    for v in range(lo, hi + 1):
        if v not in used:
            return v
    raise SystemExit(f"no free VMID left for templates in {lo}-{hi}")


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
    vmid = _free_vmid(a, facts)
    st = facts.storage(a.snippets_storage)
    scratch = Path(st.path) / "valor-tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        work = Path(tmp)
        ui.info(f"downloading {entry['image_url']}")
        sums, image = work / "sums", work / "image.qcow2"
        _fetch(entry["checksums_url"], sums)
        _fetch(entry["image_url"], image)
        _verify(entry, image, sums, work)
        fmt = json.loads(run(["qemu-img", "info", "--output=json", str(image)]).stdout)["format"]
        if fmt != "qcow2":
            raise SystemExit(f"unexpected image format {fmt}")
        rs = facts.storage(a.range_storage)
        disk = f"{rs.id}:0,import-from={image},discard=on,ssd=1,iothread=1" + (",format=qcow2" if rs.file_based else "")
        net = f"virtio,bridge={a.vm_bridge}" + (f",tag={a.vm_vlan}" if a.vm_vlan else "")
        ipcfg = "ip=dhcp" if a.vm_ip == "dhcp" else f"ip={a.vm_ip},gw={a.vm_gateway}"   # the VALOR VM's address is free now
        desc = (f"VALOR {a.id} template for {os_name}: {entry.get('description', '')}. Built "
                f"{time.strftime('%Y-%m-%d %H:%M %Z')} from {entry['image_url']} (verified). Managed by the VALOR "
                "installer; do not edit.")
        name = "tpl-" + os_name.replace(".", "")
        ui.info(f"creating template VM {vmid} ({name}) on {rs.id}")
        run(["qm", "create", str(vmid), "--name", name, "--pool", a.pool_templates, "--ostype", "l26",
             "--memory", "2048", "--cores", "2", "--cpu", cpu_type(facts), "--scsihw", "virtio-scsi-single",
             "--scsi0", disk, "--ide2", f"{rs.id}:cloudinit", "--boot", "order=scsi0", "--serial0", "socket",
             "--vga", "serial0", "--agent", "enabled=1,fstrim_cloned_disks=1", "--net0", net, "--ipconfig0", ipcfg,
             *(["--nameserver", " ".join(a.vm_dns)] if a.vm_ip != "dhcp" and a.vm_dns else []),
             "--ciuser", "valor", "--ciupgrade", "0", "--cicustom", f"vendor={snippet}",
             "--tags", f"valor-template;{os_tag(os_name)}", "--description", desc])
    templates = rec.objects.get("templates", [])
    templates.append({"os": os_name, "vmid": vmid, "built": True})
    rec.set("templates", templates)
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


def ensure(a: Answers, facts: Facts, rec: Record, wanted: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    snippet = None
    for os_name in wanted:
        have = existing(facts, a, os_name)
        if have:
            out[os_name] = int(have["vmid"])
            ui.ok(f"{os_name}: reusing template {have['vmid']}")
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
