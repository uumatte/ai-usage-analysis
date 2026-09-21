"""Offline tests for API tagging: fake clients, no network, no cost.
"""
import json
from types import SimpleNamespace as NS

import openai
import pytest

from aiusage import tag_api
from aiusage.llm import COMPATIBLE_PROVIDERS, ModelRefused, Usage, get_backend
from aiusage.llm.claude_backend import ClaudeBackend
from aiusage.llm.openai_backend import OpenAIBackend
from aiusage.llm.openai_compatible_backend import PRESETS, OpenAICompatibleBackend, extract_json

SCHEMA = tag_api.TAGS_SCHEMA
ANSWER = {"sessions": [{"session_id": "a", "tags": ["lookup", "history"]}]}


# --- Claude backend ---

class FakeAnthropic:
    def __init__(self, response):
        self.response, self.kwargs = response, None
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def claude_response(stop_reason="end_turn", text=json.dumps(ANSWER)):
    return NS(stop_reason=stop_reason, stop_details=NS(category="cyber"),
              content=[NS(type="thinking", thinking=""), NS(type="text", text=text)],
              usage=NS(input_tokens=100, cache_read_input_tokens=5000, cache_creation_input_tokens=0,
                       output_tokens=40))


def test_claude_request_and_response():
    fake = FakeAnthropic(claude_response())
    data, usage = ClaudeBackend(client=fake).call_json("SYSTEM", "USER", SCHEMA, "session_tags")
    assert data == ANSWER
    assert usage == Usage(input_tokens=5100, cache_read_tokens=5000, cache_write_tokens=0, output_tokens=40)
    kw = fake.kwargs
    assert kw["model"] == "claude-opus-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["messages"] == [{"role": "user", "content": "USER"}]


def test_claude_refusal_and_truncation():
    with pytest.raises(ModelRefused):
        ClaudeBackend(client=FakeAnthropic(claude_response("refusal"))).call_json("S", "U", SCHEMA, "x")
    with pytest.raises(RuntimeError):
        ClaudeBackend(client=FakeAnthropic(claude_response("max_tokens"))).call_json("S", "U", SCHEMA, "x")


# --- OpenAI backend ---

class FakeOpenAI:
    def __init__(self, response):
        self.response, self.kwargs = response, None
        self.responses = NS(create=self.create)

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def openai_response(status="completed", reason=None, content=None):
    content = content or [NS(type="output_text", text=json.dumps(ANSWER))]
    return NS(status=status, incomplete_details=NS(reason=reason),
              output=[NS(type="reasoning"), NS(type="message", content=content)],
              output_text=json.dumps(ANSWER),
              usage=NS(input_tokens=5100, output_tokens=40,
                       input_tokens_details=NS(cached_tokens=5000, cache_write_tokens=0)))


def test_openai_request_and_response():
    fake = FakeOpenAI(openai_response())
    data, usage = OpenAIBackend(model="some-model", client=fake).call_json("SYSTEM", "USER", SCHEMA, "session_tags")
    assert data == ANSWER
    assert usage == Usage(input_tokens=5100, cache_read_tokens=5000, cache_write_tokens=0, output_tokens=40)
    kw = fake.kwargs
    assert kw["model"] == "some-model" and kw["instructions"] == "SYSTEM" and kw["input"] == "USER"
    assert kw["text"]["format"] == {"type": "json_schema", "name": "session_tags", "schema": SCHEMA, "strict": True}
    assert kw["store"] is False
    assert "reasoning" not in kw  # only sent when an effort is chosen


def test_openai_effort_refusal_and_truncation():
    fake = FakeOpenAI(openai_response())
    OpenAIBackend(model="m", effort="low", client=fake).call_json("S", "U", SCHEMA, "x")
    assert fake.kwargs["reasoning"] == {"effort": "low"}

    refusal = openai_response(content=[NS(type="refusal", refusal="I can't help with that.")])
    with pytest.raises(ModelRefused):
        OpenAIBackend(model="m", client=FakeOpenAI(refusal)).call_json("S", "U", SCHEMA, "x")
    with pytest.raises(ModelRefused):
        OpenAIBackend(model="m", client=FakeOpenAI(openai_response("incomplete", "content_filter"))).call_json("S", "U", SCHEMA, "x")
    with pytest.raises(RuntimeError):
        OpenAIBackend(model="m", client=FakeOpenAI(openai_response("incomplete", "max_output_tokens"))).call_json("S", "U", SCHEMA, "x")


# --- OpenAI-compatible backend (DeepSeek, Qwen, Kimi, GLM, Doubao, MiniMax, Gemini) ---

class FakeChat:
    """Records every call; `replies` is a list of responses or exceptions, used in order."""
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def chat_response(content=json.dumps(ANSWER), finish_reason="stop", refusal=None, usage=None):
    usage = usage or NS(prompt_tokens=5100, completion_tokens=40,
                        prompt_tokens_details=NS(cached_tokens=5000, cache_write_tokens=0))
    return NS(choices=[NS(finish_reason=finish_reason, message=NS(content=content, refusal=refusal))], usage=usage)


def bad_request():
    import httpx2
    response = httpx2.Response(400, request=httpx2.Request("POST", "https://example.invalid"))
    return openai.BadRequestError("response_format not supported", response=response, body=None)



def test_every_compatible_provider_has_a_complete_preset():
    assert set(COMPATIBLE_PROVIDERS) == set(PRESETS)
    for name, preset in PRESETS.items():
        assert preset.json_mode in ("json_schema", "json_object", "prompt"), name
        assert preset.max_tokens_param in ("max_tokens", "max_completion_tokens", None), name
        assert preset.api_key_env.isupper(), name
        if name != "openai-compatible":
            assert preset.base_url.startswith("https://"), name


@pytest.mark.parametrize("provider, format_type, token_param", [
    ("kimi", "json_schema", "max_completion_tokens"),
    ("gemini", "json_schema", None),
    ("deepseek", "json_object", "max_tokens"),
    ("glm", "json_object", "max_tokens"),
    ("minimax", None, "max_completion_tokens"),
])
def test_compatible_request_follows_preset(provider, format_type, token_param):
    fake = FakeChat(chat_response())
    data, usage = OpenAICompatibleBackend(provider, model="m", client=fake).call_json("SYSTEM", "USER", SCHEMA, "session_tags")
    assert data == ANSWER and usage.cache_read_tokens == 5000
    kw = fake.calls[0]
    assert kw.get("response_format", {}).get("type") == format_type
    for param in ("max_tokens", "max_completion_tokens"):
        assert (param in kw) == (param == token_param)
    system = kw["messages"][0]["content"]
    if format_type != "json_schema":
        assert "JSON" in system and '"session_id"' in system  # JSON mode needs the word and the schema


def test_qwen_turns_thinking_off_and_sets_no_token_limit():
    fake = FakeChat(chat_response())
    OpenAICompatibleBackend("qwen", model="m", client=fake).call_json("S", "U", SCHEMA, "x")
    assert fake.calls[0]["extra_body"] == {"enable_thinking": False}
    assert "max_tokens" not in fake.calls[0] and "max_completion_tokens" not in fake.calls[0]


def test_rejected_json_schema_falls_back_to_json_mode_and_remembers():
    fake = FakeChat(bad_request(), chat_response(), chat_response())
    backend = OpenAICompatibleBackend("kimi", model="m", client=fake)
    backend.call_json("S", "U", SCHEMA, "x")
    backend.call_json("S", "U", SCHEMA, "x")
    types = [call.get("response_format", {}).get("type") for call in fake.calls]
    assert types == ["json_schema", "json_object", "json_object"]


def test_think_blocks_and_fences_are_stripped():
    text = "<think>let me see</think>\n```json\n" + json.dumps(ANSWER) + "\n```"
    assert extract_json(text) == ANSWER
    fake = FakeChat(chat_response(content=text))
    data, _ = OpenAICompatibleBackend("minimax", model="m", client=fake).call_json("S", "U", SCHEMA, "x")
    assert data == ANSWER


def test_compatible_errors_filters_truncation_and_bad_json():
    def call(response, provider="glm"):
        return OpenAICompatibleBackend(provider, model="m", client=FakeChat(response)).call_json("S", "U", SCHEMA, "x")
    with pytest.raises(ModelRefused):
        call(chat_response(finish_reason="sensitive"))
    with pytest.raises(ModelRefused):
        call(chat_response(refusal="no"))
    with pytest.raises(RuntimeError):
        call(chat_response(finish_reason="length"))
    with pytest.raises(RuntimeError):
        call(chat_response(content=""))
    with pytest.raises(RuntimeError):  # valid JSON, wrong shape
        call(chat_response(content=json.dumps({"sessions": [{"session_id": "a"}]})))


def test_deepseek_cache_field_is_read():
    usage = NS(prompt_tokens=900, completion_tokens=10, prompt_tokens_details=None, prompt_cache_hit_tokens=800)
    fake = FakeChat(chat_response(usage=usage))
    _, u = OpenAICompatibleBackend("deepseek", model="m", client=fake).call_json("S", "U", SCHEMA, "x")
    assert u.cache_read_tokens == 800 and u.input_tokens == 900


def test_get_backend_explains_missing_model_and_key(monkeypatch):
    with pytest.raises(SystemExit, match="--model deepseek-v4-pro"):
        get_backend("deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="DEEPSEEK_API_KEY"):
        get_backend("deepseek", model="deepseek-v4-pro")


# --- Provider-neutral core ---

class ScriptedBackend:
    """Answers from a function of the requested session ids."""
    name, model = "fake", "fake-model"
    errors = (ConnectionError,)

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def call_json(self, system, user, schema, schema_name):
        ids = [json.loads(line)["session_id"] for line in user.splitlines()[2:]]
        self.calls.append(ids)
        return self.answer(ids, len(self.calls)), Usage(input_tokens=10, output_tokens=5)


def rows(n):
    return [{"session_id": f"s{i}", "title": "t", "prompts": ["p"]} for i in range(n)]


def test_missing_sessions_are_retried_once():
    # first call leaves out s1; the retry answers it
    def answer(ids, call_no):
        return {"sessions": [{"session_id": i, "tags": ["chat"]} for i in ids if not (call_no == 1 and i == "s1")]}
    backend = ScriptedBackend(answer)
    result = tag_api.tag_batch(backend, "SYSTEM", rows(3), {}, tag_api.new_totals())
    assert set(result) == {"s0", "s1", "s2"}
    assert backend.calls[-1] == ["s1"]


def test_failed_chunks_are_skipped_not_fatal():
    class Failing(ScriptedBackend):
        def call_json(self, *args):
            raise ModelRefused("test")
    totals = tag_api.new_totals()
    result = tag_api.tag_batch(Failing(None), "SYSTEM", rows(3), {}, totals)
    assert result == {} and totals["failed_chunks"] == 2  # first attempt + retry


def test_tag_all_resumes_and_writes_merge_format(tmp_path, monkeypatch):
    inputs, outputs = tmp_path / "inputs", tmp_path / "outputs"
    inputs.mkdir()
    (inputs / "batch_01.jsonl").write_text("\n".join(json.dumps(r) for r in rows(5)), encoding="utf-8")
    library = tmp_path / "tag_library.json"
    library.write_text(json.dumps([{"tag": "history", "description": "d"}]), encoding="utf-8")
    monkeypatch.setattr(tag_api, "INPUTS", inputs)
    monkeypatch.setattr(tag_api, "OUTPUTS", outputs)
    monkeypatch.setattr(tag_api, "LIBRARY", library)
    outputs.mkdir()
    (outputs / "batch_01.json").write_text(json.dumps({"s0": ["chat"], "s1": ["chat"]}), encoding="utf-8")

    backend = ScriptedBackend(lambda ids, _: {"sessions": [{"session_id": i, "tags": ["lookup", "history"]} for i in ids]})
    tag_api.tag_all(backend)

    assert backend.calls == [["s2", "s3", "s4"]]  # only the missing sessions were requested
    written = json.loads((outputs / "batch_01.json").read_text(encoding="utf-8"))
    assert list(written) == ["s0", "s1", "s2", "s3", "s4"]  # input order, same format merge reads
    assert written["s0"] == ["chat"] and written["s4"] == ["lookup", "history"]
