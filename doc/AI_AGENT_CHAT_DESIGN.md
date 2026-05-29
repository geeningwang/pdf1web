# AI Agent Chat Pane — Design Document

**Status**: Proposed  
**Date**: May 2026  
**Scope**: Right-side chat pane for LLM-driven PDF content stream editing

---

## 1. Overview

Users can select a content stream object in the left-pane tree view and then interact with an LLM in a right-side chat pane to edit it in natural language. The server-side agent translates each message into a concrete modification of the content stream's `.pdfs` file, re-links the PDF, and returns the result for download or live preview.

**Example interactions**:
- "Move the heading down by 30 points"
- "Change all text to red"
- "Remove the background rectangle"
- "Increase the font size of the first paragraph to 14pt"

---

## 2. User Flow

```
1. User uploads or opens a PDF → tree view appears in left pane
2. User clicks a content stream node in the tree (type_label = "Content stream")
3. Right pane switches to AI Chat mode (alongside the existing ContentStreamPane)
4. Chat pane shows:
   - The decoded content stream (read-only preview, collapsible)
   - A message input box
   - A "Send" button
   - A "Download modified PDF" button (enabled after first successful edit)
5. User types a request → agent processes → chat shows:
   - Agent's explanation of what it changed
   - A diff of the modified content stream (old vs new)
   - A "Apply" button (if user wants to confirm before download)
6. User can continue chatting (multi-turn, accumulated edits)
7. User downloads the final PDF at any point
```

---

## 3. Architecture

```
Browser                         FastAPI Server                 LLM Provider
──────────────────              ──────────────────────         ─────────────
TreePane                        
  │ select content stream       
  ▼                             
AiChatPane                      
  │ POST /api/agent/chat        
  │ {upload_id, obj_num, gen,  ──────────────────────────►   /v1/chat/completions
  │  message, history}          AgentService                 (OpenAI / Anthropic /
  │                               build_prompt()              any compatible)
  │                               call_llm()         ◄───────
  │                               extract_pdfs()              
  │                               validate_pdfs()             
  │                               relink_pdf()                
  │◄──────────────────────────   return reply + download_url  
  │                             
  ▼                             
Show diff, explanation          
Download modified PDF           
```

### Components

| Component | Location | Responsibility |
|---|---|---|
| `AiChatPane.tsx` | `frontend/src/components/` | Chat UI — message input, history, diff view, download button |
| `agentService.py` | `backend/pdf/` | Core agent — prompt building, LLM call, response parsing, PDF re-linking |
| `llm_client.py` | `backend/pdf/` | Provider-agnostic LLM HTTP client — OpenAI-compatible REST |
| `llm_config.py` | `backend/pdf/` | Configuration loading from env / config file |
| Agent endpoints | `backend/main.py` | `POST /api/agent/chat`, `GET /api/agent/config` |

---

## 4. Frontend Design

### 4.1 Layout

The existing two-pane layout (tree | detail) becomes a three-pane layout when a content stream is selected and the AI pane is activated:

```
┌──────────────────┬────────────────────┬───────────────────────┐
│   Tree Pane      │   Detail Pane      │   AI Chat Pane        │
│   (left, fixed)  │   (center, flex)   │   (right, resizable)  │
│                  │                    │                       │
│  ▶ Body          │  ContentStreamPane │  ┌─ Content stream ─┐ │
│    ▶ obj 5 0    │  (operators, canvas│  │ BT /F1 12 Tf ... │ │
│    ▶ obj 12 0 ● │   renderer)        │  └──────────────────┘ │
│    ...           │                    │                       │
│                  │                    │  ┌─ Chat history ───┐ │
│                  │                    │  │ You: move heading │ │
│                  │                    │  │ down 30pt        │ │
│                  │                    │  │                  │ │
│                  │                    │  │ Agent: Changed   │ │
│                  │                    │  │ Td 72 720 → Td   │ │
│                  │                    │  │ 72 690           │ │
│                  │                    │  └──────────────────┘ │
│                  │                    │                       │
│                  │                    │  [message input    ]  │
│                  │                    │  [Send] [Download PDF]│
└──────────────────┴────────────────────┴───────────────────────┘
```

The AI pane is hidden by default and appears when:
- A content stream node is selected **and**
- The user clicks "Open AI Chat" (a button added to `ContentStreamPane`)

### 4.2 `AiChatPane.tsx` State

```typescript
interface ChatMessage {
  role: 'user' | 'agent'
  text: string           // explanation text
  diff?: { old: string; new: string }   // unified diff of .pdfs
  downloadUrl?: string   // blob URL for the modified PDF
}

interface AiChatPaneProps {
  uploadId: string
  objNum: number
  objGen: number
  currentPdfs: string    // current decoded content stream text
}
```

### 4.3 Diff View

Show a simple side-by-side or inline diff of the old vs. new content stream after each agent turn. Highlight changed lines in green/red. Use a lightweight diff library (`diff` npm package).

---

## 5. Backend API

### 5.1 `POST /api/agent/chat`

**Request body** (JSON):
```json
{
  "upload_id": "a12de1f8-...",
  "obj_num": 12,
  "obj_gen": 0,
  "message": "Move the heading down by 30 points",
  "history": [
    { "role": "user",  "content": "..." },
    { "role": "assistant", "content": "..." }
  ]
}
```

**Response** (JSON):
```json
{
  "reply": "I moved the first Td operator from '72 720 Td' to '72 690 Td'.",
  "new_pdfs": "BT\n/F1 12 Tf\n72 690 Td\n...",
  "diff": "--- old\n+++ new\n@@ -3 +3 @@\n-72 720 Td\n+72 690 Td",
  "download_url": "/api/agent/download/a12de1f8-.../12/0/modified.pdf",
  "error": null
}
```

If the LLM produces an invalid content stream, `error` contains the validation message and `new_pdfs` / `download_url` are null — the agent explains what went wrong and invites the user to rephrase.

### 5.2 `GET /api/agent/config`

Returns available LLM providers and their configured models (no keys exposed):

```json
{
  "providers": [
    { "id": "openai",    "name": "OpenAI",    "models": ["gpt-4o", "gpt-4o-mini"] },
    { "id": "anthropic", "name": "Anthropic", "models": ["claude-opus-4-5"] }
  ],
  "default_provider": "openai",
  "default_model": "gpt-4o-mini"
}
```

### 5.3 `GET /api/agent/download/{upload_id}/{obj_num}/{obj_gen}/modified.pdf`

Returns the latest modified PDF for this session as `application/pdf`. This is a temporary file stored in the session's temp directory on the server.

---

## 6. Agent Design (`agentService.py`)

### 6.1 Agent Entry Point

```python
def chat(
    upload_id: str,
    obj_num: int,
    obj_gen: int,
    user_message: str,
    history: list[dict],
    provider: str,
    model: str,
) -> AgentResult:
    ...
```

### 6.2 Context Gathering

Before building the prompt, the agent collects:
1. **Current `.pdfs`** — the decoded content stream, either from:
   - An in-session temp PDFX dir (if a prior edit was applied), or
   - A fresh export of `uploads/<upload_id>/*.pdf`
2. **`.pdfjson`** — the object's dict (page dimensions, filter chain)
3. **Page dimensions** — resolved from the parent Page `/MediaBox` (needed for coordinate context)
4. **Font map** — `/Resources /Font` dict from the page, resolved to font names (so the agent knows `/F1` = Helvetica, etc.)

### 6.3 System Prompt

```
You are a PDF content stream editor. You will be given:
  - A decoded PDF content stream (PDF operators)
  - The object's JSON dictionary
  - Page dimensions (width × height in points)
  - The font resource map for this page
  - A user request to modify the content stream

Your task:
  1. Make the minimal change to satisfy the user's request.
  2. Return ONLY the modified content stream, enclosed in <pdfs> tags.
  3. After the <pdfs> block, write a brief one-paragraph explanation of what you changed.
  4. Do not add or remove stream operators outside of what is needed.
  5. Preserve all BT/ET pairing, q/Q pairing, and cm stack balance.
  6. Coordinates: origin is bottom-left; x increases right, y increases up.
     Page size: {width} × {height} points.

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
```

### 6.4 User Turn Construction

Each user turn in the messages array:
```
Current content stream:
---
{current_pdfs}
---

User request: {user_message}
```

For multi-turn, the full `history` is passed so the LLM sees the accumulated edits.

### 6.5 Response Parsing

1. Extract content between `<pdfs>` and `</pdfs>` tags — this is the new stream.
2. Extract explanation text after the `</pdfs>` closing tag.
3. If no `<pdfs>` block found, treat the entire response as an error explanation.

### 6.6 Validation

After extracting the new stream:
- Check BT/ET balance (equal counts)
- Check q/Q balance
- Check no binary / null bytes
- Attempt to tokenize with the existing `pdf.tokenizer` — catch parse errors

If validation fails: return the error message to the user without applying the change.

### 6.7 Re-linking

1. Export the original PDF to a temp PDFX dir (or reuse the session PDFX dir).
2. Overwrite `objects/obj_{NNNNN}_{G}.pdfs` with the new stream.
3. Call `link_pdf(pdfx_dir, out_path)` — the modified object will be detected as changed (sha256 mismatch) and re-serialized with re-encoding.
4. Store `out_path` in the session for the download endpoint.

### 6.8 Session State (server-side)

```python
# In _sessions or a parallel dict:
_agent_sessions: dict[str, AgentSession] = {}

@dataclass
class AgentSession:
    upload_id: str
    pdfx_dir: Path          # temp PDFX dir, kept alive for multi-turn
    current_pdf: Path       # latest linked PDF
    active_obj: tuple[int, int] | None
```

The PDFX dir is created on first chat request for an `upload_id` and reused for subsequent turns. It is deleted when the upload session is evicted.

---

## 7. LLM Provider Configuration (`llm_config.py`)

### 7.1 Configuration Sources (priority order)

1. Environment variables
2. `backend/llm_config.json` (gitignored)

### 7.2 Environment Variables

```bash
LLM_PROVIDER=openai           # openai | anthropic | custom
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.openai.com/v1    # override for custom endpoints
LLM_MODEL=gpt-4o-mini
LLM_MAX_TOKENS=4096
LLM_TIMEOUT=60
```

### 7.3 `llm_config.json` Schema

```json
{
  "providers": {
    "openai": {
      "api_key": "sk-...",
      "base_url": "https://api.openai.com/v1",
      "default_model": "gpt-4o-mini",
      "max_tokens": 4096,
      "timeout": 60
    },
    "anthropic": {
      "api_key": "sk-ant-...",
      "base_url": "https://api.anthropic.com/v1",
      "default_model": "claude-opus-4-5",
      "max_tokens": 4096,
      "timeout": 60
    }
  },
  "default_provider": "openai"
}
```

### 7.4 `llm_client.py` — OpenAI-Compatible REST Client

All providers are accessed via the OpenAI chat completions API shape (`POST /v1/chat/completions`). Anthropic's Messages API is wrapped in an adapter that converts to/from the OpenAI message format.

No third-party LLM SDK dependencies — uses `httpx` (already available via `fastapi`/`uvicorn` dependencies) for async HTTP calls.

---

## 8. Scope Constraints (v1)

| In scope | Out of scope |
|---|---|
| Content stream objects only | Images, fonts, form fields |
| Text position, color, size edits | Adding new fonts or embedding images |
| Single content stream per turn | Multi-stream (form XObjects etc.) |
| Download modified PDF | Live canvas preview update |
| Multi-turn chat within session | Persistent chat history across sessions |

---

## 9. Phased Implementation Plan

### Phase 1 — Backend Agent (no UI changes)
1. `llm_config.py` — config loading from env/file
2. `llm_client.py` — async `httpx` call, OpenAI-compatible, Anthropic adapter
3. `agentService.py` — `chat()` function: context gathering, prompt, parse, validate, relink
4. `POST /api/agent/chat` endpoint in `main.py`
5. `GET /api/agent/config` endpoint
6. `GET /api/agent/download/...` endpoint
7. Unit test: POST to `/api/agent/chat` with a real content stream + mock LLM response

### Phase 2 — Frontend Chat Pane
1. Add `AiChatPane.tsx` — chat history, input box, send button, download button
2. Add "Open AI Chat" toggle button to `ContentStreamPane.tsx`
3. Wire `App.tsx` to show the three-pane layout when AI pane is open
4. Add diff view (using `diff` npm package)
5. Add provider/model selector (populated from `GET /api/agent/config`)

### Phase 3 — Polish
1. Streaming responses (SSE from the agent endpoint for real-time token display)
2. "Undo" — revert to previous `.pdfs` version (keep per-turn snapshots in `AgentSession`)
3. Canvas preview auto-refresh after successful edit
4. Error recovery: agent re-prompts LLM if validation fails (up to 2 retries)
