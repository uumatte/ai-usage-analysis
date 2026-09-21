"""Backend for providers with an OpenAI-compatible Chat Completions API
(DeepSeek, Qwen, Kimi, GLM, Doubao, MiniMax, Gemini, or any other server).

Each provider has a preset because "compatible" is never exact: base URL, key
variable, best structured-output mode, output-length parameter, extra fields.
On a rejected request the mode steps down (json_schema -> json_object ->
prompt); inline reasoning and code fences are stripped and the parsed JSON is
checked against the schema here.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import openai

from aiusage.llm import ModelRefused, Usage

MAX_OUTPUT_TOKENS = 16000
JSON_MODES = ("json_schema", "json_object", "prompt")


@dataclass(frozen=True)
class Preset:
    base_url: str | None
    api_key_env: str
    json_mode: str                     # best structured-output mode the provider documents
    max_tokens_param: str | None       # "max_tokens", "max_completion_tokens" or None (leave default)
    extra_body: dict = field(default_factory=dict)
    example_model: str = ""
    notes: str = ""


PRESETS = {
    "deepseek": Preset(
        "https://api.deepseek.com", "DEEPSEEK_API_KEY", "json_object", "max_tokens",
        example_model="deepseek-v4-pro",
        notes="JSON mode only; the prompt must mention JSON (it does)."),
    "qwen": Preset(
        # legacy domain; workspace domains ({id}.<region>.maas.aliyuncs.com) are recommended
        "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", "json_schema", None,
        extra_body={"enable_thinking": False},
        example_model="qwen3.7-plus",
        notes="Keys only work in their own region. Strict schema on Qwen3.7/3.8 series; "
              "no max_tokens with structured output; thinking off for valid JSON."),
    "kimi": Preset(
        "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY", "json_schema", "max_completion_tokens",
        example_model="kimi-k2.6",
        notes="Mainland endpoint: https://api.moonshot.cn/v1"),
    "glm": Preset(
        "https://api.z.ai/api/paas/v4/", "ZAI_API_KEY", "json_object", "max_tokens",
        example_model="glm-5.3",
        notes="JSON mode only. Mainland endpoint: https://open.bigmodel.cn/api/paas/v4/"),
    "doubao": Preset(
        "https://ark.cn-beijing.volces.com/api/v3", "ARK_API_KEY", "json_schema", "max_completion_tokens",
        example_model="doubao-seed-2-1-pro-260915",
        notes="max_tokens defaults to 4096, so max_completion_tokens is set. "
              "BytePlus endpoint: https://ark.ap-southeast.bytepluses.com/api/v3"),
    "minimax": Preset(
        "https://api.minimax.io/v1", "MINIMAX_API_KEY", "prompt", "max_completion_tokens",
        example_model="MiniMax-M3",
        notes="No documented JSON mode; reasoning arrives as <think> in the content and is stripped. "
              "Mainland endpoint: https://api.minimax.cn/v1"),
    "gemini": Preset(
        "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "json_schema", None,
        example_model="gemini-3.8-flash",
        notes="Gemini API's OpenAI-compatibility endpoint (beta)."),
    "openai-compatible": Preset(
        None, "OPENAI_COMPATIBLE_API_KEY", "json_schema", "max_tokens",
        notes="Any other server: pass --base-url, --model and optionally --api-key-env."),
}

THINK_RE = re.compile(r"<think>.*?</think>", re.S)
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def extract_json(text: str):
    """Parse JSON from model text, tolerating inline reasoning and code fences."""
    text = THINK_RE.sub("", text or "").strip()
    text = FENCE_RE.sub("", text).strip()
    if not text.startswith(("{", "[")):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise RuntimeError("response contains no JSON object")
        text = text[start:end + 1]
    return json.loads(text)


def check_schema(value, schema: dict, path: str = "$") -> None:
    """Minimal JSON-schema check for the object/array/string schemas used in tagging."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise RuntimeError(f"{path}: expected object")
        for key in schema.get("required", []):
            if key not in value:
                raise RuntimeError(f"{path}: missing '{key}'")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                check_schema(value[key], sub, f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, list):
            raise RuntimeError(f"{path}: expected array")
        for i, item in enumerate(value):
            check_schema(item, schema.get("items", {}), f"{path}[{i}]")
    elif kind == "string" and not isinstance(value, str):
        raise RuntimeError(f"{path}: expected string")


class OpenAICompatibleBackend:
    errors = (openai.APIStatusError, openai.APIConnectionError)

    def __init__(self, provider: str, model: str, effort: str | None = None,
                 base_url: str | None = None, api_key_env: str | None = None, client=None):
        preset = PRESETS[provider]
        self.name = provider
        self.model = model
        self.effort = effort
        self.preset = preset
        self.mode = preset.json_mode
        if client is None:
            base_url = base_url or os.environ.get(f"{provider.upper().replace('-', '_')}_BASE_URL") or preset.base_url
            if not base_url:
                raise SystemExit(f"{provider}: pass --base-url")
            key_env = api_key_env or preset.api_key_env
            api_key = os.environ.get(key_env)
            if not api_key:
                raise SystemExit(f"{provider}: set the {key_env} environment variable (or pass --api-key-env)")
            client = openai.OpenAI(base_url=base_url, api_key=api_key, max_retries=5)
        self.client = client

    def _params(self, system: str, user: str, schema: dict, schema_name: str) -> dict:
        schema_text = json.dumps(schema, ensure_ascii=False)
        if self.mode != "json_schema":
            # JSON mode needs the word "JSON" in the prompt, and without a schema
            # parameter the model has to read the schema from the instructions.
            system = (f"{system}\n\nRespond with a single JSON object (no Markdown, no extra text) "
                      f"that matches this JSON schema:\n{schema_text}")
        params = dict(model=self.model,
                      messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        if self.mode == "json_schema":
            params["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": schema_name, "schema": schema, "strict": True}}
        elif self.mode == "json_object":
            params["response_format"] = {"type": "json_object"}
        if self.preset.max_tokens_param:
            params[self.preset.max_tokens_param] = MAX_OUTPUT_TOKENS
        if self.effort:
            params["reasoning_effort"] = self.effort
        if self.preset.extra_body:
            params["extra_body"] = dict(self.preset.extra_body)
        return params

    def _create(self, system, user, schema, schema_name):
        while True:
            try:
                return self.client.chat.completions.create(**self._params(system, user, schema, schema_name))
            except openai.BadRequestError:
                # The structured-output mode was probably rejected: step down once per mode.
                position = JSON_MODES.index(self.mode)
                if position == len(JSON_MODES) - 1:
                    raise
                self.mode = JSON_MODES[position + 1]
                print(f"    {self.name}: request rejected, retrying with structured output mode '{self.mode}'")

    def call_json(self, system: str, user: str, schema: dict, schema_name: str):
        response = self._create(system, user, schema, schema_name)
        choice = response.choices[0]
        message = choice.message

        if getattr(message, "refusal", None):
            raise ModelRefused(message.refusal)
        if choice.finish_reason in ("content_filter", "sensitive"):  # "sensitive" is Zhipu's filter reason
            raise ModelRefused(choice.finish_reason)
        if choice.finish_reason == "length":
            raise RuntimeError("response hit the output limit; send fewer sessions per request")
        if not message.content:
            raise RuntimeError("empty response")  # DeepSeek JSON mode can return empty content

        data = extract_json(message.content)
        check_schema(data, schema)

        u = response.usage
        details = getattr(u, "prompt_tokens_details", None)
        cache_read = (getattr(details, "cached_tokens", None)
                      or getattr(u, "prompt_cache_hit_tokens", None)  # DeepSeek's field name
                      or 0)
        usage = Usage(input_tokens=u.prompt_tokens, cache_read_tokens=cache_read,
                      cache_write_tokens=getattr(details, "cache_write_tokens", None) or 0,
                      output_tokens=u.completion_tokens)
        return data, usage
