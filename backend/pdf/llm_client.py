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

async def _complete_openai(messages: list[dict], cfg: LLMConfig) -> str:
    """POST to {base_url}/chat/completions (OpenAI shape).

    base_url is expected to already include the version prefix, e.g.
    https://api.openai.com/v1 or https://api.moonshot.cn/v1.
    """
    url = f"{cfg.base_url}/chat/completions"
    payload = {
        "model": cfg.model,
        "messages": messages,
        "max_tokens": cfg.max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=cfg.timeout)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(
                    f"LLM API error {resp.status}: {text[:500]}"
                )
            body = json.loads(text)
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected LLM response shape: {body}") from exc


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

async def complete(messages: list[dict], cfg: LLMConfig) -> str:
    """Send *messages* to the configured LLM and return the assistant text.

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
