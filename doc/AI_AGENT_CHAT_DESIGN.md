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
5. User types a request → server-side agent runs its inner loop → the pane
   streams each inner step in real time as it happens:

   ┌─ Agent working… ─────────────────────────────────────────────────┐
   │  ⚙  Round 1 — analysing content stream and building edit plan    │
   │  ↗  LLM call 1 sent                                              │
   │  ↙  LLM response 1 received                                      │
   │  ✗  Validation failed: BT/ET imbalance                           │
   │  ↺  Round 2 — asking LLM to fix the BT/ET error                 │
   │  ↗  LLM call 2 sent                                              │
   │  ↙  LLM response 2 received                                      │
   │  ✓  Validation passed                                            │
   │  🔗 Re-linking PDF…                                              │
   │  ✓  Done                                                         │
   └───────────────────────────────────────────────────────────────────┘
   Then the final result appears:
   - Agent's explanation of what changed
   - A diff of the modified content stream (old vs new)
   - A "Download PDF" button

6. User can continue chatting (multi-turn, accumulated edits)
7. User downloads the final PDF at any point
```

---

## 3. Architecture

```
Browser                         FastAPI Server                      LLM Provider
──────────────────              ──────────────────────────          ─────────────
TreePane                        
  │ select content stream       
  ▼                             
AiChatPane                      
  │ GET /api/agent/chat (SSE)  ──────────────────────────────►
  │ {upload_id, obj_num, gen,   AgentService — inner loop:
  │  message, history}          
  │                              ┌─ Round 1 ───────────────────────────────────┐
  │  ◄── SSE: step:thinking      │  build_prompt(context)                      │
  │  ◄── SSE: step:llm_request   │  call_llm()  ──────────────────────────────►│
  │  ◄── SSE: step:llm_response  │             ◄──────────────── LLM response  │
  │  ◄── SSE: step:validation    │  validate_pdfs()                             │
  │                              └─────────────────────────────────────────────┘
  │                              ┌─ Round 2 (retry on failure) ────────────────┐
  │  ◄── SSE: step:retry         │  build_retry_prompt(error)                  │
  │  ◄── SSE: step:llm_request   │  call_llm()  ──────────────────────────────►│
  │  ◄── SSE: step:llm_response  │             ◄──────────────── LLM response  │
  │  ◄── SSE: step:validation    │  validate_pdfs()  ✓                         │
  │                              └─────────────────────────────────────────────┘
  │  ◄── SSE: step:relink        relink_pdf()
  │  ◄── SSE: done               {reply, diff, download_url}
  │                             
  ▼                             
Render steps in real time       
Show diff, explanation          
Download modified PDF           
```

**Key design point**: the agent-to-LLM conversation is potentially multi-round. Each round is triggered by a validation failure, a clarification need, or a planning step. Every round — including the LLM request payload and response — is streamed to the browser as a Server-Sent Events (SSE) stream so the user sees the agent's full inner workings in real time.

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
// One step emitted by the server during the agent inner loop
interface AgentStep {
  type:
    | 'thinking'      // agent is planning / building prompt
    | 'llm_request'   // about to call LLM (shows round number)
    | 'llm_response'  // raw text from LLM (collapsible)
    | 'validation'    // validation result (pass/fail + detail)
    | 'retry'         // starting a new round due to failure
    | 'relink'        // re-linking PDF
  round?: number      // which agent-LLM round (1-based)
  status?: 'ok' | 'fail'
  text?: string       // human-readable description
  detail?: string     // collapsible raw content (LLM response text, error msg)
}

// One full turn in the user ↔ agent conversation
interface ChatTurn {
  role: 'user' | 'agent'
  // user turns:
  text?: string
  // agent turns:
  steps?: AgentStep[]         // inner loop steps, streamed in real time
  reply?: string              // final explanation (shown after steps)
  diff?: { old: string; new: string }
  downloadUrl?: string
  error?: string
}

interface AiChatPaneProps {
  uploadId: string
  objNum: number
  objGen: number
  currentPdfs: string    // current decoded content stream text
}
```

### 4.3 Agent Steps Display

Each agent turn renders its inner steps as a collapsible timeline **above** the final reply:

```
▼ Agent worked through 2 rounds  (click to expand/collapse)
  ⚙  Analysing content stream
  ↗  LLM call 1
  ↙  LLM responded  [expand to see raw response]
  ✗  Validation: BT/ET imbalance on line 14
  ↺  Retry — round 2
  ↗  LLM call 2
  ↙  LLM responded  [expand]
  ✓  Validation passed
  🔗 Re-linking PDF
─────────────────────────────────
I moved the Td operator from 72 720 to 72 690.
[--- diff ---]
[Download PDF]
```

Steps are appended to the DOM in real time as SSE events arrive. The step list auto-collapses after the `done` event fires, leaving only the reply + diff visible by default.

### 4.4 Diff View

Show an inline diff of the old vs. new content stream after each agent turn. Highlight changed lines in green/red. Use a lightweight diff library (`diff` npm package).

---

## 5. Backend API

### 5.1 `GET /api/agent/chat` — SSE stream

The chat endpoint uses **Server-Sent Events** so that every inner agent step is pushed to the browser as it happens. The browser opens a persistent GET connection; the server streams events until `done` or `error`.

**Query parameters**: `upload_id`, `obj_num`, `obj_gen`, `message`, `history` (JSON-encoded string), `provider` (optional), `model` (optional).

> Why GET + query params instead of POST + body for SSE?  
> The browser's native `EventSource` API only supports GET. For long messages, `history` is JSON-encoded and passed as a query parameter; for very long histories the client may fall back to a two-step pattern (POST to create a session token, then GET SSE with that token).

**SSE event types**:

```
# Agent planning
event: step
data: {"type": "thinking", "text": "Analysing the content stream…"}

# About to call LLM (round N)
event: step
data: {"type": "llm_request", "round": 1, "text": "Sending request to LLM (round 1)"}

# LLM response received — detail contains the raw LLM text (potentially long)
event: step
data: {"type": "llm_response", "round": 1, "text": "Response received", "detail": "<raw LLM text>"}

# Validation result
event: step
data: {"type": "validation", "status": "fail", "text": "BT/ET imbalance on line 14", "detail": "Found 3 BT and 2 ET"}

# Starting a retry round
event: step
data: {"type": "retry", "round": 2, "text": "Retrying — asking LLM to fix BT/ET imbalance"}

# Second LLM call / response
event: step
data: {"type": "llm_request", "round": 2, "text": "Sending request to LLM (round 2)"}
event: step
data: {"type": "llm_response", "round": 2, "text": "Response received", "detail": "<raw LLM text>"}

# Validation passed
event: step
data: {"type": "validation", "status": "ok", "text": "Content stream valid"}

# Re-linking
event: step
data: {"type": "relink", "text": "Re-linking PDF…"}

# Final result — connection closes after this
event: done
data: {
  "reply": "I moved the Td operator from '72 720 Td' to '72 690 Td'.",
  "new_pdfs": "BT\n/F1 12 Tf\n72 690 Td\n…",
  "diff": "--- old\n+++ new\n@@ -3 +3 @@\n-72 720 Td\n+72 690 Td",
  "download_url": "/api/agent/download/a12de1f8-.../12/0/modified.pdf"
}

# On unrecoverable error
event: error
data: {"error": "LLM returned no <pdfs> block after 3 rounds"}
```

If after the maximum number of rounds (default 3) the agent still cannot produce a valid content stream, it emits an `error` event with an explanation, and the user is invited to rephrase.

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

### 6.1 Agent Entry Point — async generator

The agent is implemented as an **async generator** that `yield`s `AgentEvent` objects. The FastAPI endpoint iterates the generator and forwards each event as an SSE frame.

```python
@dataclass
class AgentEvent:
    event: str        # 'step' | 'done' | 'error'
    data: dict        # serialised as JSON in the SSE data field

async def chat(
    upload_id: str,
    obj_num: int,
    obj_gen: int,
    user_message: str,
    history: list[dict],
    provider: str,
    model: str,
) -> AsyncGenerator[AgentEvent, None]:
    yield AgentEvent('step', {'type': 'thinking', 'text': 'Gathering context…'})
    # … inner loop …
    yield AgentEvent('done', {'reply': …, 'diff': …, 'download_url': …})
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

### 6.5 Inner Loop

The core of the agent is a loop that runs up to `MAX_ROUNDS` (default 3) times:

```
for round in 1..MAX_ROUNDS:
    yield step(thinking / retry, round)
    messages = build_messages(context, history, user_message, prior_errors)
    yield step(llm_request, round)
    llm_text = await call_llm(messages)
    yield step(llm_response, round, detail=llm_text)
    new_pdfs, explanation = parse_response(llm_text)
    if new_pdfs is None:
        prior_errors.append('No <pdfs> block in response')
        yield step(validation, fail, 'No <pdfs> block found')
        continue
    errors = validate(new_pdfs)
    if errors:
        prior_errors.append(errors)
        yield step(validation, fail, errors)
        continue
    # success
    yield step(validation, ok)
    break
else:
    yield error('Could not produce valid content stream after MAX_ROUNDS rounds')
    return
```

On each retry, `prior_errors` is included in the next prompt so the LLM knows exactly what it got wrong previously.

### 6.6 Response Parsing

1. Extract content between `<pdfs>` and `</pdfs>` tags — this is the new stream.
2. Extract explanation text after the `</pdfs>` closing tag.
3. If no `<pdfs>` block found, treat the entire response as a failure and add to `prior_errors`.

### 6.7 Validation

After extracting the new stream:
- Check BT/ET balance (equal counts)
- Check q/Q balance
- Check no binary / null bytes
- Attempt to tokenize with the existing `pdf.tokenizer` — catch parse errors

If validation fails: append the error to `prior_errors` and continue the inner loop (retry).

### 6.8 Re-linking

1. Export the original PDF to a temp PDFX dir (or reuse the session PDFX dir).
2. Overwrite `objects/obj_{NNNNN}_{G}.pdfs` with the new stream.
3. Call `link_pdf(pdfx_dir, out_path)` — the modified object will be detected as changed (sha256 mismatch) and re-serialized with re-encoding.
4. Store `out_path` in the session for the download endpoint.

### 6.9 Session State (server-side)

```python
# In _sessions or a parallel dict:
_agent_sessions: dict[str, AgentSession] = {}

@dataclass
class AgentSession:
    upload_id: str
    pdfx_dir: Path            # temp PDFX dir, kept alive for multi-turn
    current_pdf: Path         # latest linked PDF
    active_obj: tuple[int, int] | None
    pdfs_history: list[str]   # per-turn snapshots for undo
```

The PDFX dir is created on first chat request for an `upload_id` and reused for subsequent turns. Each successful edit appends the previous `.pdfs` content to `pdfs_history` (enabling undo). The session is deleted when the upload session is evicted.

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
3. `agentService.py` — async-generator `chat()`: context gathering, inner loop (multi-round LLM calls, validation, retry), relink, yield `AgentEvent` per step
4. `GET /api/agent/chat` SSE endpoint in `main.py` — iterates the generator and forwards events
5. `GET /api/agent/config` endpoint
6. `GET /api/agent/download/...` endpoint
7. Test: hit `/api/agent/chat` with a real content stream + mock LLM that returns a bad response on round 1, good on round 2 — verify all SSE steps arrive

### Phase 2 — Frontend Chat Pane
1. Add `AiChatPane.tsx`:
   - `EventSource` SSE consumer
   - Per-turn step timeline (thinking → llm_request → llm_response → validation → retry → …)
   - Steps auto-collapse on `done` event
   - Final reply + diff view (`diff` npm package)
   - Download button
2. Add "Open AI Chat" toggle button to `ContentStreamPane.tsx`
3. Wire `App.tsx` to show the three-pane layout when AI pane is open
4. Add provider/model selector (populated from `GET /api/agent/config`)

### Phase 3 — Polish
1. "Undo" button — revert to previous `.pdfs` using `pdfs_history` in `AgentSession`
2. Canvas preview auto-refresh after successful edit
3. Expand/collapse individual LLM response details in the step timeline
4. Show token usage / latency per round in the step timeline
