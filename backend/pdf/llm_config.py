"""
LLM configuration loader.

Priority order:
  1. Environment variables (LLM_PROVIDER, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, …)
  2. backend/llm_config.json (gitignored)

Exposes:
  get_llm_config()           → LLMConfig for the active provider
  list_providers()           → list[ProviderInfo] for /api/agent/config
  KNOWN_PROVIDERS            → built-in provider metadata
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

_CONFIG_FILE = Path(__file__).parent.parent / "llm_config.json"

# ---------------------------------------------------------------------------
# Built-in provider metadata (display info + default base URLs)
# ---------------------------------------------------------------------------

KNOWN_PROVIDERS: dict[str, dict] = {
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"],
        "default_model": "gpt-4o-mini",
        "style": "openai",          # request / response shape
    },
    "anthropic": {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"],
        "default_model": "claude-opus-4-5",
        "style": "anthropic",
    },
    "kimi": {
        "name": "Kimi (Moonshot AI)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "default_model": "moonshot-v1-8k",
        "style": "openai",          # Kimi is OpenAI-compatible
    },
    "custom": {
        "name": "Custom (OpenAI-compatible)",
        "base_url": "",
        "models": [],
        "default_model": "",
        "style": "openai",
    },
}


@dataclass
class LLMConfig:
    provider: str
    style: str          # "openai" | "anthropic"
    api_key: str
    base_url: str
    model: str
    max_tokens: int = 4096
    timeout: int = 60


@dataclass
class ProviderInfo:
    id: str
    name: str
    models: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_file_config() -> dict:
    """Load llm_config.json if it exists; return empty dict otherwise."""
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def get_llm_config(provider: str | None = None, model: str | None = None) -> LLMConfig:
    """Return the LLMConfig to use for a request.

    If *provider* / *model* are given (e.g. from query params) they override
    the defaults.  If a required value (api_key) is missing a ValueError is
    raised so the caller can return a 400/503.
    """
    file_cfg = _load_file_config()

    # --- Determine provider ---
    if not provider:
        provider = (
            os.environ.get("LLM_PROVIDER")
            or file_cfg.get("default_provider")
            or "openai"
        )

    meta = KNOWN_PROVIDERS.get(provider, KNOWN_PROVIDERS["custom"])
    file_prov = file_cfg.get("providers", {}).get(provider, {})

    # --- api_key ---
    api_key = (
        os.environ.get("LLM_API_KEY")
        or file_prov.get("api_key")
        or ""
    )

    # --- base_url ---
    base_url = (
        os.environ.get("LLM_BASE_URL")
        or file_prov.get("base_url")
        or meta["base_url"]
    )

    # --- model ---
    if not model:
        model = (
            os.environ.get("LLM_MODEL")
            or file_prov.get("default_model")
            or meta["default_model"]
            or ""
        )

    max_tokens = int(
        os.environ.get("LLM_MAX_TOKENS")
        or file_prov.get("max_tokens")
        or 4096
    )
    timeout = int(
        os.environ.get("LLM_TIMEOUT")
        or file_prov.get("timeout")
        or 60
    )

    if not api_key:
        raise ValueError(
            f"No API key configured for provider '{provider}'. "
            "Set LLM_API_KEY env var or add it to backend/llm_config.json."
        )

    return LLMConfig(
        provider=provider,
        style=meta.get("style", "openai"),
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        model=model,
        max_tokens=max_tokens,
        timeout=timeout,
    )


def list_providers() -> list[ProviderInfo]:
    """Return provider info for /api/agent/config.

    Merges KNOWN_PROVIDERS with any extra models listed in llm_config.json.
    Does not expose API keys.
    """
    file_cfg = _load_file_config()
    file_provs = file_cfg.get("providers", {})

    infos: list[ProviderInfo] = []
    seen: set[str] = set()

    for pid, meta in KNOWN_PROVIDERS.items():
        models = list(meta["models"])
        fp = file_provs.get(pid, {})
        for m in fp.get("extra_models", []):
            if m not in models:
                models.append(m)
        infos.append(ProviderInfo(id=pid, name=meta["name"], models=models))
        seen.add(pid)

    # Also add any completely custom providers from the config file
    for pid, fp in file_provs.items():
        if pid in seen:
            continue
        infos.append(ProviderInfo(
            id=pid,
            name=fp.get("name", pid),
            models=fp.get("models", []),
        ))

    return infos
