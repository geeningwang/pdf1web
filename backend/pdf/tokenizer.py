"""
PdfTokenizer — converts raw PDF bytes into a stream of tokens.
Faithful port of the C++ PdfTokenizer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .reader import PdfReader


class TokType(Enum):
    Boolean = auto()
    Integer = auto()
    Real = auto()
    LiteralString = auto()
    HexString = auto()
    Name = auto()
    Null = auto()
    ArrayBegin = auto()
    ArrayEnd = auto()
    DictBegin = auto()
    DictEnd = auto()
    Stream = auto()
    EndStream = auto()
    Obj = auto()
    EndObj = auto()
    R = auto()
    Xref = auto()
    Trailer = auto()
    StartXref = auto()
    Eof = auto()
    Unknown = auto()


@dataclass
class Token:
    type: TokType = TokType.Unknown
    sval: str = ""
    ival: int = 0
    dval: float = 0.0
    offset: int = -1


_WHITESPACE = frozenset(b"\x00\x09\x0a\x0c\x0d\x20")
_DELIMITER = frozenset(b"()<>[]{}/%")

_KEYWORDS: dict[str, TokType] = {
    "true": TokType.Boolean,
    "false": TokType.Boolean,
    "null": TokType.Null,
    "obj": TokType.Obj,
    "endobj": TokType.EndObj,
    "R": TokType.R,
    "stream": TokType.Stream,
    "endstream": TokType.EndStream,
    "xref": TokType.Xref,
    "trailer": TokType.Trailer,
    "startxref": TokType.StartXref,
}


class PdfTokenizer:
    def __init__(self, reader: "PdfReader") -> None:
        self._reader = reader
        self.pos: int = 0

    def seek(self, offset: int) -> None:
        self.pos = offset

    def tell(self) -> int:
        return self.pos

    # ------------------------------------------------------------------
    def _peek_byte(self) -> int | None:
        b = self._reader.byte_at(self.pos)
        return b

    def _read_byte(self) -> int | None:
        b = self._reader.byte_at(self.pos)
        if b is not None:
            self.pos += 1
        return b

    def _unread_byte(self) -> None:
        if self.pos > 0:
            self.pos -= 1

    # ------------------------------------------------------------------
    def skip_ws(self) -> int:
        while True:
            b = self._peek_byte()
            if b is None:
                break
            if b == ord("%"):
                # comment — skip to end of line
                while True:
                    b2 = self._peek_byte()
                    if b2 is None or b2 == ord("\n") or b2 == ord("\r"):
                        break
                    self._read_byte()
            elif b in _WHITESPACE:
                self._read_byte()
            else:
                break
        return self.pos

    def peek(self) -> Token:
        saved = self.pos
        tok = self.next()
        self.pos = saved
        return tok

    # ------------------------------------------------------------------
    def next(self) -> Token:
        self.skip_ws()
        tok_start = self.pos
        b = self._read_byte()
        if b is None:
            return Token(TokType.Eof, offset=tok_start)

        # Name
        if b == ord("/"):
            t = self._read_name()
            t.offset = tok_start
            return t

        # Literal string
        if b == ord("("):
            t = self._read_literal_string()
            t.offset = tok_start
            return t

        # Hex string or dict delimiter
        if b == ord("<"):
            b2 = self._peek_byte()
            if b2 == ord("<"):
                self._read_byte()
                return Token(TokType.DictBegin, "<<", offset=tok_start)
            t = self._read_hex_string()
            t.offset = tok_start
            return t

        if b == ord(">"):
            b2 = self._peek_byte()
            if b2 == ord(">"):
                self._read_byte()
                return Token(TokType.DictEnd, ">>", offset=tok_start)
            return Token(TokType.Unknown, ">", offset=tok_start)

        if b == ord("["):
            return Token(TokType.ArrayBegin, "[", offset=tok_start)
        if b == ord("]"):
            return Token(TokType.ArrayEnd, "]", offset=tok_start)

        # Number
        if b in (ord("+"), ord("-"), ord(".")) or chr(b).isdigit():
            t = self._read_number(b)
            t.offset = tok_start
            return t

        # Keyword / boolean / null
        t = self._read_keyword(b)
        t.offset = tok_start
        return t

    # ------------------------------------------------------------------
    def _read_name(self) -> Token:
        parts: list[str] = []
        while True:
            b = self._peek_byte()
            if b is None or b in _WHITESPACE or b in _DELIMITER:
                break
            self._read_byte()
            if b == ord("#"):
                # hex escape
                h1 = self._read_byte()
                h2 = self._read_byte()
                h1 = h1 if h1 is not None else ord("0")
                h2 = h2 if h2 is not None else ord("0")
                val = (_hex_val(h1) << 4) | _hex_val(h2)
                parts.append(chr(val))
            else:
                parts.append(chr(b))
        return Token(TokType.Name, "".join(parts))

    def _read_literal_string(self) -> Token:
        parts: list[int] = []
        depth = 1
        while depth > 0:
            b = self._read_byte()
            if b is None:
                break
            if b == ord("\\"):
                esc = self._read_byte()
                if esc is None:
                    break
                ESC_MAP = {
                    ord("n"): ord("\n"), ord("r"): ord("\r"),
                    ord("t"): ord("\t"), ord("b"): ord("\b"),
                    ord("f"): ord("\f"), ord("("): ord("("),
                    ord(")"): ord(")"), ord("\\"): ord("\\"),
                }
                if esc in ESC_MAP:
                    parts.append(ESC_MAP[esc])
                elif esc == ord("\r"):
                    nx = self._peek_byte()
                    if nx == ord("\n"):
                        self._read_byte()
                elif esc == ord("\n"):
                    pass  # line continuation — discard
                elif ord("0") <= esc <= ord("7"):
                    v = esc - ord("0")
                    for _ in range(2):
                        o = self._peek_byte()
                        if o is not None and ord("0") <= o <= ord("7"):
                            self._read_byte()
                            v = v * 8 + (o - ord("0"))
                        else:
                            break
                    parts.append(v & 0xFF)
                else:
                    parts.append(esc)
            elif b == ord("("):
                depth += 1
                parts.append(b)
            elif b == ord(")"):
                depth -= 1
                if depth > 0:
                    parts.append(b)
            else:
                parts.append(b)
        return Token(TokType.LiteralString, bytes(parts).decode("latin-1"))

    def _read_hex_string(self) -> Token:
        parts: list[int] = []
        while True:
            b = self._peek_byte()
            if b is None or b == ord(">"):
                break
            self._read_byte()
            if b in _WHITESPACE:
                continue
            hi = _hex_val(b)
            b2 = self._peek_byte()
            lo = 0
            if b2 is not None and b2 != ord(">"):
                v = _hex_val(b2)
                if v >= 0:
                    self._read_byte()
                    lo = v
            if hi >= 0:
                parts.append((hi << 4) | lo)
        b = self._peek_byte()
        if b == ord(">"):
            self._read_byte()
        return Token(TokType.HexString, bytes(parts).decode("latin-1"))

    def _read_number(self, first: int) -> Token:
        buf = [chr(first)]
        is_real = first == ord(".")
        while True:
            b = self._peek_byte()
            if b is None:
                break
            if b == ord("."):
                is_real = True
                self._read_byte()
                buf.append(".")
            elif chr(b).isdigit():
                self._read_byte()
                buf.append(chr(b))
            else:
                break
        s = "".join(buf)
        if is_real:
            return Token(TokType.Real, s, dval=float(s) if s not in (".", "+", "-") else 0.0)
        return Token(TokType.Integer, s, ival=int(s) if s not in ("+", "-") else 0)

    def _read_keyword(self, first: int) -> Token:
        buf = [chr(first)]
        while True:
            b = self._peek_byte()
            if b is None or b in _WHITESPACE or b in _DELIMITER:
                break
            self._read_byte()
            buf.append(chr(b))
        s = "".join(buf)
        ktype = _KEYWORDS.get(s, TokType.Unknown)
        tok = Token(ktype, s)
        if ktype == TokType.Boolean:
            tok.ival = 1 if s == "true" else 0
        return tok


def _hex_val(c: int) -> int:
    if ord("0") <= c <= ord("9"):
        return c - ord("0")
    if ord("a") <= c <= ord("f"):
        return c - ord("a") + 10
    if ord("A") <= c <= ord("F"):
        return c - ord("A") + 10
    return -1
