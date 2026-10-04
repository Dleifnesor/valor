"""A minimal ISO 9660 writer for small data CDs (one directory level, a few small files).

VALOR builds its kickstart CD inside the hardened worker, whose seccomp filter rightly stops tools like genisoimage;
this pure-Python writer needs no external program. Files appear in upper case 8.3 form ("KS.CFG;1"); Linux (and
Anaconda) see them as lower case without the version ("ks.cfg").
"""

from __future__ import annotations

import re
import struct
import time

SECTOR = 2048


def _both16(n: int) -> bytes:
    return struct.pack("<H", n) + struct.pack(">H", n)


def _both32(n: int) -> bytes:
    return struct.pack("<I", n) + struct.pack(">I", n)


def _dir_date(t: time.struct_time) -> bytes:
    return bytes([t.tm_year - 1900, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, 0])


def _vol_date(t: time.struct_time) -> bytes:
    return time.strftime("%Y%m%d%H%M%S00", t).encode() + b"\x00"


def _record(ident: bytes, extent: int, size: int, is_dir: bool, t: time.struct_time) -> bytes:
    pad = b"\x00" if len(ident) % 2 == 0 else b""
    length = 33 + len(ident) + len(pad)
    return (bytes([length, 0]) + _both32(extent) + _both32(size) + _dir_date(t) + bytes([2 if is_dir else 0, 0, 0])
            + _both16(1) + bytes([len(ident)]) + ident + pad)


def _name(name: str) -> bytes:
    base, _, ext = name.upper().partition(".")
    if not re.fullmatch(r"[A-Z0-9_]{1,8}", base) or not re.fullmatch(r"[A-Z0-9_]{0,3}", ext):
        raise ValueError(f"{name!r} is not an 8.3 name")
    return f"{base}.{ext};1".encode()


def build(files: dict[str, bytes], label: str) -> bytes:
    """files: {"ks.cfg": data}; label: volume id (e.g. OEMDRV). Returns the ISO image."""
    if not re.fullmatch(r"[A-Z0-9_]{1,32}", label):
        raise ValueError("volume labels are up to 32 of A-Z 0-9 _")
    t = time.gmtime()
    names = sorted(files)
    # layout: 16 system sectors, PVD (16), terminator (17), L path table (18), M path table (19), root dir (20), files
    root_extent, first_file = 20, 21
    extents, nxt = {}, first_file
    for n in names:
        extents[n] = nxt
        nxt += max(1, -(-len(files[n]) // SECTOR))
    total = nxt
    root = _record(b"\x00", root_extent, SECTOR, True, t) + _record(b"\x01", root_extent, SECTOR, True, t)
    for n in names:
        root += _record(_name(n), extents[n], len(files[n]), False, t)
    if len(root) > SECTOR:
        raise ValueError("too many files for one directory sector")
    root = root.ljust(SECTOR, b"\x00")
    path_l = bytes([1, 0]) + struct.pack("<I", root_extent) + struct.pack("<H", 1) + b"\x00\x00"
    path_m = bytes([1, 0]) + struct.pack(">I", root_extent) + struct.pack(">H", 1) + b"\x00\x00"
    pvd = (b"\x01CD001\x01\x00" + b" " * 32 + label.ljust(32).encode() + b"\x00" * 8 + _both32(total)
           + b"\x00" * 32 + _both16(1) + _both16(1) + _both16(SECTOR) + _both32(len(path_l))
           + struct.pack("<I", 18) + struct.pack("<I", 0) + struct.pack(">I", 19) + struct.pack(">I", 0)
           + _record(b"\x00", root_extent, SECTOR, True, t)
           + b" " * 128 + b"VALOR".ljust(128) + b"VALOR".ljust(128) + b" " * 128
           + b" " * 37 + b" " * 37 + b" " * 37
           + _vol_date(t) + _vol_date(t) + b"0" * 16 + b"\x00" + b"0" * 16 + b"\x00" + b"\x01\x00")
    pvd = pvd.ljust(SECTOR, b"\x00")
    term = b"\xffCD001\x01".ljust(SECTOR, b"\x00")
    out = bytearray(b"\x00" * SECTOR * 16)
    out += pvd + term + path_l.ljust(SECTOR, b"\x00") + path_m.ljust(SECTOR, b"\x00") + root
    for n in names:
        data = files[n]
        out += data + b"\x00" * ((-len(data)) % SECTOR or (SECTOR if not data else 0))
    assert len(out) == total * SECTOR, (len(out), total)
    return bytes(out)
