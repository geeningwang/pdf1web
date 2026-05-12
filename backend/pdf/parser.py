"""
PdfParser — builds PdfObject values from token sequences.
Port of the C++ PdfParser.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .objects import PdfObject, PdfObjType, PdfRef
from .tokenizer import PdfTokenizer, TokType

if TYPE_CHECKING:
    from .reader import PdfReader


class PdfParser:
    def __init__(self, reader: "PdfReader") -> None:
        self._reader = reader
        self.tok = PdfTokenizer(reader)

    # ------------------------------------------------------------------
    def parse_object(self) -> PdfObject:
        t = self.tok.next()

        if t.type == TokType.Null:
            return PdfObject(type=PdfObjType.Null)

        if t.type == TokType.Boolean:
            return PdfObject(type=PdfObjType.Boolean, bval=(t.ival != 0))

        if t.type == TokType.Integer:
            # Could be start of "N G R" reference — look ahead
            n = t.ival
            saved = self.tok.tell()
            t2 = self.tok.next()
            if t2.type == TokType.Integer:
                t3 = self.tok.next()
                if t3.type == TokType.R:
                    return PdfObject(
                        type=PdfObjType.Reference,
                        ref=PdfRef(num=int(n), gen=int(t2.ival)),
                    )
                # put back t3 and t2
                self.tok.seek(t2.offset)
            else:
                self.tok.seek(t2.offset)
            return PdfObject(type=PdfObjType.Integer, ival=int(n))

        if t.type == TokType.Real:
            return PdfObject(type=PdfObjType.Real, dval=t.dval)

        if t.type == TokType.LiteralString:
            return PdfObject(type=PdfObjType.LiteralString, sval=t.sval)

        if t.type == TokType.HexString:
            return PdfObject(type=PdfObjType.HexString, sval=t.sval)

        if t.type == TokType.Name:
            return PdfObject(type=PdfObjType.Name, sval=t.sval)

        if t.type == TokType.DictBegin:
            return self._parse_dict_or_stream()

        if t.type == TokType.ArrayBegin:
            return self._parse_array()

        return PdfObject(type=PdfObjType.Null)

    # ------------------------------------------------------------------
    def _parse_dict_or_stream(self) -> PdfObject:
        """Called after '<<' has been consumed."""
        obj = PdfObject(type=PdfObjType.Dictionary)
        while True:
            key = self.tok.next()
            if key.type in (TokType.DictEnd, TokType.Eof):
                break
            if key.type != TokType.Name:
                continue  # malformed — skip
            val = self.parse_object()
            obj.dict[key.sval] = val
        return self._maybe_read_stream(obj)

    def _maybe_read_stream(self, dict_obj: PdfObject) -> PdfObject:
        self.tok.skip_ws()
        saved = self.tok.tell()
        t = self.tok.next()
        if t.type != TokType.Stream:
            self.tok.seek(saved)
            return dict_obj

        # Consume mandatory line-ending after "stream"
        data_start = self.tok.tell()
        data = self._reader.data
        if data_start < len(data) and data[data_start] == ord("\r"):
            data_start += 1
        if data_start < len(data) and data[data_start] == ord("\n"):
            data_start += 1
        self.tok.seek(data_start)

        # Determine stream length
        length = 0
        length_obj = dict_obj.get("Length")
        if length_obj.is_int():
            length = int(length_obj.ival)

        stream_obj = PdfObject(
            type=PdfObjType.Stream,
            dict=dict_obj.dict,
            stream_offset=data_start,
        )
        if length > 0:
            stream_obj.stream_raw = self._reader.read(data_start, length)
            self.tok.seek(data_start + length)
            self.tok.skip_ws()
            self.tok.next()  # consume "endstream"

        return stream_obj

    def _parse_array(self) -> PdfObject:
        """Called after '[' has been consumed."""
        obj = PdfObject(type=PdfObjType.Array)
        while True:
            self.tok.skip_ws()
            t = self.tok.peek()
            if t.type in (TokType.ArrayEnd, TokType.Eof):
                self.tok.next()  # consume ']'
                break
            obj.arr.append(self.parse_object())
        return obj

    # ------------------------------------------------------------------
    def parse_indirect_object(
        self, offset: int, out: list[int]
    ) -> PdfObject:
        """Parse 'N G obj <value> endobj' at *offset*.
        *out* will be set to [num, gen].
        """
        self.tok.seek(offset)
        t_num = self.tok.next()
        t_gen = self.tok.next()
        self.tok.next()  # consume "obj"
        num = int(t_num.ival) if t_num.type == TokType.Integer else -1
        gen = int(t_gen.ival) if t_gen.type == TokType.Integer else 0
        out.clear()
        out.extend([num, gen])
        return self.parse_object()

    def parse_trailer(self, offset: int) -> PdfObject:
        """Parse trailer dictionary starting at *offset*."""
        self.tok.seek(offset)
        t = self.tok.next()  # should be "trailer"
        if t.type != TokType.Trailer:
            self.tok.seek(offset)
        t2 = self.tok.next()
        if t2.type == TokType.DictBegin:
            return self._parse_dict_or_stream()
        return PdfObject(type=PdfObjType.Null)
