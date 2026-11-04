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
HEX_LENGTH = {"md5": 32, "sha1": 40, "sha224": 56, "sha256": 64, "sha384": 96, "sha512": 128}


def check_checksum(checksum: str, algorithm: str | None) -> str:
    """The algorithm for a hex checksum (given, or guessed from its length); raises if they don't fit together."""
    algorithm = algorithm or next((a for a, n in HEX_LENGTH.items() if n == len(checksum)), None)
    if algorithm not in HEX_LENGTH:
        raise ValorError("invalid_checksum", f"a {len(checksum)}-character checksum matches no supported algorithm")
    if len(checksum) != HEX_LENGTH[algorithm] or not re.fullmatch(r"[0-9a-fA-F]+", checksum):
        raise ValorError("invalid_checksum", f"a {algorithm} checksum has {HEX_LENGTH[algorithm]} hex characters, "
                                             f"this one has {len(checksum)}")
    return algorithm


def catalog_file(cfg) -> Path:
    return cfg.catalog_file.parent / "isos.toml"


def load_catalog(cfg) -> dict:
    with catalog_file(cfg).open("rb") as fh:
        return tomllib.load(fh)


def _natural(name: str):
    """Sort key that orders numbers by value: Rocky-10.2 before Rocky-10.10."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def parse_checksums(text: str) -> dict[str, str]:
    """filename -> hash, for GNU ('<hash>  <file>' / '<hash> *<file>') and BSD ('SHA256 (<file>) = <hash>') lists."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        bsd = re.match(r"^[A-Z0-9-]+ \((.+)\) = ([0-9a-fA-F]+)$", line)
        if bsd:
            out[bsd.group(1)] = bsd.group(2).lower()
            continue
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{32,128}", parts[0]):
            out[parts[1].lstrip("*")] = parts[0].lower()
    return out


def _fetch(url: str) -> bytes:
    import requests
    response = requests.get(url, timeout=60)
    if response.status_code != 200:
        raise ValorError("download_failed", f"{url}: HTTP {response.status_code}")
    return response.content


def _verify_signature(cfg, entry: dict, data: bytes, sig: bytes) -> None:
    """Check a checksum list's detached GPG signature against the publisher key shipped with VALOR. A throwaway
    keyring holds only that key, so nothing else on the system can vouch for the list."""
    key = cfg.catalog_file.parent / entry["signing_key_file"]
    fingerprint = entry["signing_key_fingerprint"].replace(" ", "").upper()
    with tempfile.TemporaryDirectory() as home:
        env = {**os.environ, "GNUPGHOME": home}
        subprocess.run(["gpg", "--batch", "-q", "--import", str(key)], env=env, check=True, capture_output=True)
        (Path(home) / "data").write_bytes(data)
        (Path(home) / "sig").write_bytes(sig)
        result = subprocess.run(["gpg", "--batch", "--status-fd", "1", "--verify", f"{home}/sig", f"{home}/data"],
                                env=env, capture_output=True, text=True)
        if f"VALIDSIG {fingerprint}" not in result.stdout:
            raise ValorError("bad_signature", f"the checksum list for {entry['title']} is not signed by {fingerprint}",
                             hint="The mirror may be compromised or the key rotated; check the publisher's site.")


def resolve(cfg, entry_id: str) -> dict:
    """Catalog entry -> {url, filename, checksum, algorithm, verified}. Follows point releases via the checksum list."""
    catalog = load_catalog(cfg)
    if entry_id not in catalog:
        raise ValorError("unknown_iso", f"no catalog entry {entry_id}", hint=f"Known: {', '.join(catalog)}")
    entry = catalog[entry_id]

    # Fixed file (e.g. Windows evaluation media): a pinned checksum when the catalog has one, else https only.
    if "checksums_url" not in entry:
        pinned = entry.get("sha256")
        return {"id": entry_id, "url": entry["url"], "filename": entry["filename"], "checksum": pinned,
                "algorithm": "sha256" if pinned else None,
                "verified": "pinned checksum" if pinned else "https only", "note": entry.get("note", "")}

    # Versioned file (e.g. Rocky-10.1-...iso): the newest name in the publisher's (signed) checksum list.
    text = _fetch(entry["checksums_url"])
    verified = "checksum (https)"
    if entry.get("checksums_sig_url"):
        _verify_signature(cfg, entry, text, _fetch(entry["checksums_sig_url"]))
        verified = "signed checksum list"
    sums = parse_checksums(text.decode(errors="replace"))
    names = sorted((name for name in sums if re.fullmatch(entry["pattern"], name)), key=_natural)
    if not names:
        raise ValorError("unknown_iso", f"no file matching {entry['pattern']} in {entry['checksums_url']}")
    newest = names[-1]
    return {"id": entry_id, "url": entry["base_url"] + newest, "filename": newest, "checksum": sums[newest],
            "algorithm": entry.get("algorithm", "sha256"), "verified": verified}


def installer_iso(pve, iso_id: str) -> dict | None:
    """The newest library ISO for an ISO catalog entry (versioned names: matched by its pattern)."""
    entry = load_catalog(pve.cfg).get(iso_id)
    if not entry:
        return None

    def matches(iso: dict) -> bool:
        if entry.get("pattern"):
            return bool(re.fullmatch(entry["pattern"], iso["name"]))
        return iso["name"] == entry.get("filename")

    hits = [iso for iso in list_isos(pve) if matches(iso)]
    return max(hits, key=lambda iso: _natural(iso["name"])) if hits else None


def find(pve, name: str) -> str | None:
    """volid of the library ISO with this file name (first ISO storage that has it)."""
    return next((iso["volid"] for iso in list_isos(pve) if iso["name"] == name), None)


def list_isos(pve) -> list[dict]:
    """Every ISO on the storages VALOR may read; only the ones on iso_storage can be deleted ("writable").
    A storage the token can't read is skipped rather than failing the whole list."""
    storages = pve.cfg.iso_storages or ((pve.cfg.iso_storage,) if pve.cfg.iso_storage else ())
    out = []
    for storage in storages:
        try:
            items = pve.storage_content(storage, "iso")
        except ValorError:
            continue
        for item in items:
            volid = item["volid"]
            out.append({"volid": volid, "storage": storage, "name": volid.split("/", 1)[-1],
                        "size": item.get("size", 0), "ctime": item.get("ctime", 0),
                        "writable": storage == pve.cfg.iso_storage})
    return sorted(out, key=lambda iso: iso["name"].lower())


def check_filename(name: str) -> str:
    if not FILENAME.match(name or ""):
        raise ValorError("invalid_filename", "ISO file names: letters, digits, . _ + = - and ending in .iso")
    return name


def download(pve, url: str, filename: str, checksum: str | None = None, algorithm: str | None = None) -> str:
    if not pve.cfg.iso_storage:
        raise ValorError("no_iso_storage", "no ISO storage configured", hint="Re-run the installer (--upgrade).")
    if not url.startswith("https://"):
        raise ValorError("insecure_url", "downloads must use https://")
    if checksum:
        algorithm = check_checksum(checksum, algorithm)
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
        head = b"".join(f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
                        for key, value in fields.items())
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
        for part in self._parts:
            part.close()
        self._parts = []
        super().close()
