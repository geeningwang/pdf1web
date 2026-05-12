"""
PdfDocument — top-level model: loads a PDF, resolves objects, builds the node tree.
Port of the C++ PdfDocument / PdfNode.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from .filters import flat_decode, ascii_hex_decode, ascii85_decode
from .objects import PdfObject, PdfObjType, PdfRef
from .parser import PdfParser
from .reader import PdfReader
from .xref import PdfXrefTable, XrefEntry, XrefEntryType

# Maximum recursion depth when building the tree (mirrors kMaxDepth=8 in C++)
_MAX_DEPTH = 8


# -----------------------------------------------------------------------
# PdfNode
# -----------------------------------------------------------------------
@dataclass
class PdfNode:
    label: str = ""
    detail: str = ""
    obj_num: int = -1
    gen_num: int = 0
    is_image: bool = False
    children: list["PdfNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        # Cap detail text in the tree payload — full detail is fetched lazily
        # via /api/object/... when the user selects a node.
        detail = self.detail[:4096] if len(self.detail) > 4096 else self.detail
        return {
            "label": self.label,
            "detail": detail,
            "obj_num": self.obj_num,
            "gen_num": self.gen_num,
            "is_image": self.is_image,
            "children": [c.to_dict() for c in self.children],
        }


# -----------------------------------------------------------------------
# Formatting helpers (mirror C++ BriefValue / DetailText)
# -----------------------------------------------------------------------
def _brief(obj: PdfObject) -> str:
    t = obj.type
    if t == PdfObjType.Null:
        return "null"
    if t == PdfObjType.Boolean:
        return "true" if obj.bval else "false"
    if t == PdfObjType.Integer:
        return str(obj.ival)
    if t == PdfObjType.Real:
        return repr(obj.dval)
    if t == PdfObjType.LiteralString:
        safe = obj.sval[:80].replace("\r", "\\r").replace("\n", "\\n")
        return f"({safe})"
    if t == PdfObjType.HexString:
        hex_str = obj.sval.encode("latin-1").hex().upper()
        return f"<{hex_str[:40]}>"
    if t == PdfObjType.Name:
        return f"/{obj.sval}"
    if t == PdfObjType.Reference:
        return f"{obj.ref.num} {obj.ref.gen} R"
    if t == PdfObjType.Array:
        return f"[ {len(obj.arr)} items ]"
    if t == PdfObjType.Dictionary:
        return f"<< {len(obj.dict)} entries >>"
    if t == PdfObjType.Stream:
        return f"<< stream, length={len(obj.stream_raw)} >>"
    return ""


def _detail(obj: PdfObject, indent: int = 0) -> str:
    pad = "  " * indent
    t = obj.type
    if t == PdfObjType.Null:
        return pad + "null"
    if t == PdfObjType.Boolean:
        return pad + ("true" if obj.bval else "false")
    if t == PdfObjType.Integer:
        return pad + str(obj.ival)
    if t == PdfObjType.Real:
        return pad + repr(obj.dval)
    if t == PdfObjType.LiteralString:
        return pad + f"({obj.sval})"
    if t == PdfObjType.HexString:
        hex_str = obj.sval.encode("latin-1").hex().upper()
        return pad + f"<{hex_str}>"
    if t == PdfObjType.Name:
        return pad + f"/{obj.sval}"
    if t == PdfObjType.Reference:
        return pad + f"{obj.ref.num} {obj.ref.gen} R"
    if t == PdfObjType.Array:
        lines = [pad + "["]
        for item in obj.arr:
            lines.append(_detail(item, indent + 1))
        lines.append(pad + "]")
        return "\n".join(lines)
    if t in (PdfObjType.Dictionary, PdfObjType.Stream):
        lines = [pad + "<<"]
        for k, v in obj.dict.items():
            lines.append(pad + f"  /{k}  {_brief(v)}")
        lines.append(pad + ">>")
        if t == PdfObjType.Stream:
            lines.append("stream")
            lines.append(f"[{len(obj.stream_raw)} bytes raw]")
            if obj.stream_decoded:
                show = min(len(obj.stream_decoded), 4096)
                lines.append(f"[{len(obj.stream_decoded)} bytes decoded]")
                lines.append(
                    f"--- decoded content (first {show} bytes) ---"
                )
                text = _bytes_to_printable(obj.stream_decoded[:show])
                lines.append(text)
            else:
                show = min(len(obj.stream_raw), 512)
                lines.append(f"--- raw hex (first {show} bytes) ---")
                lines.append(_hex_dump(obj.stream_raw[:show]))
            lines.append("endstream")
        return "\n".join(lines)
    return pad + "?"


def _bytes_to_printable(data: bytes) -> str:
    out: list[str] = []
    for b in data:
        if b == ord("\n"):
            out.append("\n")
        elif b == ord("\r"):
            pass
        elif b >= 32 or b == ord("\t"):
            out.append(chr(b))
        else:
            out.append(".")
    return "".join(out)


def _hex_dump(data: bytes) -> str:
    lines: list[str] = []
    for i in range(0, len(data), 16):
        row = data[i:i+16]
        hex_part = " ".join(f"{b:02X}" for b in row)
        lines.append(hex_part)
    return "\n".join(lines)


# -----------------------------------------------------------------------
# Stream decoder
# -----------------------------------------------------------------------
def _decode_stream(obj: PdfObject) -> bytes | None:
    if obj.type != PdfObjType.Stream:
        return None
    if not obj.stream_raw:
        return b""

    filter_names: list[str] = []
    f_obj = obj.get("Filter")
    if f_obj.is_name():
        filter_names.append(f_obj.sval)
    elif f_obj.is_array():
        for f in f_obj.arr:
            if f.is_name():
                filter_names.append(f.sval)

    if not filter_names:
        return obj.stream_raw

    data = obj.stream_raw
    for name in filter_names:
        decoded: bytes | None = None
        if name == "FlateDecode":
            decoded = flat_decode(data)
        elif name == "ASCIIHexDecode":
            decoded = ascii_hex_decode(data)
        elif name == "ASCII85Decode":
            decoded = ascii85_decode(data)
        else:
            decoded = None  # unsupported filter — leave as-is

        if decoded is not None:
            data = decoded
        else:
            return None  # decode failed

    return data


# -----------------------------------------------------------------------
# PdfDocument
# -----------------------------------------------------------------------
class PdfDocument:
    def __init__(self) -> None:
        self._reader: PdfReader | None = None
        self._xref = PdfXrefTable()
        self._trailer = PdfObject(type=PdfObjType.Null)
        self._version: str = ""
        self._file_path: str = ""
        self._object_cache: dict[int, PdfObject] = {}
        self._root: PdfNode | None = None

    # ------------------------------------------------------------------
    @classmethod
    def from_bytes(cls, data: bytes, filename: str = "") -> "PdfDocument":
        doc = cls()
        doc._file_path = filename
        doc._reader = PdfReader(data)
        doc._parse_document()
        doc._root = doc._build_tree()
        return doc

    # ------------------------------------------------------------------
    def root(self) -> PdfNode | None:
        return self._root

    def version(self) -> str:
        return self._version

    def file_path(self) -> str:
        return self._file_path

    def get_object_detail(self, num: int, gen: int = 0) -> str:
        obj = self.resolve_num(num, gen)
        if obj is None:
            return f"(object {num} {gen} R could not be resolved)"
        # Decode the stream now (lazy — only when user explicitly requests detail)
        if obj.type == PdfObjType.Stream and not obj.stream_decoded:
            decoded = _decode_stream(obj)
            if decoded is not None:
                obj.stream_decoded = decoded
        return f"Object {num} {gen} obj\n" + _detail(obj)

    # ------------------------------------------------------------------
    def resolve(self, ref: PdfRef) -> PdfObject | None:
        return self.resolve_num(ref.num, ref.gen)

    def resolve_num(self, num: int, gen: int = 0) -> PdfObject | None:
        if num in self._object_cache:
            return self._object_cache[num]

        xe = self._xref.get_entry(num)
        if xe is None or xe.etype == XrefEntryType.Free:
            return None

        if xe.etype == XrefEntryType.Compressed:
            return self._resolve_compressed(num, xe)

        # Normal in-use object
        assert self._reader is not None
        parser = PdfParser(self._reader)
        out: list[int] = []
        try:
            obj = parser.parse_indirect_object(xe.offset, out)
        except Exception:
            return None

        # Do NOT decode streams eagerly here — decode on demand in
        # get_object_detail() / _add_page_children() only when the user
        # actually selects a node.  Eager decoding of all streams during
        # tree construction pinned the CPU for PDFs with many pages.
        self._object_cache[num] = obj
        return obj

    def _resolve_compressed(self, num: int, xe: XrefEntry) -> PdfObject | None:
        """Resolve an object stored inside an object stream."""
        stm_obj = self.resolve_num(int(xe.offset))
        if stm_obj is None or stm_obj.type != PdfObjType.Stream:
            return None

        # Decode the object stream
        if stm_obj.stream_decoded:
            decoded = stm_obj.stream_decoded
        else:
            decoded = _decode_stream(stm_obj)
            if decoded is None:
                decoded = stm_obj.stream_raw

        if not decoded:
            return None

        n_obj = stm_obj.get("N")
        first_obj = stm_obj.get("First")
        if not n_obj.is_int() or not first_obj.is_int():
            return None

        N = int(n_obj.ival)
        first = int(first_obj.ival)

        # Parse the (objNum, offset) header pairs
        text = decoded.decode("latin-1", errors="replace")
        pairs: list[tuple[int, int]] = []
        pos = 0
        for _ in range(N):
            while pos < len(text) and text[pos] in " \t\r\n":
                pos += 1
            start = pos
            while pos < len(text) and text[pos].isdigit():
                pos += 1
            if start == pos:
                break
            on = int(text[start:pos])
            while pos < len(text) and text[pos] in " \t\r\n":
                pos += 1
            start = pos
            while pos < len(text) and text[pos].isdigit():
                pos += 1
            if start == pos:
                break
            off = int(text[start:pos])
            pairs.append((on, off))

        # Parse and cache all objects in the stream
        sub_reader = PdfReader(decoded)
        for idx, (on, off) in enumerate(pairs):
            if on in self._object_cache:
                continue
            abs_offset = first + off
            try:
                p = PdfParser(sub_reader)
                p.tok.seek(abs_offset)
                sub_obj = p.parse_object()
                self._object_cache[on] = sub_obj
            except Exception:
                pass

        return self._object_cache.get(num)

    # ------------------------------------------------------------------
    def _parse_document(self) -> None:
        assert self._reader is not None
        data = self._reader.data
        size = self._reader.size

        if size < 8:
            raise ValueError("File too small")
        if data[:5] != b"%PDF-":
            raise ValueError("Not a PDF file (missing %PDF- header)")

        # Parse version string
        i = 5
        while i < size and data[i] not in (ord("\r"), ord("\n"), ord(" ")):
            self._version += chr(data[i])
            i += 1

        # Find startxref offset (scan last 2048 bytes)
        sx_offset = self._reader.scan_backward(size, b"startxref", 2048)
        if sx_offset < 0:
            raise ValueError("No startxref found")

        # Parse the integer after "startxref"
        num_start = sx_offset + 9  # len("startxref") == 9
        while num_start < size and data[num_start] in (
            ord(" "), ord("\r"), ord("\n")
        ):
            num_start += 1
        num_end = num_start
        while num_end < size and ord("0") <= data[num_end] <= ord("9"):
            num_end += 1
        if num_end == num_start:
            raise ValueError("Cannot parse startxref value")
        xref_offset = int(data[num_start:num_end])

        self._trailer = self._xref.parse(self._reader, xref_offset)

    # ------------------------------------------------------------------
    # Tree building
    # ------------------------------------------------------------------
    def _build_tree(self) -> PdfNode:
        root = PdfNode(
            label="PDF Document",
            detail=f"PDF version: {self._version}\nFile: {self._file_path}",
        )

        # --- Header ---
        hdr = PdfNode(label="Header", detail=f"PDF version: {self._version}")
        hdr.children.append(
            PdfNode(label=f"Version: {self._version}", detail=self._version)
        )
        root.children.append(hdr)

        # --- Trailer ---
        tr_node = PdfNode(label="Trailer", detail=_detail(self._trailer))
        if self._trailer.is_dict():
            for k, v in self._trailer.dict.items():
                lbl = f"/{k}  {_brief(v)}"
                tr_node.children.append(self._build_value_node(lbl, v, 1))
        root.children.append(tr_node)

        # --- XRef Table ---
        entries = self._xref.entries
        in_use = sum(1 for e in entries.values() if e.etype == XrefEntryType.InUse)
        free = sum(1 for e in entries.values() if e.etype == XrefEntryType.Free)
        compressed = sum(1 for e in entries.values() if e.etype == XrefEntryType.Compressed)
        xref_node = PdfNode(
            label="XRef Table",
            detail=(
                f"Total entries: {len(entries)}\n"
                f"In-use: {in_use}\n"
                f"Free:   {free}\n"
                f"Compressed: {compressed}"
            ),
        )
        for obj_num in sorted(entries):
            xe = entries[obj_num]
            if xe.etype == XrefEntryType.Free:
                lbl = f"obj {obj_num}  [free]"
                # Free entries have no content to fetch; keep static detail
                en = PdfNode(
                    label=lbl,
                    detail=f"Object {obj_num} gen {xe.gen}  FREE",
                    obj_num=-1,  # no object to resolve
                    gen_num=xe.gen,
                )
            elif xe.etype == XrefEntryType.InUse:
                lbl = f"obj {obj_num}  @{xe.offset}"
                # detail="" triggers lazy fetch of real object content on click
                en = PdfNode(label=lbl, detail="", obj_num=obj_num, gen_num=xe.gen)
            else:
                lbl = f"obj {obj_num}  in ObjStm {xe.offset}"
                en = PdfNode(label=lbl, detail="", obj_num=obj_num, gen_num=xe.gen)
            xref_node.children.append(en)
        root.children.append(xref_node)

        # --- Catalog ---
        root_ref = self._trailer.get("Root")
        if root_ref.is_ref():
            cat_node = self._build_object_node(
                root_ref.ref.num,
                root_ref.ref.gen,
                f"{root_ref.ref.num} {root_ref.ref.gen} obj  [Catalog]",
                1,
            )
            root.children.append(cat_node)

            cat = self.resolve_num(root_ref.ref.num)
            if cat:
                pages_ref = cat.get("Pages")
                if pages_ref.is_ref():
                    pages = self.resolve_num(pages_ref.ref.num)
                    if pages:
                        count_obj = pages.get("Count")
                        pages_node = PdfNode(
                            label="Pages",
                            detail=(
                                f"Pages node ({count_obj.ival if count_obj.is_int() else '?'} pages)\n"
                                + _detail(pages)
                            ),
                            obj_num=pages_ref.ref.num,
                        )
                        kids = pages.get("Kids")
                        if kids.is_array():
                            page_num = 1
                            for kid_ref in kids.arr:
                                if not kid_ref.is_ref():
                                    continue
                                lbl = f"Page {page_num}  ({kid_ref.ref.num} 0 R)"
                                page_num += 1
                                pages_node.children.append(
                                    self._build_object_node(
                                        kid_ref.ref.num, kid_ref.ref.gen, lbl, 2
                                    )
                                )
                        root.children.append(pages_node)

        # --- Info dictionary ---
        info_ref = self._trailer.get("Info")
        if info_ref.is_ref():
            info_node = self._build_object_node(
                info_ref.ref.num,
                info_ref.ref.gen,
                f"{info_ref.ref.num} {info_ref.ref.gen} obj  [Info]",
                1,
            )
            info_node.label = "Info"
            root.children.append(info_node)

        return root

    # ------------------------------------------------------------------
    def _build_value_node(
        self, label: str, obj: PdfObject, depth: int
    ) -> PdfNode:
        node = PdfNode(label=label, detail=_detail(obj))
        if depth >= _MAX_DEPTH:
            return node

        if obj.type in (PdfObjType.Dictionary, PdfObjType.Stream):
            for k, v in obj.dict.items():
                child_lbl = f"/{k}  {_brief(v)}"
                if v.is_ref():
                    child = PdfNode(
                        label=child_lbl,
                        obj_num=v.ref.num,
                        gen_num=v.ref.gen,
                        detail=_brief(v),
                    )
                    node.children.append(child)
                else:
                    node.children.append(
                        self._build_value_node(child_lbl, v, depth + 1)
                    )
        elif obj.type == PdfObjType.Array:
            for idx, item in enumerate(obj.arr):
                child_lbl = f"[{idx}]  {_brief(item)}"
                node.children.append(
                    self._build_value_node(child_lbl, item, depth + 1)
                )
        return node

    def _build_object_node(
        self, num: int, gen: int, label: str, depth: int
    ) -> PdfNode:
        node = PdfNode(label=label, obj_num=num, gen_num=gen)

        obj = self.resolve_num(num, gen)
        if obj is None:
            node.detail = "(unresolvable)"
            return node

        node.detail = _detail(obj)

        if depth >= _MAX_DEPTH:
            return node

        # Annotate label with /Type
        if obj.is_dict():
            type_obj = obj.get("Type")
            if type_obj.is_name():
                node.label = f"{label}  [/{type_obj.sval}]"
            sub_obj = obj.get("Subtype")
            if sub_obj.is_name() and sub_obj.sval == "Image":
                node.is_image = True

        # Children from dict entries
        if obj.is_dict():
            for k, v in obj.dict.items():
                child_lbl = f"/{k}  {_brief(v)}"
                if v.is_ref():
                    child = PdfNode(
                        label=child_lbl,
                        obj_num=v.ref.num,
                        gen_num=v.ref.gen,
                        detail=f"Reference: {v.ref.num} {v.ref.gen} R\n\n(Select to load target object)",
                    )
                    node.children.append(child)
                else:
                    node.children.append(
                        self._build_value_node(child_lbl, v, depth + 1)
                    )
        elif obj.is_array():
            for idx, item in enumerate(obj.arr):
                child_lbl = f"[{idx}]  {_brief(item)}"
                node.children.append(
                    self._build_value_node(child_lbl, item, depth + 1)
                )

        # Special handling for Page objects
        if obj.is_dict():
            type_obj = obj.get("Type")
            if type_obj.is_name() and type_obj.sval == "Page":
                self._add_page_children(node, obj)

        return node

    def _add_page_children(self, node: PdfNode, page_obj: PdfObject) -> None:
        """Add Content Stream(s) and XObject nodes to a Page node.

        Streams are NOT decoded here — decoding is deferred until the user
        selects the node and get_object_detail() is called.  Eager decoding
        of all page content streams during tree construction caused severe
        CPU spikes on large PDFs.
        """
        # --- Content Stream(s) ---
        cont_val = page_obj.get("Contents")
        if not cont_val.is_null():
            refs: list[PdfRef] = []
            if cont_val.is_ref():
                refs.append(cont_val.ref)
            elif cont_val.is_array():
                for item in cont_val.arr:
                    if item.is_ref():
                        refs.append(item.ref)

            cs_node = PdfNode(label="\u25b6 Content Stream(s)  [text & drawing operators]",
                              detail="(select a stream node to decode and view)")

            for ref in refs:
                stm = self.resolve_num(ref.num, ref.gen)
                raw_len = len(stm.stream_raw) if stm else 0
                sn = PdfNode(
                    label=f"stream  {ref.num} 0 R  [{raw_len} bytes raw]",
                    obj_num=ref.num,
                    gen_num=ref.gen,
                    detail="",  # empty → frontend fetches via /api/object/...
                )
                cs_node.children.append(sn)

            node.children.append(cs_node)

        # --- XObjects (Images & Forms) ---
        res_val = page_obj.get("Resources")
        res: PdfObject | None = None
        if res_val.is_ref():
            res = self.resolve_num(res_val.ref.num, res_val.ref.gen)
        elif res_val.is_dict():
            res = res_val

        if res:
            xo_dict = res.get("XObject")
            xo_ptr: PdfObject | None = None
            if xo_dict.is_ref():
                xo_ptr = self.resolve_num(xo_dict.ref.num, xo_dict.ref.gen)
            elif xo_dict.is_dict():
                xo_ptr = xo_dict

            if xo_ptr and xo_ptr.dict:
                xo_node = PdfNode(
                    label="\U0001f5bc XObjects  (Images & Forms)",
                    detail=f"XObject resources ({len(xo_ptr.dict)} entries)",
                )
                for k, v in xo_ptr.dict.items():
                    xo: PdfObject | None = None
                    xnum, xgen = -1, 0
                    if v.is_ref():
                        xnum, xgen = v.ref.num, v.ref.gen
                        xo = self.resolve_num(xnum, xgen)
                    elif v.is_dict() or v.type == PdfObjType.Stream:
                        xo = v

                    subtype = "?"
                    extra = ""
                    is_img = False
                    if xo:
                        st = xo.get("Subtype")
                        if st.is_name():
                            subtype = st.sval
                        if subtype == "Image":
                            is_img = True
                            w = xo.get("Width")
                            h = xo.get("Height")
                            bpc = xo.get("BitsPerComponent")
                            cs = xo.get("ColorSpace")
                            if w.is_int() and h.is_int():
                                extra += f"  {w.ival}x{h.ival}"
                            if bpc.is_int():
                                extra += f"  {bpc.ival}bpc"
                            if cs.is_name():
                                extra += f"  /{cs.sval}"

                    lbl = f"/{k}  [/{subtype}]{extra}"
                    if xnum >= 0:
                        lbl += f"  ({xnum} 0 R)"

                    xn = PdfNode(
                        label=lbl,
                        obj_num=xnum,
                        gen_num=xgen,
                        detail="",  # fetched lazily
                        is_image=is_img,
                    )
                    xo_node.children.append(xn)
                node.children.append(xo_node)
