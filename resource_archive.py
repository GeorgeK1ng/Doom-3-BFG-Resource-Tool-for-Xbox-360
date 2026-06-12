"""
resource_archive.py
===================

Reader / writer for DOOM 3 BFG Edition ``.resources`` container files.

Format (verified against id Software's open-source File_Resource.cpp /
File_Resource.h, and against the Xbox 360 ``_common.resources`` archive):

    HEADER (12 bytes)
        magic        uint32   0xD000000D
        tableOffset  int32    absolute offset of the lookup table
        tableLength  int32    byte length of the lookup table

    BODY
        Raw file blobs concatenated.  Each entry's bytes live at
        [offset, offset+length).

    TABLE (at tableOffset)
        numFileResources  int32
        repeated numFileResources times:
            nameLength    int32   length of the filename in bytes
            name          bytes   filename (no terminator stored in count)
            offset        int32   absolute offset of this blob in the body
            length        int32   blob length in bytes

Endianness
----------
The PC build writes every integer big-endian (idFile::WriteBig).  The Xbox 360
build is big-endian for the header ints (magic / tableOffset / tableLength) and
for each entry's *offset* and *length*, but stores the per-name *length prefix*
little-endian.  This module therefore tracks two independent endian settings and
auto-detects them on load, so the same code round-trips either platform's files
byte-for-byte.

This module has no GUI dependency and is unit-testable headless.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from typing import List, Optional


RESOURCE_FILE_MAGIC = 0xD000000D

# Extensions whose contents are plain text in idTech 4 / BFG.
TEXT_EXTENSIONS = {
    "script", "def", "decl", "mtr", "gui", "guied", "sndshd", "skin",
    "fx", "prt", "af", "pda", "cfg", "txt", "lang", "material",
    "particle", "fxe", "ai", "py", "json", "xml", "menu", "rib",
}


def _looks_like_text(data: bytes, sample: int = 2048) -> bool:
    """Heuristic: treat a blob as text if a leading sample is mostly
    printable / whitespace and contains no NUL bytes."""
    if not data:
        return True
    chunk = data[:sample]
    if b"\x00" in chunk:
        return False
    printable = sum(
        1 for b in chunk
        if 9 <= b <= 13 or 32 <= b <= 126 or b >= 160
    )
    return printable / len(chunk) > 0.90


@dataclass
class ResourceEntry:
    """A single file inside the container."""
    name: str
    offset: int
    length: int
    data: Optional[bytes] = field(default=None, repr=False)

    @property
    def ext(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""

    def is_probably_text(self) -> bool:
        if self.ext in TEXT_EXTENSIONS:
            return True
        if self.data is not None:
            return _looks_like_text(self.data)
        return False


class ResourceArchive:
    """Load, edit, and save a DOOM 3 BFG ``.resources`` archive.

    All blob data is held in memory (these archives are typically tens to a
    few hundred MB).  ``save()`` rebuilds the file from scratch, recomputing
    every offset, exactly as the engine's WriteResourceFile does.
    """

    def __init__(self) -> None:
        self.entries: List[ResourceEntry] = []
        # Endian for header ints + per-entry offset/length.
        self.int_endian: str = ">"
        # Endian for the per-name length prefix.
        self.namelen_endian: str = "<"
        self.path: Optional[str] = None

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str) -> "ResourceArchive":
        with open(path, "rb") as fh:
            raw = fh.read()
        arc = cls()
        arc.path = path
        arc._parse(raw)
        return arc

    def _parse(self, raw: bytes) -> None:
        if len(raw) < 12:
            raise ValueError("File too small to be a .resources archive.")

        # Magic appears identical in both endians (0xD000000D is a palindrome
        # byte-wise: D0 00 00 0D), so detect header-int endianness from the
        # plausibility of tableOffset instead.
        magic_be = struct.unpack_from(">I", raw, 0)[0]
        magic_le = struct.unpack_from("<I", raw, 0)[0]
        if magic_be != RESOURCE_FILE_MAGIC and magic_le != RESOURCE_FILE_MAGIC:
            raise ValueError(
                "Not a DOOM 3 BFG .resources file "
                f"(magic = 0x{magic_be:08X})."
            )

        int_endian = self._detect_int_endian(raw)
        self.int_endian = int_endian
        i = struct.Struct(int_endian + "i")

        table_offset = i.unpack_from(raw, 4)[0]
        table_length = i.unpack_from(raw, 8)[0]
        if not (0 < table_offset <= len(raw)):
            raise ValueError("Bad tableOffset; file may be corrupt.")

        # Detect the name-length endianness from the first entry.
        self.namelen_endian = self._detect_namelen_endian(
            raw, table_offset, int_endian
        )

        self.entries = self._read_table(raw, table_offset)
        # Hydrate blob bytes for every entry.
        for e in self.entries:
            e.data = raw[e.offset:e.offset + e.length]

    @staticmethod
    def _detect_int_endian(raw: bytes) -> str:
        """Pick the endianness that yields a sane tableOffset (inside file)."""
        for endian in (">", "<"):
            off = struct.unpack_from(endian + "i", raw, 4)[0]
            if 12 <= off <= len(raw):
                return endian
        return ">"

    def _detect_namelen_endian(
        self, raw: bytes, table_offset: int, int_endian: str
    ) -> str:
        """Read the first name-length both ways; choose the one that produces
        a filename of printable ASCII followed by a sane offset/length."""
        num = struct.unpack_from(int_endian + "i", raw, table_offset)[0]
        if num <= 0:
            return int_endian
        start = table_offset + 4
        for endian in (int_endian, "<", ">"):
            try:
                slen = struct.unpack_from(endian + "i", raw, start)[0]
                if not (0 < slen < 1024):
                    continue
                name = raw[start + 4:start + 4 + slen]
                if all(32 <= b < 127 for b in name):
                    return endian
            except struct.error:
                continue
        return int_endian

    def _read_table(self, raw: bytes, table_offset: int) -> List[ResourceEntry]:
        ints = struct.Struct(self.int_endian + "i")
        nlen = struct.Struct(self.namelen_endian + "i")
        o = table_offset
        num = ints.unpack_from(raw, o)[0]
        o += 4
        out: List[ResourceEntry] = []
        for _ in range(num):
            slen = nlen.unpack_from(raw, o)[0]
            o += 4
            name = raw[o:o + slen].decode("latin1")
            o += slen
            offset = ints.unpack_from(raw, o)[0]
            o += 4
            length = ints.unpack_from(raw, o)[0]
            o += 4
            out.append(ResourceEntry(name=name, offset=offset, length=length))
        return out

    # ------------------------------------------------------------------ edit
    def find(self, name: str) -> Optional[ResourceEntry]:
        name = name.lower()
        for e in self.entries:
            if e.name.lower() == name:
                return e
        return None

    def replace_data(self, name: str, data: bytes) -> None:
        e = self.find(name)
        if e is None:
            raise KeyError(name)
        e.data = data
        e.length = len(data)

    def add_entry(self, name: str, data: bytes) -> ResourceEntry:
        e = ResourceEntry(name=name, offset=0, length=len(data), data=data)
        self.entries.append(e)
        return e

    def remove_entry(self, name: str) -> None:
        e = self.find(name)
        if e is not None:
            self.entries.remove(e)

    # ------------------------------------------------------------------ save
    def to_bytes(self) -> bytes:
        """Serialize the whole archive, recomputing all offsets."""
        ints = struct.Struct(self.int_endian + "i")
        uint = struct.Struct(self.int_endian + "I")
        nlen = struct.Struct(self.namelen_endian + "i")

        buf = io.BytesIO()
        # Reserve header; rewritten at the end.
        buf.write(uint.pack(RESOURCE_FILE_MAGIC))
        buf.write(ints.pack(0))   # tableOffset placeholder
        buf.write(ints.pack(0))   # tableLength placeholder

        # Write blobs, recording fresh offsets.
        for e in self.entries:
            data = e.data if e.data is not None else b""
            e.offset = buf.tell()
            e.length = len(data)
            buf.write(data)

        # Write the table.
        table_offset = buf.tell()
        buf.write(ints.pack(len(self.entries)))
        for e in self.entries:
            name_bytes = e.name.encode("latin1")
            buf.write(nlen.pack(len(name_bytes)))
            buf.write(name_bytes)
            buf.write(ints.pack(e.offset))
            buf.write(ints.pack(e.length))
        table_length = buf.tell() - table_offset

        # Patch the header.
        buf.seek(4)
        buf.write(ints.pack(table_offset))
        buf.write(ints.pack(table_length))
        return buf.getvalue()

    def save(self, path: Optional[str] = None) -> str:
        path = path or self.path
        if path is None:
            raise ValueError("No path given to save().")
        data = self.to_bytes()
        with open(path, "wb") as fh:
            fh.write(data)
        self.path = path
        return path
