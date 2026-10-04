"""ISO library: list, download (catalog entry or URL), upload and delete installer media on the cluster's ISO
storage. Downloads and uploads run as Proxmox tasks; Proxmox verifies checksums, VALOR verifies signatures."""

from __future__ import annotations

import io
import os
import re
import secrets
import subprocess
import tempfile
import tomllib
from pathlib import Path

from .errors import ValorError

FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+=-]{0,200}\.iso$")
ALGORITHMS = ("md5", "sha1", "sha224", "sha256", "sha384", "sha512")


def catalog_file(cfg) -> Path:
    return cfg.catalog_file.parent / "isos.toml"


def load_catalog(cfg) -> dict:
    with catalog_file(cfg).open("rb") as fh:
        return tomllib.load(fh)


def _natural(name: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]


def parse_checksums(text: str) -> dict[str, str]:
    """filename -> hash, for GNU ('<hash>  <file>' / '<hash> *<file>') and BSD ('SHA256 (<file>) = <hash>') lists."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^[A-Z0-9-]+ \((.+)\) = ([0-9a-fA-F]+)$", line)
        if m:
            out[m.group(1)] = m.group(2).lower()
            continue
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{32,128}", parts[0]):
            out[parts[1].lstrip("*")] = parts[0].lower()
    return out


def _fetch(url: str) -> bytes:
    import requests
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise ValorError("download_failed", f"{url}: HTTP {r.status_code}")
    return r.content


def _verify_signature(cfg, entry: dict, data: bytes, sig: bytes) -> None:
    key = cfg.catalog_file.parent / entry["signing_key_file"]
    fpr = entry["signing_key_fingerprint"].replace(" ", "").upper()
    with tempfile.TemporaryDirectory() as home:
        env = {**os.environ, "GNUPGHOME": home}
        subprocess.run(["gpg", "--batch", "-q", "--import", str(key)], env=env, check=True, capture_output=True)
        (Path(home) / "data").write_bytes(data)
        (Path(home) / "sig").write_bytes(sig)
        res = subprocess.run(["gpg", "--batch", "--status-fd", "1", "--verify", f"{home}/sig", f"{home}/data"],
                             env=env, capture_output=True, text=True)
        if f"VALIDSIG {fpr}" not in res.stdout:
            raise ValorError("bad_signature", f"the checksum list for {entry['title']} is not signed by {fpr}",
                             hint="The mirror may be compromised or the key rotated; check the publisher's site.")


def resolve(cfg, entry_id: str) -> dict:
    """Catalog entry -> {url, filename, checksum, algorithm, verified}. Follows point releases via the checksum list."""
    cat = load_catalog(cfg)
    if entry_id not in cat:
        raise ValorError("unknown_iso", f"no catalog entry {entry_id}", hint=f"Known: {', '.join(cat)}")
    e = cat[entry_id]
    if "checksums_url" not in e:
        pinned = e.get("sha256")
        return {"id": entry_id, "url": e["url"], "filename": e["filename"], "checksum": pinned,
                "algorithm": "sha256" if pinned else None,
                "verified": "pinned checksum" if pinned else "https only", "note": e.get("note", "")}
    text = _fetch(e["checksums_url"])
    verified = "checksum (https)"
    if e.get("checksums_sig_url"):
        _verify_signature(cfg, e, text, _fetch(e["checksums_sig_url"]))
        verified = "signed checksum list"
    sums = parse_checksums(text.decode(errors="replace"))
    names = sorted((n for n in sums if re.fullmatch(e["pattern"], n)), key=_natural)
    if not names:
        raise ValorError("unknown_iso", f"no file matching {e['pattern']} in {e['checksums_url']}")
    name = names[-1]
    return {"id": entry_id, "url": e["base_url"] + name, "filename": name, "checksum": sums[name],
            "algorithm": e.get("algorithm", "sha256"), "verified": verified}


def list_isos(pve) -> list[dict]:
    out = []
    for storage in pve.cfg.iso_storages or ((pve.cfg.iso_storage,) if pve.cfg.iso_storage else ()):
        try:
            items = pve.storage_content(storage, "iso")
        except ValorError:
            continue
        for it in items:
            volid = it["volid"]
            out.append({"volid": volid, "storage": storage, "name": volid.split("/", 1)[-1], "size": it.get("size", 0),
                        "ctime": it.get("ctime", 0), "writable": storage == pve.cfg.iso_storage})
    return sorted(out, key=lambda i: i["name"].lower())


def check_filename(name: str) -> str:
    if not FILENAME.match(name or ""):
        raise ValorError("invalid_filename", "ISO file names: letters, digits, . _ + = - and ending in .iso")
    return name


def download(pve, url: str, filename: str, checksum: str | None = None, algorithm: str | None = None) -> str:
    if not pve.cfg.iso_storage:
        raise ValorError("no_iso_storage", "no ISO storage configured", hint="Re-run the installer (--upgrade).")
    if not url.startswith("https://"):
        raise ValorError("insecure_url", "downloads must use https://")
    if checksum and algorithm not in ALGORITHMS:
        raise ValorError("invalid_checksum", f"checksum algorithm must be one of {ALGORITHMS}")
    return pve.download_url(pve.cfg.iso_storage, url, check_filename(filename), checksum, algorithm)


def delete(pve, volid: str) -> None:
    storage, _, rest = volid.partition(":")
    if storage != pve.cfg.iso_storage or not rest.startswith("iso/"):
        raise ValorError("not_allowed", "only ISOs on VALOR's ISO storage can be deleted from VALOR")
    pve.delete_volume(storage, volid)


class MultipartFile(io.RawIOBase):
    """A streamed multipart/form-data body (known length), so multi-GB uploads never sit in memory."""

    def __init__(self, fields: dict[str, str], file_field: str, filename: str, path: str):
        self.boundary = "valor" + secrets.token_hex(16)
        head = b"".join(f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                        for k, v in fields.items())
        head += (f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
                 "Content-Type: application/octet-stream\r\n\r\n").encode()
        tail = f"\r\n--{self.boundary}--\r\n".encode()
        self._parts = [io.BytesIO(head), open(path, "rb"), io.BytesIO(tail)]
        self.length = len(head) + os.path.getsize(path) + len(tail)
        self._pos = 0

    @property
    def content_type(self) -> str:
        return f"multipart/form-data; boundary={self.boundary}"

    def __len__(self) -> int:
        return self.length

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        # requests computes the body length as len() - tell(); without tell() it falls back to chunked encoding
        return self._pos

    def read(self, size: int = -1) -> bytes:
        out = b""
        while self._parts and (size < 0 or len(out) < size):
            chunk = self._parts[0].read(-1 if size < 0 else size - len(out))
            if not chunk:
                self._parts.pop(0).close()
                continue
            out += chunk
        self._pos += len(out)
        return out

    def close(self) -> None:
        for p in self._parts:
            p.close()
        self._parts = []
        super().close()
