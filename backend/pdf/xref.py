"""
PdfXrefTable — parses xref tables / xref streams and builds the object map.
Port of the C++ PdfXrefTable.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from .filters import flat_decode
from .objects import PdfObject, PdfObjType
from .parser import PdfParser
from .tokenizer import PdfTokenizer, TokType

if TYPE_CHECKING:
    from .reader import PdfReader


class XrefEntryType(Enum):
    Free = auto()
    InUse = auto()
    Compressed = auto()


@dataclass
class XrefEntry:
    etype: XrefEntryType = XrefEntryType.Free
    offset: int = 0   # file offset (InUse) or obj-stream obj num (Compressed)
    gen: int = 0
    index_in_stm: int = 0  # index within object stream (Compressed only)


class PdfXrefTable:
    def __init__(self) -> None:
        self._entries: dict[int, XrefEntry] = {}

    @property
    def entries(self) -> dict[int, XrefEntry]:
        return self._entries

    def get_entry(self, obj_num: int) -> XrefEntry | None:
        return self._entries.get(obj_num)

    def _add_entry(self, obj_num: int, e: XrefEntry) -> None:
        # First (newest) entry wins — matches incremental-update semantics
        if obj_num not in self._entries:
            self._entries[obj_num] = e

    # ------------------------------------------------------------------
    def parse(self, reader: "PdfReader", xref_offset: int) -> PdfObject:
        """Parse all xref sections starting at *xref_offset*.
        Returns the (merged) trailer dictionary.
        """
        trailer = PdfObject(type=PdfObjType.Null)
        offset = xref_offset
        safety = 64

        while offset > 0 and safety > 0:
            safety -= 1
            tok = PdfTokenizer(reader)
            tok.seek(offset)
            tok.skip_ws()
            first = tok.next()

            section_trailer: PdfObject
            if first.type == TokType.Xref:
                self._parse_classic_xref(reader, offset)
                section_trailer = self._parse_classic_trailer(reader, offset)
            elif first.type == TokType.Integer:
                section_trailer = self._parse_xref_stream(reader, offset)
            else:
                break

            if trailer.is_null():
                trailer = section_trailer

            prev = section_trailer.get("Prev")
            if prev.is_int() and prev.ival > 0:
                offset = int(prev.ival)
            else:
                break

        return trailer

    # ------------------------------------------------------------------
    def _parse_classic_xref(self, reader: "PdfReader", offset: int) -> None:
        data = reader.data
        size = reader.size
        pos = offset

        # Skip "xref" keyword + line ending
        while pos < size and data[pos] not in (ord("\n"), ord("\r")):
            pos += 1
        while pos < size and data[pos] in (ord("\n"), ord("\r")):
            pos += 1

        while pos < size:
            # Skip spaces/tabs
            while pos < size and data[pos] in (ord(" "), ord("\t")):
                pos += 1

            # Stop at "trailer"
            if pos + 7 <= size and data[pos:pos+7] == b"trailer":
                break
            if data[pos] < ord("0") or data[pos] > ord("9"):
                break

            # Read firstObj
            start = pos
            while pos < size and ord("0") <= data[pos] <= ord("9"):
                pos += 1
            first_obj = int(data[start:pos])

            # Skip whitespace
            while pos < size and data[pos] in (ord(" "), ord("\t")):
                pos += 1

            # Read count
            start = pos
            while pos < size and ord("0") <= data[pos] <= ord("9"):
                pos += 1
            count = int(data[start:pos]) if start < pos else 0

            # Skip to next line
            while pos < size and data[pos] not in (ord("\n"), ord("\r")):
                pos += 1
            while pos < size and data[pos] in (ord("\n"), ord("\r")):
                pos += 1

            # Parse 20-byte entries
            for i in range(count):
                if pos + 20 > size:
                    break
                entry_bytes = data[pos:pos+20]
                pos += 20
                try:
                    entry_off = int(entry_bytes[0:10])
                    entry_gen = int(entry_bytes[11:16])
                    entry_type = chr(entry_bytes[17])
                except (ValueError, IndexError):
                    continue
                xe = XrefEntry(
                    etype=XrefEntryType.InUse if entry_type == "n" else XrefEntryType.Free,
                    offset=entry_off,
                    gen=entry_gen,
                )
                self._add_entry(first_obj + i, xe)

    def _parse_classic_trailer(self, reader: "PdfReader", xref_offset: int) -> PdfObject:
        """Re-scan from *xref_offset* to find and parse the trailer dict."""
        tok = PdfTokenizer(reader)
        tok.seek(xref_offset)
        tok.skip_ws()
        tok.next()  # "xref"

        # Skip subsection lines until we see "trailer"
        while True:
            tok.skip_ws()
            t = tok.next()
            if t.type == TokType.Trailer:
                tok.skip_ws()
                t2 = tok.next()
                if t2.type == TokType.DictBegin:
                    p = PdfParser(reader)
                    p.tok.seek(t2.offset)
                    p.tok.next()  # consume "<<"
                    td = PdfObject(type=PdfObjType.Dictionary)
                    while True:
                        key = p.tok.next()
                        if key.type in (TokType.DictEnd, TokType.Eof):
                            break
                        if key.type != TokType.Name:
                            continue
                        val = p.parse_object()
                        td.dict[key.sval] = val
                    return td
                break
            if t.type in (TokType.Eof,):
                break
            # Skip subsection header: two integers, then 20-byte entries
            if t.type == TokType.Integer:
                cnt_tok = tok.next()
                if cnt_tok.type == TokType.Integer:
                    n = int(cnt_tok.ival)
                    for _ in range(n):
                        tok.next()
                        tok.next()
                        tok.next()
            else:
                break
        return PdfObject(type=PdfObjType.Null)

    # ------------------------------------------------------------------
    def _parse_xref_stream(self, reader: "PdfReader", offset: int) -> PdfObject:
        """Parse an xref stream object (PDF 1.5+)."""
        parser = PdfParser(reader)
        out: list[int] = []
        obj = parser.parse_indirect_object(offset, out)

        if obj.type != PdfObjType.Stream:
            return PdfObject(type=PdfObjType.Null)

        # Decode the stream
        filter_obj = obj.get("Filter")
        decoded: bytes | None = None
        if not filter_obj.is_null():
            fname = ""
            if filter_obj.is_name():
                fname = filter_obj.sval
            elif filter_obj.is_array() and filter_obj.arr and filter_obj.arr[0].is_name():
                fname = filter_obj.arr[0].sval
            if fname == "FlateDecode":
                decoded = flat_decode(obj.stream_raw)

        if decoded is None:
            decoded = obj.stream_raw

        # Parse /W and /Index
        w_obj = obj.get("W")
        idx_obj = obj.get("Index")
        size_obj = obj.get("Size")

        if not w_obj.is_array() or len(w_obj.arr) < 3:
            return obj

        w0 = int(w_obj.arr[0].ival)
        w1 = int(w_obj.arr[1].ival)
        w2 = int(w_obj.arr[2].ival)
        entry_size = w0 + w1 + w2
        if entry_size <= 0:
            return obj

        # Build index pairs
        index_pairs: list[tuple[int, int]] = []
        if not idx_obj.is_null() and idx_obj.is_array() and len(idx_obj.arr) >= 2:
            for i in range(0, len(idx_obj.arr) - 1, 2):
                index_pairs.append(
                    (int(idx_obj.arr[i].ival), int(idx_obj.arr[i + 1].ival))
                )
        else:
            total = int(size_obj.ival) if size_obj.is_int() else 0
            index_pairs.append((0, total))

        def read_int(data: bytes, pos: int, width: int) -> int:
            v = 0
            for k in range(width):
                v = (v << 8) | data[pos + k]
            return v

        data_pos = 0
        for first_obj, count in index_pairs:
            for i in range(count):
                if data_pos + entry_size > len(decoded):
                    break
                ep_start = data_pos
                data_pos += entry_size

                entry_type = read_int(decoded, ep_start, w0) if w0 > 0 else 1
                field1 = read_int(decoded, ep_start + w0, w1)
                field2 = read_int(decoded, ep_start + w0 + w1, w2)

                if entry_type == 0:
                    xe = XrefEntry(etype=XrefEntryType.Free, gen=int(field2))
                elif entry_type == 1:
                    xe = XrefEntry(etype=XrefEntryType.InUse,
                                   offset=int(field1), gen=int(field2))
                elif entry_type == 2:
                    xe = XrefEntry(etype=XrefEntryType.Compressed,
                                   offset=int(field1),  # obj-stream obj num
                                   gen=0,
                                   index_in_stm=int(field2))
                else:
                    continue

                self._add_entry(first_obj + i, xe)

        return obj  # serves as trailer dict too
