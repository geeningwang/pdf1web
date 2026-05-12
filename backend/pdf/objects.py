"""
PdfObject — the in-memory PDF value model.
Port of the C++ PdfObject / PdfRef structs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class PdfObjType(Enum):
    Null = auto()
    Boolean = auto()
    Integer = auto()
    Real = auto()
    LiteralString = auto()
    HexString = auto()
    Name = auto()
    Array = auto()
    Dictionary = auto()
    Stream = auto()
    Reference = auto()


@dataclass
class PdfRef:
    num: int = 0
    gen: int = 0


@dataclass
class PdfObject:
    type: PdfObjType = PdfObjType.Null

    # scalars
    bval: bool = False
    ival: int = 0
    dval: float = 0.0
    sval: str = ""  # String, HexString, Name

    # composite
    arr: list["PdfObject"] = field(default_factory=list)
    dict: dict[str, "PdfObject"] = field(default_factory=dict)

    # stream (dict is already in .dict when type==Stream)
    stream_raw: bytes = b""
    stream_decoded: bytes = b""
    stream_offset: int = -1

    # reference
    ref: PdfRef = field(default_factory=PdfRef)

    # ------------------------------------------------------------------
    def is_null(self) -> bool:
        return self.type == PdfObjType.Null

    def is_ref(self) -> bool:
        return self.type == PdfObjType.Reference

    def is_dict(self) -> bool:
        return self.type in (PdfObjType.Dictionary, PdfObjType.Stream)

    def is_array(self) -> bool:
        return self.type == PdfObjType.Array

    def is_int(self) -> bool:
        return self.type == PdfObjType.Integer

    def is_name(self) -> bool:
        return self.type == PdfObjType.Name

    def is_str(self) -> bool:
        return self.type in (PdfObjType.LiteralString, PdfObjType.HexString)

    def get(self, key: str) -> "PdfObject":
        """Safe dict lookup — returns Null object if key is missing."""
        return self.dict.get(key, _NULL_OBJ)


_NULL_OBJ = PdfObject(type=PdfObjType.Null)
