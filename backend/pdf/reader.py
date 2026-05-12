"""
PdfReader — wraps raw PDF bytes and provides positional access.
Port of the C++ PdfReader (memory-map replaced by plain bytes).
"""
from __future__ import annotations


class PdfReader:
    __slots__ = ("data", "size")

    def __init__(self, data: bytes) -> None:
        self.data: bytes = data
        self.size: int = len(data)

    def is_open(self) -> bool:
        return self.size > 0

    def ptr(self, offset: int) -> int | None:
        """Return offset if valid, else None."""
        if 0 <= offset < self.size:
            return offset
        return None

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or offset >= self.size:
            return b""
        end = min(offset + length, self.size)
        return self.data[offset:end]

    def byte_at(self, offset: int) -> int | None:
        if 0 <= offset < self.size:
            return self.data[offset]
        return None

    def scan_backward(
        self, from_pos: int, keyword: bytes, search_len: int = 2048
    ) -> int:
        """Scan backwards from *from_pos* for *keyword*.
        Returns the offset of the first byte of the keyword, or -1.
        """
        start = max(0, from_pos - search_len)
        chunk = self.data[start:from_pos]
        idx = chunk.rfind(keyword)
        if idx < 0:
            return -1
        return start + idx
