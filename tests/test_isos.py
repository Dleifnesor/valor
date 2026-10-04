# *tests the disc library* OwO tests/test_isos.py: tests for the ISO LIBRARY (summary in the docstring)! ^w^
"""ISO library: checksum lists, newest point release, signatures, file names, streamed multipart bodies."""

# hashlib/io for the multipart test; the isos module and ValorError UwU
import hashlib
import io

import pytest

from valor import isos
from valor.errors import ValorError


# Both checksum-list styles parse (GNU "hash  file" and BSD "SHA256 (file) = hash"), with comments ignored >w<
def test_parse_gnu_and_bsd_checksum_lists():
    gnu = "abc123" * 10 + "abcd  debian-13.7.0-amd64-netinst.iso\n" + "ff" * 32 + " *ubuntu-24.04.5-live-server-amd64.iso\n"
    bsd = "# comment\nSHA256 (Rocky-10.2-x86_64-minimal.iso) = " + "aa" * 32 + "\n"
    sums = {**isos.parse_checksums(gnu), **isos.parse_checksums(bsd)}
    assert sums["ubuntu-24.04.5-live-server-amd64.iso"] == "ff" * 32
    assert sums["Rocky-10.2-x86_64-minimal.iso"] == "aa" * 32
    assert "debian-13.7.0-amd64-netinst.iso" in sums


# resolve() picks the newest point release by NUMBER (.10 > .9 > .3), checks the signature with the pinned
# fingerprint, and handles fixed-URL entries and unknown ids (network calls are faked) OwO
def test_resolve_picks_newest_point_release_and_checks_signature(cfg, monkeypatch):
    listing = "\n".join(f"{c * 64}  ubuntu-24.04{suffix}-live-server-amd64.iso" for c, suffix in
                        (("a", ".3"), ("b", ".10"), ("c", ".9"), ("d", "")))
    monkeypatch.setattr(isos, "_fetch", lambda url: listing.encode() if url.endswith("SUMS") else b"sig")
    checked = []
    monkeypatch.setattr(isos, "_verify_signature", lambda cfg, e, data, sig: checked.append(e["signing_key_fingerprint"]))
    r = isos.resolve(cfg, "ubuntu-24.04-server")
    assert r["filename"] == "ubuntu-24.04.10-live-server-amd64.iso" and r["checksum"] == "b" * 64     # 10 > 9 > 3
    assert r["url"].startswith("https://releases.ubuntu.com/") and r["verified"] == "signed checksum list"
    assert checked == ["843938DF228D22F7B3742BC0D94AA3F0EFE21092"]
    w = isos.resolve(cfg, "windows-server-2022-eval")
    assert w["checksum"] is None and w["verified"] == "https only" and w["filename"].endswith(".iso")
    with pytest.raises(ValorError):
        isos.resolve(cfg, "nope")


# A bogus signature (checked with real gpg) stops the download ^w^
def test_bad_signature_stops_the_download(cfg, monkeypatch, tmp_path):
    # real gpg, but a signature that can't be valid: must refuse
    monkeypatch.setattr(isos, "_fetch", lambda url: b"bogus")
    with pytest.raises(ValorError) as e:
        isos.resolve(cfg, "debian-13-netinst")
    assert e.value.code in ("bad_signature",)


# File names: normal ones pass; path tricks, spaces and non-.iso names fail UwU
@pytest.mark.parametrize("name,ok", [("ubuntu-24.04.5-live-server-amd64.iso", True), ("../etc/passwd.iso", False),
                                     ("evil name.iso", False), ("x.img", False), ("win+eval=1.iso", True)])
def test_filenames(name, ok):
    if ok:
        assert isos.check_filename(name) == name
    else:
        with pytest.raises(ValorError):
            isos.check_filename(name)


# Downloads need https and a known checksum algorithm >w<
def test_download_requires_https_and_known_algorithm(cfg):
    class P:
        def __init__(self):
            self.cfg = type("C", (), {"iso_storage": "iso-store"})()
    with pytest.raises(ValorError):
        isos.download(P(), "http://example.com/a.iso", "a.iso")
    with pytest.raises(ValorError):
        isos.download(P(), "https://example.com/a.iso", "a.iso", "ab", "crc32")


# The streamed multipart body: its length is known up front (so no chunked upload), and the file part inside is
# byte-for-byte the original file OwO
def test_multipart_streams_exactly(tmp_path):
    data = bytes(range(256)) * 4000
    f = tmp_path / "x.iso"
    f.write_bytes(data)
    body = isos.MultipartFile({"content": "iso", "checksum": "abc"}, "filename", "x.iso", str(f))
    from requests.utils import super_len
    assert super_len(body) == len(body) > len(data)          # requests sends Content-Length, not chunked
    out = io.BytesIO()
    while chunk := body.read(7777):
        out.write(chunk)
    raw = out.getvalue()
    assert len(raw) == len(body) and raw.endswith(f"--{body.boundary}--\r\n".encode())
    assert hashlib.sha256(raw[raw.index(b"\r\n\r\n", raw.index(b'name="filename"')) + 4:-len(f"\r\n--{body.boundary}--\r\n")]).digest() \
        == hashlib.sha256(data).digest()
    assert b'name="content"\r\n\r\niso\r\n' in raw
