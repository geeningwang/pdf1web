"""
Async LLM client — OpenAI-compatible REST calls with an Anthropic adapter.

Supports:
  - OpenAI chat completions  (style="openai")
  - Anthropic Messages API   (style="anthropic") via an adapter layer

No third-party LLM SDKs — uses httpx (already a transitive dependency).

Public API:
  async def complete(messages, config) -> str
      Send a chat-completion request and return the assistant's text.
"""
from __future__ import annotations

import json
import logging

import httpx

from .llm_config import LLMConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAI-style request / response
# ---------------------------------------------------------------------------

async def _complete_openai(messages: list[dict], cfg: LLMConfig) -> tuple[str, dict]:
    """POST to {base_url}/chat/completions with stream=true (OpenAI shape).

    Uses httpx.AsyncClient which integrates cleanly with uvicorn/asyncio —
    no aiohttp TimerContext/CancelledError conversion issues.

    Returns (content_text, debug_info).
    """
    import time
    url = f"{cfg.base_url}/chat/completions"
    prompt_chars = sum(len(m.get("content", "")) for m in messages)
    payload = {
        "model": cfg.model,
        "messages": messages,
        "max_tokens": cfg.max_tokens,
        "stream": True,
    }
    req_headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    if cfg.user_agent:
        req_headers["User-Agent"] = cfg.user_agent

    # connect=15s, read=None (each SSE chunk resets read; no per-read limit),
    # pool=5s, write=30s.  cfg.timeout is the total wall-clock limit.
    timeout = httpx.Timeout(connect=15, read=None, write=30, pool=5)
    t0 = time.monotonic()

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason = "?"
    usage: dict = {}

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload, headers=req_headers) as resp:
            if resp.status_code != 200:
                error_text = await resp.aread()
                error_str = error_text.decode("utf-8", errors="replace")
                elapsed = time.monotonic() - t0
                debug = {
                    "url": url, "model": cfg.model,
                    "prompt_chars": prompt_chars,
                    "elapsed": f"{elapsed:.1f}s",
                    "http_status": resp.status_code,
                    "raw_response": error_str[:1000],
                }
                raise RuntimeError(
                    f"LLM API error {resp.status_code}: {error_str[:500]}\n\n"
                    f"DEBUG: {json.dumps(debug, indent=2)}"
                )

            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    if delta.get("content"):
                        content_parts.append(delta["content"])
                    if delta.get("reasoning_content"):
                        reasoning_parts.append(delta["reasoning_content"])
                    fr = choices[0].get("finish_reason")
                    if fr:
                        finish_reason = fr
                if chunk.get("usage"):
                    usage = chunk["usage"]

    elapsed = time.monotonic() - t0
    content = "".join(content_parts) or "".join(reasoning_parts)
    debug_info = {
        "url": url,
        "model": cfg.model,
        "timeout_setting": f"connect=15 read=None total_wall={cfg.timeout}s",
        "elapsed": f"{elapsed:.1f}s",
        "prompt_chars": prompt_chars,
        "finish_reason": finish_reason,
        "usage": usage,
        "content_len": len("".join(content_parts)),
        "reasoning_len": len("".join(reasoning_parts)),
        "http_status": 200,
        "http_request_body": payload,
    }
    if not content:
        raise RuntimeError(
            f"LLM returned empty content (finish_reason={finish_reason}).\n\n"
            f"DEBUG:\n{json.dumps(debug_info, indent=2)}"
        )
    return content, debug_info


# ---------------------------------------------------------------------------
# Anthropic Messages API adapter
# ---------------------------------------------------------------------------

def _openai_to_anthropic(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Split a messages list into (system_prompt, user/assistant turns).

    Anthropic's /v1/messages API requires the system prompt to be a
    top-level field; user/assistant turns go in the `messages` array.
    """
    system: str | None = None
    turns: list[dict] = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "system":
            # Concatenate multiple system messages (unusual but safe)
            system = (system + "\n" + content) if system else content
        else:
            turns.append({"role": role, "content": content})
    return system, turns


async def _complete_anthropic(messages: list[dict], cfg: LLMConfig) -> tuple[str, dict]:
    """POST to Anthropic /v1/messages.  Returns (content_text, debug_info)."""
    import time
    system, turns = _openai_to_anthropic(messages)

    url = f"{cfg.base_url}/v1/messages"
    payload: dict = {
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "messages": turns,
    }
    if system:
        payload["system"] = system

    headers = {
        "x-api-key": cfg.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=cfg.timeout)
    t0 = time.monotonic()
    http_status = 0
    raw_response = ""
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            http_status = resp.status
            raw_response = await resp.text()
            if resp.status != 200:
                raise RuntimeError(
                    f"Anthropic API error {resp.status}: {raw_response[:500]}"
                )
            body = json.loads(raw_response)
    elapsed = time.monotonic() - t0
    try:
        content = ""
        for block in body["content"]:
            if block.get("type") == "text":
                content = block["text"]
                break
        if not content:
            raise RuntimeError("No text block in Anthropic response")
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Anthropic response shape: {body}") from exc
    debug_info = {
        "url": url,
        "model": cfg.model,
        "elapsed": f"{elapsed:.1f}s",
        "http_status": http_status,
        "http_request_body": payload,
        "finish_reason": body.get("stop_reason", "?"),
        "usage": body.get("usage", {}),
    }
    return content, debug_info


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def complete(messages: list[dict], cfg: LLMConfig) -> tuple[str, dict]:
    """Send *messages* to the configured LLM and return (assistant_text, debug_info).

    *messages* follows the OpenAI format:
      [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]

    Raises RuntimeError on API errors or unexpected response shapes.
    """
    logger.debug(
        "LLM call: provider=%s model=%s n_messages=%d",
        cfg.provider, cfg.model, len(messages),
    )
    if cfg.style == "anthropic":
        return await _complete_anthropic(messages, cfg)
    else:
        return await _complete_openai(messages, cfg)
