"""
AI Agent Service — multi-round LLM agent for PDF content stream editing.

Public API:
  async def chat(upload_id, obj_num, obj_gen, user_message, history,
                 provider, model) -> AsyncGenerator[AgentEvent, None]

Each call is an async generator that yields AgentEvent objects which the
FastAPI SSE endpoint serialises and forwards to the browser.

Inner loop (up to MAX_ROUNDS):
  1. build messages (system prompt + context + history + user turn + prior errors)
  2. call LLM via llm_client.complete()
  3. parse <pdfs>…</pdfs> block from response
  4. validate the new content stream
  5. on failure → append error to prior_errors, retry
  6. on success → relink PDF, update AgentSession, yield done
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator

from .llm_config import LLMConfig, get_llm_config
from .llm_client import complete as llm_complete

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3

# Set to True to show full HTTP request/response bodies in the AI Edit chat pane.
# Flip to False to silence the HTTP log without removing the instrumentation.
DEBUG_HTTP: bool = True

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class AgentEvent:
    event: str    # "step" | "done" | "error"
    data: dict


@dataclass
class AgentSession:
    upload_id: str
    pdfx_dir: Path          # persisted PDFX export dir (temp lifetime = server process)
    current_pdf: Path       # latest linked PDF path
    active_obj: tuple[int, int] | None = None
    pdfs_history: list[str] = field(default_factory=list)   # per-turn undo snapshots


# In-memory agent sessions — keyed by upload_id
_agent_sessions: dict[str, AgentSession] = {}


# ---------------------------------------------------------------------------
# SSE event helpers
# ---------------------------------------------------------------------------

def _step(type_: str, **kwargs) -> AgentEvent:
    return AgentEvent("step", {"type": type_, **kwargs})

def _done(**kwargs) -> AgentEvent:
    return AgentEvent("done", kwargs)

def _error(msg: str) -> AgentEvent:
    return AgentEvent("error", {"error": msg})


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------

def _find_pdf_path(upload_id: str) -> Path:
    """Return the uploaded PDF path for *upload_id*."""
    from pathlib import Path as _P
    uploads_dir = _P(__file__).parent.parent / "uploads" / upload_id
    pdf_files = [f for f in uploads_dir.iterdir() if f.suffix.lower() == ".pdf"]
    if not pdf_files:
        raise FileNotFoundError(f"No PDF found for upload '{upload_id}'")
    return pdf_files[0]


def _get_or_create_session(upload_id: str) -> AgentSession:
    """Return existing AgentSession or create one by exporting the upload."""
    if upload_id in _agent_sessions:
        return _agent_sessions[upload_id]

    from .exporter import export_pdf

    pdf_path = _find_pdf_path(upload_id)
    # Create a temp dir that persists for the lifetime of the server process.
    # We keep a reference via AgentSession so it's not GC'd.
    tmp = tempfile.mkdtemp(prefix=f"agent_{upload_id}_")
    tmp_path = Path(tmp)
    pdfx_dir = export_pdf(str(pdf_path), tmp)
    # Copy original as the current "modified" PDF (identity — no changes yet)
    import shutil
    current_pdf = tmp_path / "current.pdf"
    shutil.copy2(pdf_path, current_pdf)

    session = AgentSession(
        upload_id=upload_id,
        pdfx_dir=pdfx_dir,
        current_pdf=current_pdf,
    )
    _agent_sessions[upload_id] = session
    return session


def _resolve_stream_obj(session: AgentSession, obj_num: int, obj_gen: int) -> tuple[int, int]:
    """If obj is a Contents array, return the first stream ref inside it.

    PDF pages often have a /Contents key pointing to an array such as
    [11 0 R  12 0 R ...].  The array itself has no .pdfs; its element streams do.
    This helper reads the .pdfjson and, if the object is an Array, follows the
    first reference to find the actual stream object.
    """
    import json, re
    fname = f"obj_{obj_num:05d}_{obj_gen}.pdfjson"
    p = session.pdfx_dir / "objects" / fname
    if not p.exists():
        return obj_num, obj_gen

    meta = json.loads(p.read_text(encoding="utf-8"))
    # If _type is Array, the .pdfjson looks like:
    #   {"_obj": "4 0", "_type": "Array", "_stream": "none"}
    # The actual content is in the .pdfo (raw bytes) but we need to look at
    # the exporter for array-of-refs structure.  Instead, scan for any
    # .pdfs files whose obj number follows immediately (heuristic: pick the
    # lowest-numbered .pdfs in the export dir).
    if meta.get("_type") == "Array":
        pdfs_files = sorted(session.pdfx_dir.glob("objects/*.pdfs"))
        if pdfs_files:
            stem = pdfs_files[0].stem  # e.g. "obj_00011_0"
            m = re.match(r"obj_(\d+)_(\d+)$", stem)
            if m:
                return int(m.group(1)), int(m.group(2))

    return obj_num, obj_gen


def _get_current_pdfs(session: AgentSession, obj_num: int, obj_gen: int) -> str:
    """Return the current decoded content stream text for the object."""
    fname = f"obj_{obj_num:05d}_{obj_gen}.pdfs"
    pdfs_path = session.pdfx_dir / "objects" / fname
    if not pdfs_path.exists():
        raise FileNotFoundError(
            f"No .pdfs file for object {obj_num} {obj_gen} in PDFX dir"
        )
    return pdfs_path.read_text(encoding="utf-8")


def _get_obj_json(session: AgentSession, obj_num: int, obj_gen: int) -> str:
    """Return the object's .pdfjson as a string for context."""
    fname = f"obj_{obj_num:05d}_{obj_gen}.pdfjson"
    p = session.pdfx_dir / "objects" / fname
    return p.read_text(encoding="utf-8") if p.exists() else "{}"


def _get_page_info(upload_id: str, obj_num: int) -> dict:
    """Try to extract MediaBox and font-name map for the page that owns obj_num.

    Returns a dict with keys: width, height, font_map.
    Falls back to zeros / empty map if the session is not loaded.
    """
    # Import here to avoid circular dependency with main.py's session store
    try:
        from pdf.document import PdfDocument, _decode_stream
        from pdf.objects import PdfObjType
        from pdf.xref import XrefEntryType

        uploads_dir = Path(__file__).parent.parent / "uploads" / upload_id
        pdf_files = [f for f in uploads_dir.iterdir() if f.suffix.lower() == ".pdf"]
        if not pdf_files:
            return {"width": 0, "height": 0, "font_map": {}}

        doc = PdfDocument.from_bytes(pdf_files[0].read_bytes(), str(pdf_files[0]))

        # Find the Page object that references obj_num
        page_obj = None
        for num, entry in doc._xref.entries.items():
            if entry.etype not in (XrefEntryType.InUse, XrefEntryType.Compressed):
                continue
            o = doc.resolve_num(num, entry.gen)
            if o is None:
                continue
            type_v = o.get("Type") if (o.is_dict() or o.type == PdfObjType.Stream) else None
            if type_v and type_v.is_name() and type_v.sval == "Page":
                # Check if this page's /Contents references obj_num
                contents = o.get("Contents")
                if _refs_obj(doc, contents, obj_num):
                    page_obj = o
                    break

        width, height = 0.0, 0.0
        font_map: dict[str, str] = {}

        if page_obj is not None:
            mb = page_obj.get("MediaBox")
            if mb.is_array() and len(mb.arr) == 4:
                try:
                    width = float(mb.arr[2].dval or mb.arr[2].ival)
                    height = float(mb.arr[3].dval or mb.arr[3].ival)
                except Exception:
                    pass

            # Build font name map: /Alias -> BaseFont name
            res = page_obj.get("Resources")
            if res.is_null():
                res = page_obj  # sometimes Resources is inline
            fonts = res.get("Font")
            if fonts.is_dict():
                for alias, ref in fonts.dict.items():
                    font_obj = doc.resolve(ref)
                    if font_obj is None:
                        continue
                    bf = font_obj.get("BaseFont")
                    if bf.is_name():
                        font_map[f"/{alias}"] = bf.sval

        return {"width": width, "height": height, "font_map": font_map}

    except Exception as exc:
        logger.warning("Could not gather page info: %s", exc)
        return {"width": 0, "height": 0, "font_map": {}}


def _refs_obj(doc, obj, target_num: int) -> bool:
    """Check whether *obj* (or any array element) is a reference to *target_num*."""
    from pdf.objects import PdfObjType
    if obj.type == PdfObjType.Reference:
        return obj.ref.num == target_num
    if obj.is_array():
        return any(_refs_obj(doc, el, target_num) for el in obj.arr)
    return False


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
You are a PDF content stream editor. You will be given:
  - A decoded PDF content stream (PDF operators)
  - The object's JSON dictionary
  - Page dimensions (width × height in points)
  - The font resource map for this page
  - A user request to modify the content stream

Your task:
  1. Make the minimal change to satisfy the user's request.
  2. Return ONLY the modified content stream, enclosed in <pdfs> tags.
  3. After the </pdfs> tag, write a brief one-paragraph explanation of what you changed.
  4. Do not add or remove stream operators outside of what is needed.
  5. Preserve all BT/ET pairing, q/Q pairing, and cm stack balance.
  6. Coordinates: origin is bottom-left; x increases right, y increases up.
     Page size: {width:.1f} × {height:.1f} points.

Font resource map (alias → BaseFont name):
{font_map}

PDF operator quick reference:
  BT / ET         — begin/end text block
  Tf              — set font: /FontName size Tf
  Td              — move text position: dx dy Td
  Tm              — set text matrix: a b c d e f Tm
  Tj / TJ         — show text string
  re              — rectangle: x y w h re
  rg / RG         — set fill/stroke color (RGB): r g b rg
  f / S / B       — fill / stroke / fill+stroke path
  q / Q           — save / restore graphics state
  cm              — concatenate matrix to CTM
"""


def _fmt_font_map(font_map: dict) -> str:
    if not font_map:
        return "  (none)"
    return "\n".join(f"  {alias} → {name}" for alias, name in sorted(font_map.items()))


def _build_messages(
    current_pdfs: str,
    obj_json: str,
    page_info: dict,
    history: list[dict],
    user_message: str,
    prior_errors: list[str],
) -> list[dict]:
    """Build the messages array for the LLM call."""
    system = _SYSTEM_PROMPT_TEMPLATE.format(
        width=page_info["width"],
        height=page_info["height"],
        font_map=_fmt_font_map(page_info["font_map"]),
    )

    messages: list[dict] = [{"role": "system", "content": system}]

    # Inject prior conversation history (alternating user/assistant turns)
    for turn in history:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": content})

    # Build the current user turn
    user_content = (
        f"Object dictionary:\n```json\n{obj_json}\n```\n\n"
        f"Current content stream:\n---\n{current_pdfs}\n---\n\n"
        f"User request: {user_message}"
    )
    if prior_errors:
        error_block = "\n".join(f"  - {e}" for e in prior_errors)
        user_content += (
            f"\n\nIMPORTANT — your previous response(s) had errors:\n{error_block}\n"
            "Please fix these issues in your next response."
        )

    messages.append({"role": "user", "content": user_content})
    return messages


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_PDFS_RE = re.compile(r"<pdfs>(.*?)</pdfs>", re.DOTALL)


def _parse_response(text: str, finish_reason: str = "") -> tuple[str | None, str]:
    """Extract (new_pdfs, explanation) from LLM response text.

    Returns (None, "") if no <pdfs> block is found.

    When finish_reason is 'length' the response may be truncated before the
    closing </pdfs> tag.  In that case we accept the partial content — the
    caller's validation step will catch any structural problems.
    """
    m = _PDFS_RE.search(text)
    if not m:
        # Truncation fallback: LLM hit token limit mid-block
        if finish_reason == "length":
            open_tag = text.find("<pdfs>")
            if open_tag != -1:
                new_pdfs = text[open_tag + 6:].lstrip("\n")
                return new_pdfs, ""
        return None, ""
    new_pdfs = m.group(1)
    # Strip a single leading/trailing newline (LLM formatting habit)
    new_pdfs = new_pdfs.lstrip("\n").rstrip("\n")
    explanation = text[m.end():].strip()
    return new_pdfs, explanation


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_pdfs(text: str) -> list[str]:
    """Return a list of validation error strings; empty list = valid."""
    errors: list[str] = []

    # Check for null bytes or non-text binary content
    if "\x00" in text:
        errors.append("Content stream contains null bytes — do not include binary data")

    # Check for non-printable / control characters (allow common whitespace)
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("C") and ch not in ("\n", "\r", "\t"):
            errors.append(
                f"Content stream contains control character U+{ord(ch):04X} — "
                "use only printable ASCII and standard whitespace"
            )
            break

    # BT / ET balance
    bt_count = len(re.findall(r"\bBT\b", text))
    et_count = len(re.findall(r"\bET\b", text))
    if bt_count != et_count:
        errors.append(
            f"BT/ET imbalance: {bt_count} BT vs {et_count} ET"
        )

    # q / Q balance
    q_count = len(re.findall(r"\bq\b", text))
    bq_count = len(re.findall(r"\bQ\b", text))
    if q_count != bq_count:
        errors.append(
            f"q/Q imbalance: {q_count} q vs {bq_count} Q"
        )

    return errors


# ---------------------------------------------------------------------------
# Re-linking
# ---------------------------------------------------------------------------

def _apply_and_relink(
    session: AgentSession,
    obj_num: int,
    obj_gen: int,
    new_pdfs: str,
) -> Path:
    """Write new_pdfs to the PDFX dir and re-link the PDF.

    Returns the path to the newly linked PDF.
    """
    from .linker import link_pdf

    fname = f"obj_{obj_num:05d}_{obj_gen}.pdfs"
    pdfs_path = session.pdfx_dir / "objects" / fname
    pdfs_path.write_text(new_pdfs, encoding="utf-8")

    out_path = session.pdfx_dir.parent / "current.pdf"
    link_pdf(session.pdfx_dir, out_path)
    return out_path


# ---------------------------------------------------------------------------
# Simple text diff
# ---------------------------------------------------------------------------

def _make_diff(old: str, new: str) -> str:
    """Return a minimal unified-diff-style string between old and new."""
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    import difflib
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=3))
    return "\n".join(diff)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def chat(
    upload_id: str,
    obj_num: int,
    obj_gen: int,
    user_message: str,
    history: list[dict],
    provider: str | None = None,
    model: str | None = None,
    debug_http: bool = True,
) -> AsyncGenerator[AgentEvent, None]:
    """Async generator — yields AgentEvent objects as the agent works."""

    # --- Config ---
    try:
        cfg: LLMConfig = get_llm_config(provider, model)
    except ValueError as exc:
        yield _error(str(exc))
        return

    # --- Session / context setup ---
    yield _step("thinking", text="Gathering context…")
    try:
        session = _get_or_create_session(upload_id)
        # If the selected object is a Contents array, resolve to the first stream
        obj_num, obj_gen = _resolve_stream_obj(session, obj_num, obj_gen)
        session.active_obj = (obj_num, obj_gen)
        current_pdfs = _get_current_pdfs(session, obj_num, obj_gen)
        obj_json = _get_obj_json(session, obj_num, obj_gen)
    except FileNotFoundError:
        yield _error(
            f"Object {obj_num} {obj_gen} is not a content stream. "
            "Please open a content stream object (e.g. a Page's stream) "
            "and click ✦ AI Edit from there."
        )
        return
    except Exception as exc:
        yield _error(f"Context gathering failed: {exc}")
        return

    page_info = _get_page_info(upload_id, obj_num)

    # --- Inner loop ---
    prior_errors: list[str] = []
    new_pdfs: str | None = None
    explanation = ""

    for round_num in range(1, MAX_ROUNDS + 1):
        if round_num == 1:
            yield _step("thinking", round=round_num,
                        text=f"Analysing content stream and building edit plan")
        else:
            yield _step("retry", round=round_num,
                        text=f"Round {round_num} — asking LLM to fix: {prior_errors[-1]}")

        messages = _build_messages(
            current_pdfs, obj_json, page_info,
            history, user_message, prior_errors,
        )

        prompt_chars = sum(len(m.get("content", "")) for m in messages)

        if debug_http:
            _req_body = json.dumps(
                {"model": cfg.model, "max_tokens": cfg.max_tokens,
                 "stream": True, "messages": messages},
                ensure_ascii=False, indent=2,
            )
            _req_detail = (
                f"=== HTTP REQUEST ===\n"
                f"POST {cfg.base_url}/chat/completions\n"
                f"Authorization: Bearer ***redacted***\n"
                f"Content-Type: application/json\n\n"
                f"{_req_body}"
            )
        else:
            _req_detail = (
                f"provider: {cfg.provider}  model: {cfg.model}\n"
                f"base_url: {cfg.base_url}\n"
                f"max_tokens: {cfg.max_tokens}  timeout: {cfg.timeout}s\n"
                f"messages: {len(messages)}  prompt_chars: {prompt_chars}"
            )
        yield _step("llm_request", round=round_num,
                    text=f"Sending request to LLM (round {round_num})",
                    detail=_req_detail)

        try:
            llm_text, debug_info = await llm_complete(messages, cfg)
        except Exception as exc:
            import traceback
            yield _error(
                f"LLM call failed: {type(exc).__name__}: {exc}\n\n"
                f"Traceback:\n{traceback.format_exc()}"
            )
            return

        if debug_http:
            _resp_detail = (
                f"=== HTTP RESPONSE ===\n"
                f"Status: {debug_info.get('http_status', 200)}\n"
                f"Elapsed: {debug_info.get('elapsed', '?')}\n"
                f"finish_reason: {debug_info.get('finish_reason', '?')}\n"
                f"usage: {json.dumps(debug_info.get('usage', {}))}\n\n"
                f"=== LLM TEXT ===\n"
                f"{llm_text}"
            )
        else:
            _resp_detail = llm_text
        yield _step("llm_response", round=round_num,
                    text=(
                        f"Response received — "
                        f"{debug_info.get('elapsed','?')}  "
                        f"finish={debug_info.get('finish_reason','?')}  "
                        f"usage={debug_info.get('usage',{})}"
                    ),
                    detail=_resp_detail)

        new_pdfs, explanation = _parse_response(
            llm_text, finish_reason=debug_info.get("finish_reason", "")
        )
        if new_pdfs is None:
            msg = "LLM response contained no <pdfs>…</pdfs> block"
            prior_errors.append(msg)
            yield _step("validation", round=round_num,
                        status="fail", text=msg)
            continue

        errors = _validate_pdfs(new_pdfs)
        if errors:
            err_text = "; ".join(errors)
            prior_errors.append(err_text)
            yield _step("validation", round=round_num,
                        status="fail", text=err_text)
            continue

        # Validation passed
        yield _step("validation", round=round_num,
                    status="ok", text="Content stream valid")
        break

    else:
        # Exhausted all rounds
        yield _error(
            f"Could not produce a valid content stream after {MAX_ROUNDS} rounds. "
            f"Last errors: {'; '.join(prior_errors[-1:])}"
        )
        return

    # --- Relink ---
    yield _step("relink", text="Re-linking PDF…")
    try:
        old_pdfs = current_pdfs
        new_pdf_path = _apply_and_relink(session, obj_num, obj_gen, new_pdfs)
        session.pdfs_history.append(old_pdfs)
        session.current_pdf = new_pdf_path
    except Exception as exc:
        yield _error(f"Re-linking failed: {exc}")
        return

    diff = _make_diff(old_pdfs, new_pdfs)
    download_url = (
        f"/api/agent/download/{upload_id}/{obj_num}/{obj_gen}/modified.pdf"
    )

    yield _done(
        reply=explanation or "Done.",
        new_pdfs=new_pdfs,
        diff=diff,
        download_url=download_url,
    )


# ---------------------------------------------------------------------------
# Undo support
# ---------------------------------------------------------------------------

async def undo(upload_id: str) -> dict:
    """Revert the last edit for *upload_id*.

    Returns a dict with keys: new_pdfs, download_url, ok.
    """
    session = _agent_sessions.get(upload_id)
    if session is None or not session.pdfs_history:
        return {"ok": False, "error": "Nothing to undo"}

    if session.active_obj is None:
        return {"ok": False, "error": "No active object"}

    obj_num, obj_gen = session.active_obj
    prev_pdfs = session.pdfs_history.pop()

    try:
        new_pdf_path = _apply_and_relink(session, obj_num, obj_gen, prev_pdfs)
        session.current_pdf = new_pdf_path
    except Exception as exc:
        return {"ok": False, "error": f"Re-linking failed: {exc}"}

    download_url = (
        f"/api/agent/download/{upload_id}/{obj_num}/{obj_gen}/modified.pdf"
    )
    return {"ok": True, "new_pdfs": prev_pdfs, "download_url": download_url}


def evict_session(upload_id: str) -> None:
    """Remove an agent session and clean up its temp files."""
    import shutil
    session = _agent_sessions.pop(upload_id, None)
    if session:
        try:
            shutil.rmtree(session.pdfx_dir.parent, ignore_errors=True)
        except Exception:
            pass
