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

import aiohttp

from .llm_config import LLMConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAI-style request / response
# ---------------------------------------------------------------------------

async def _complete_openai(messages: list[dict], cfg: LLMConfig) -> tuple[str, dict]:
    """POST to {base_url}/chat/completions with stream=true (OpenAI shape).

    Streaming is used so that reasoning-model "think" time does not trigger
    a socket read timeout — each token chunk keeps the socket alive.

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

    # Large total timeout; stream keeps socket alive so individual reads are fast
    timeout = aiohttp.ClientTimeout(sock_connect=15, total=cfg.timeout)
    t0 = time.monotonic()

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason = "?"
    usage: dict = {}

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=req_headers) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                elapsed = time.monotonic() - t0
                debug = {
                    "url": url, "model": cfg.model,
                    "prompt_chars": prompt_chars,
                    "elapsed": f"{elapsed:.1f}s",
                    "http_status": resp.status,
                    "raw_response": error_text[:1000],
                }
                raise RuntimeError(
                    f"LLM API error {resp.status}: {error_text[:500]}\n\n"
                    f"DEBUG: {json.dumps(debug, indent=2)}"
                )

            async for raw_line in resp.content:
                line = raw_line.decode("utf-8").strip()
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
        "timeout_setting": f"total={cfg.timeout}s (streaming)",
        "elapsed": f"{elapsed:.1f}s",
        "prompt_chars": prompt_chars,
        "finish_reason": finish_reason,
        "usage": usage,
        "content_len": len("".join(content_parts)),
        "reasoning_len": len("".join(reasoning_parts)),
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


async def _complete_anthropic(messages: list[dict], cfg: LLMConfig) -> str:
    """POST to Anthropic /v1/messages."""
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
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(
                    f"Anthropic API error {resp.status}: {text[:500]}"
                )
            body = json.loads(text)
    try:
        # content is a list of blocks; grab the first text block
        for block in body["content"]:
            if block.get("type") == "text":
                return block["text"]
        raise RuntimeError("No text block in Anthropic response")
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Anthropic response shape: {body}") from exc


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
        text = await _complete_anthropic(messages, cfg)
        return text, {"style": "anthropic"}
    else:
        return await _complete_openai(messages, cfg)
