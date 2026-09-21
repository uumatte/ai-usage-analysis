"""Model backends. Each exposes call_json(system, user, schema, name) -> (dict, Usage).

    claude   claude_backend.py             Anthropic SDK
    openai   openai_backend.py             OpenAI Responses API
    others   openai_compatible_backend.py  Chat Completions + per-provider presets
"""
from __future__ import annotations

import os
from dataclasses import dataclass

COMPATIBLE_PROVIDERS = ("deepseek", "qwen", "kimi", "glm", "doubao", "minimax", "gemini", "openai-compatible")
PROVIDERS = ("claude", "openai") + COMPATIBLE_PROVIDERS


@dataclass
class Usage:
    """Usage of one request. input_tokens is the total, including the cached parts."""
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0


class ModelRefused(Exception):
    """The provider declined the request (safety refusal or content filter)."""


def get_backend(provider: str, model: str | None = None, effort: str | None = None,
                base_url: str | None = None, api_key_env: str | None = None):
    """Create the backend for ``provider``. SDKs are imported only when needed."""
    if provider == "claude":
        from aiusage.llm.claude_backend import ClaudeBackend
        return ClaudeBackend(model=model, effort=effort)

    if provider == "openai":
        from aiusage.llm.openai_backend import OpenAIBackend
        model = model or os.environ.get("OPENAI_MODEL")
        if not model:
            raise SystemExit("choose an OpenAI model with --model or the OPENAI_MODEL environment variable")
        return OpenAIBackend(model=model, effort=effort)

    if provider in COMPATIBLE_PROVIDERS:
        from aiusage.llm.openai_compatible_backend import PRESETS, OpenAICompatibleBackend
        if not model:
            example = PRESETS[provider].example_model
            hint = f", e.g. --model {example}" if example else ""
            raise SystemExit(f"{provider}: choose a model with --model{hint} "
                             "(model names change often; check the provider's model list)")
        return OpenAICompatibleBackend(provider, model=model, effort=effort,
                                       base_url=base_url, api_key_env=api_key_env)

    raise SystemExit(f"unknown provider {provider!r}; choose one of {PROVIDERS}")
