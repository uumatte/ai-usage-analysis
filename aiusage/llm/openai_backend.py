"""OpenAI backend (Responses API): strict JSON-schema output, store=False.
Credentials: OPENAI_API_KEY; the model has no default, pass --model.
"""
from __future__ import annotations

import json

import openai

from aiusage.llm import ModelRefused, Usage

MAX_OUTPUT_TOKENS = 16000
PROMPT_CACHE_KEY = "aiusage-tagging"


class OpenAIBackend:
    name = "openai"
    # SDK errors worth skipping a chunk for (rate limits and 5xx are retried by the SDK first)
    errors = (openai.APIStatusError, openai.APIConnectionError)

    def __init__(self, model: str, effort: str | None = None, client=None):
        self.model = model
        self.effort = effort  # only for reasoning models; left out when None
        self.client = client or openai.OpenAI(max_retries=5)

    def call_json(self, system: str, user: str, schema: dict, schema_name: str):
        params = dict(
            model=self.model,
            instructions=system,
            input=user,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            text={"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
            prompt_cache_key=PROMPT_CACHE_KEY,
            store=False,
        )
        if self.effort:
            params["reasoning"] = {"effort": self.effort}
        response = self.client.responses.create(**params)

        if response.status == "incomplete":
            reason = getattr(response.incomplete_details, "reason", None)
            if reason == "content_filter":
                raise ModelRefused("content_filter")
            raise RuntimeError(f"incomplete response ({reason}); send fewer sessions per request")
        for item in response.output:
            if item.type == "message":
                for content in item.content:
                    if content.type == "refusal":
                        raise ModelRefused(content.refusal)

        u = response.usage
        usage = Usage(
            input_tokens=u.input_tokens,  # OpenAI's count already includes cached tokens
            cache_read_tokens=u.input_tokens_details.cached_tokens or 0,
            cache_write_tokens=getattr(u.input_tokens_details, "cache_write_tokens", 0) or 0,
            output_tokens=u.output_tokens,
        )
        return json.loads(response.output_text), usage
