"""Claude (Anthropic API) backend: JSON-schema output, cached system prompt,
server-side fallback on a refusal. Credentials: ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import json

import anthropic

from aiusage.llm import ModelRefused, Usage

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "medium"   # tagging is classification; "high" if quality needs it
MAX_TOKENS = 16000
BETAS = ["server-side-fallback-2026-07-01"]


class ClaudeBackend:
    name = "claude"
    # SDK errors worth skipping a chunk for (rate limits and 5xx are retried by the SDK first)
    errors = (anthropic.APIStatusError, anthropic.APIConnectionError)

    def __init__(self, model: str | None = None, effort: str | None = None, client=None):
        self.model = model or DEFAULT_MODEL
        self.effort = effort or DEFAULT_EFFORT
        self.client = client or anthropic.Anthropic(max_retries=5)

    def call_json(self, system: str, user: str, schema: dict, schema_name: str):
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            betas=BETAS,
            fallbacks="default",
            output_config={"effort": self.effort,
                           "format": {"type": "json_schema", "schema": schema}},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        # Check why the model stopped before reading the content.
        if response.stop_reason == "refusal":
            raise ModelRefused(getattr(response.stop_details, "category", None))
        if response.stop_reason == "max_tokens":
            raise RuntimeError("response hit max_tokens; send fewer sessions per request")

        text = next(block.text for block in response.content if block.type == "text")
        u = response.usage
        cache_read = u.cache_read_input_tokens or 0
        cache_write = u.cache_creation_input_tokens or 0
        usage = Usage(
            input_tokens=u.input_tokens + cache_read + cache_write,  # Anthropic reports these separately
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            output_tokens=u.output_tokens,
        )
        return json.loads(text), usage
