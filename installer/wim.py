"""Read the edition list from a Windows install.wim / install.esd (the XML metadata in the WIM header).

Lets the installer pick an edition by name (e.g. "Datacenter ... (Desktop Experience)") on any Windows ISO,
including ones people upload, instead of trusting fixed image indexes.
"""

from __future__ import annotations

import re
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

MAGIC = b"MSWIM\x00\x00\x00"


def editions_from_wim(path: str | Path) -> list[dict]:
    """[{index, name, description}] from a WIM/ESD file."""
    with open(path, "rb") as fh:
        head = fh.read(208)
        if head[:8] != MAGIC:
            raise ValueError(f"{path} is not a WIM file")
        # reshdr XmlData at offset 72: 7-byte packed size + 1 flag byte, 8-byte offset, 8-byte original size
        size = int.from_bytes(head[72:79], "little")
        offset = struct.unpack("<Q", head[80:88])[0]
        fh.seek(offset)
        raw = fh.read(size)
    text = raw.decode("utf-16-le", errors="replace").lstrip("﻿")
    root = ET.fromstring(text[text.index("<WIM"):])
    out = []
    for img in root.findall("IMAGE"):
        name = (img.findtext("NAME") or img.findtext("DISPLAYNAME") or "").strip()
        out.append({"index": int(img.get("INDEX", "0")), "name": name,
                    "description": (img.findtext("DESCRIPTION") or "").strip()})
    return sorted(out, key=lambda e: e["index"])


def editions_from_iso(iso: str | Path) -> list[dict]:
    """Mount the ISO read-only (root, on a Proxmox node) and read sources/install.wim or install.esd."""
    with tempfile.TemporaryDirectory(prefix="valor-iso-") as mnt:
        subprocess.run(["mount", "-o", "loop,ro", str(iso), mnt], check=True, capture_output=True)
        try:
            for name in ("install.wim", "install.esd"):
                p = Path(mnt) / "sources" / name
                if p.exists():
                    return editions_from_wim(p)
            raise ValueError("no sources/install.wim or install.esd on this ISO")
        finally:
            subprocess.run(["umount", mnt], check=False, capture_output=True)


def pick(editions: list[dict], pattern: str) -> dict:
    hits = [e for e in editions if re.search(pattern, e["name"])]
    if len(hits) != 1:
        names = ", ".join(e["name"] for e in editions)
        raise ValueError(f"edition pattern {pattern!r} matched {len(hits)} images (available: {names})")
    return hits[0]
