# *stacks tiny sectors like building blocks* OwO valor/iso9660.py writes CD images (ISO 9660) all by itself!
# VALOR needs a teeny CD holding ks.cfg (see kickstart.py). The worker that builds it runs in a sandbox that
# blocks tools like genisoimage, so this file builds the CD byte by byte in pure Python ^w^
# It only supports what VALOR needs: one root directory with a few small files with 8.3 names. *wags tail*
# Background: an ISO is made of 2048-byte sectors. Sectors 0-15 are reserved; sector 16 is the "primary volume
# descriptor" (the CD's table of contents), then a terminator, path tables, the root directory and the files >w<
"""A minimal ISO 9660 writer for small data CDs (one directory level, a few small files).

VALOR builds its kickstart CD inside the hardened worker, whose seccomp filter rightly stops tools like genisoimage;
this pure-Python writer needs no external program. Files appear in upper case 8.3 form ("KS.CFG;1"); Linux (and
Anaconda) see them as lower case without the version ("ks.cfg").
"""

# Lazy type hints UwU
from __future__ import annotations

# re checks names; struct packs numbers into bytes; time gives the timestamps written on the CD OwO
import re
import struct
import time

# One CD sector = 2048 bytes. Everything on the disc is measured in these *boops the number*
SECTOR = 2048


# _both16 / _both32: ISO 9660 writes most numbers TWICE, little-endian then big-endian, so both kinds of computer
# can read them without converting ("<H" = little 16-bit, ">H" = big 16-bit, "I" = 32-bit) ^w^
# ISO 9660 stores most numbers twice: little-endian, then big-endian ("both-byte order").
def _both16(value: int) -> bytes:
    return struct.pack("<H", value) + struct.pack(">H", value)


def _both32(value: int) -> bytes:
    return struct.pack("<I", value) + struct.pack(">I", value)


# _dir_date: the 7-byte date in a directory record: years since 1900, month, day, hour, minute, second, and the
# timezone offset (0 = UTC) >w<
def _dir_date(t: time.struct_time) -> bytes:
    return bytes([t.tm_year - 1900, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, 0])


# _vol_date: the 17-byte date in the volume descriptor: "YYYYMMDDHHMMSS" + two hundredths digits as text, then a
# timezone byte (0 = UTC) OwO
def _vol_date(t: time.struct_time) -> bytes:
    return time.strftime("%Y%m%d%H%M%S00", t).encode() + b"\x00"


# _record: one directory record (the entry that says "this name lives at sector X and is N bytes long") UwU
#   ident    the name in bytes (or 0x00 for "." and 0x01 for "..")
#   extent   the first sector of the file/directory; size = its length in bytes
#   is_dir   True for directories; t = the timestamp
def _record(ident: bytes, extent: int, size: int, is_dir: bool, t: time.struct_time) -> bytes:
    """A directory record: where a file (or directory) starts, its size and name. Records have even length."""
    # Records must have an even length, so add one padding byte when the name length is even (33 + even = odd) ^w^
    pad = b"\x00" if len(ident) % 2 == 0 else b""
    # 33 fixed bytes + the name + the padding *counts carefully*
    length = 33 + len(ident) + len(pad)
    # The record layout: total length, extended-attribute length (0), the start sector (both orders), the size (both
    # orders), the date, flags (2 = directory), unit size and gap (0, 0), the volume sequence number (1), the name's
    # length, the name and the padding >w<
    return (bytes([length, 0]) + _both32(extent) + _both32(size) + _dir_date(t) + bytes([2 if is_dir else 0, 0, 0])
            + _both16(1) + bytes([len(ident)]) + ident + pad)


# _name: turn "ks.cfg" into the ISO form "KS.CFG;1" (upper case, 8.3, ";1" = file version 1). Anything that
# doesn't fit 8.3 (up to 8 chars, a dot, up to 3 chars, A-Z 0-9 _) is refused with a ValueError OwO
def _name(name: str) -> bytes:
    base, _, ext = name.upper().partition(".")
    if not re.fullmatch(r"[A-Z0-9_]{1,8}", base) or not re.fullmatch(r"[A-Z0-9_]{0,3}", ext):
        raise ValueError(f"{name!r} is not an 8.3 name")
    return f"{base}.{ext};1".encode()


# build(): make the whole ISO image in memory *rolls up sleeves*
#   files  {"ks.cfg": b"..."}: file name -> its bytes; label = the volume label (e.g. "OEMDRV")
# Returns the image bytes, ready to write to a file ^w^
def build(files: dict[str, bytes], label: str) -> bytes:
    """files: {"ks.cfg": data}; label: volume id (e.g. OEMDRV). Returns the ISO image."""
    # Volume labels may only use A-Z, 0-9 and _, up to 32 chars UwU
    if not re.fullmatch(r"[A-Z0-9_]{1,32}", label):
        raise ValueError("volume labels are up to 32 of A-Z 0-9 _")
    # One timestamp for everything; file names sorted (directories must be sorted by name) >w<
    t = time.gmtime()
    names = sorted(files)
    # layout: 16 system sectors, PVD (16), terminator (17), L path table (18), M path table (19), root dir (20), files
    # Sector numbers: the root directory sits in sector 20 and the first file starts right after, in sector 21 ^w^
    root_extent, first_file = 20, 21
    # Give each file its first sector, then move on by however many sectors it fills >w<
    extents, next_sector = {}, first_file
    for name in names:
        extents[name] = next_sector
        # -(-a // b) is a trick for "a divided by b, rounded UP" (ceil division). Even an empty file takes one sector OwO
        next_sector += max(1, -(-len(files[name]) // SECTOR))       # whole sectors, at least one
    # After the last file = the total number of sectors in the image UwU
    total = next_sector
    # "." and ".." of the root directory, then one record per file
    root = _record(b"\x00", root_extent, SECTOR, True, t) + _record(b"\x01", root_extent, SECTOR, True, t)
    # ...then one record per file, pointing at its sector ^w^
    for name in names:
        root += _record(_name(name), extents[name], len(files[name]), False, t)
    # This tiny writer keeps the whole root directory in ONE sector; refuse more files than fit >w<
    if len(root) > SECTOR:
        raise ValueError("too many files for one directory sector")
    # Pad the root directory out to a full sector with zero bytes OwO
    root = root.ljust(SECTOR, b"\x00")
    # The path tables: a list of directories (here just the root). The "L" table is little-endian and the "M" table
    # is big-endian. Each: name length 1, no extended attributes, the root's sector, parent number 1, and a 0x00 name
    # (plus a padding byte) *wags tail*
    path_l = bytes([1, 0]) + struct.pack("<I", root_extent) + struct.pack("<H", 1) + b"\x00\x00"
    path_m = bytes([1, 0]) + struct.pack(">I", root_extent) + struct.pack(">H", 1) + b"\x00\x00"
    # Primary volume descriptor: label, size, where the path tables and the root directory are.
    # Field by field: type 1 + "CD001" + version 1 + unused, system id (spaces), the volume label padded to 32,
    # unused, the size in sectors, unused, set size, sequence number, sector size, path table size, where the L and M
    # path tables are (sectors 18 and 19), the root directory's own record, volume-set/publisher/preparer/application
    # ids (publisher and preparer say "VALOR"), copyright/abstract/bibliography file ids (blank), the created and
    # modified dates, "never" for expiry and effective dates, and file structure version 1 ^w^ *phew, pants*
    pvd = (b"\x01CD001\x01\x00" + b" " * 32 + label.ljust(32).encode() + b"\x00" * 8 + _both32(total)
           + b"\x00" * 32 + _both16(1) + _both16(1) + _both16(SECTOR) + _both32(len(path_l))
           + struct.pack("<I", 18) + struct.pack("<I", 0) + struct.pack(">I", 19) + struct.pack(">I", 0)
           + _record(b"\x00", root_extent, SECTOR, True, t)
           + b" " * 128 + b"VALOR".ljust(128) + b"VALOR".ljust(128) + b" " * 128
           + b" " * 37 + b" " * 37 + b" " * 37
           + _vol_date(t) + _vol_date(t) + b"0" * 16 + b"\x00" + b"0" * 16 + b"\x00" + b"\x01\x00")
    # Pad the descriptor to a full sector; the terminator (type 255) marks the end of the descriptor list UwU
    pvd = pvd.ljust(SECTOR, b"\x00")
    term = b"\xffCD001\x01".ljust(SECTOR, b"\x00")
    # Assemble: 16 empty system sectors, then the descriptor, the terminator, both path tables and the root dir >w<
    out = bytearray(b"\x00" * SECTOR * 16)
    out += pvd + term + path_l.ljust(SECTOR, b"\x00") + path_m.ljust(SECTOR, b"\x00") + root
    # Then each file's bytes, padded with zeros to whole sectors (an empty file gets one full sector of zeros, so
    # it matches the sector count planned above) OwO
    for name in names:
        data = files[name]
        out += data + b"\x00" * ((-len(data)) % SECTOR or (SECTOR if not data else 0))
    # Sanity check: the image must be exactly the size we planned. If not, there's a bug, so fail loudly ^w^
    assert len(out) == total * SECTOR, (len(out), total)
    return bytes(out)
