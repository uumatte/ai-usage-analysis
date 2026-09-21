"""Checks on the Parquet files written by run_pipeline.py.

Invariants that must hold for any data, plus exact counts for the 2026-09-16
snapshot (derived independently of the parser code during exploration).
"""
import pandas as pd
import pytest

from aiusage.common import MESSAGE_COLUMNS, MAX_TOOL_TEXT, PROCESSED, SESSION_COLUMNS
from aiusage.parsers.claude_code import NOT_TYPED_BY_USER
from aiusage.parsers.codex import INJECTED_PREFIXES, SKIP_THREAD_SOURCES, excluded_thread_ids

PLATFORMS = ["claude_web", "chatgpt", "gemini", "claude_code", "codex"]


def load(platform):
    sessions = pd.read_parquet(PROCESSED / f"sessions_meta_{platform}.parquet")
    messages = pd.read_parquet(PROCESSED / f"messages_{platform}.parquet")
    return sessions, messages


# --- Invariants for every platform ---

@pytest.mark.parametrize("platform", PLATFORMS)
def test_columns(platform):
    sessions, messages = load(platform)
    assert list(sessions.columns) == SESSION_COLUMNS
    assert list(messages.columns) == MESSAGE_COLUMNS


@pytest.mark.parametrize("platform", PLATFORMS)
def test_keys_are_unique_and_linked(platform):
    sessions, messages = load(platform)
    assert sessions["session_id"].is_unique
    assert not messages.duplicated(["session_id", "msg_id", "block_idx"]).any()
    assert messages["session_id"].isin(sessions["session_id"]).all()
    assert sessions["session_id"].isin(messages["session_id"]).all()


@pytest.mark.parametrize("platform", PLATFORMS)
def test_values_are_valid(platform):
    sessions, messages = load(platform)
    assert set(messages["role"]) <= {"user", "assistant", "tool"}
    assert set(messages["kind"]) <= {"text", "thinking", "tool_use", "tool_result"}
    assert (messages.loc[messages["kind"] == "tool_result", "role"] == "tool").all()
    assert messages["ts"].notna().all()
    assert messages["ts"].between("2023-01-01", "2026-12-31").all()
    assert (messages["ts_local"] - messages["ts"] == pd.Timedelta(hours=8)).all()
    assert (messages["n_tokens"] >= 0).all()
    tools = messages[messages["kind"].isin(["tool_use", "tool_result"])]
    assert (tools["text"].str.len() <= MAX_TOOL_TEXT).all()
    assert set(sessions["source"]) == ({"cli"} if platform in ("claude_code", "codex") else {"web"})


@pytest.mark.parametrize("platform", PLATFORMS)
def test_human_input_is_user_text(platform):
    _, messages = load(platform)
    human = messages[messages["is_human_input"]]
    assert (human["role"] == "user").all()
    assert (human["kind"] == "text").all()
    assert (human["text"].str.strip() != "").all()


@pytest.mark.parametrize("platform", PLATFORMS)
def test_seq_is_ordered(platform):
    _, messages = load(platform)
    grouped = messages.sort_values(["session_id", "seq"]).groupby("session_id")
    assert grouped["seq"].apply(lambda s: list(s) == list(range(len(s)))).all()
    assert grouped["ts"].apply(lambda s: s.is_monotonic_increasing).all()


def test_same_schema_on_every_platform():
    """DuckDB reads all platforms with one wildcard, so the types must match."""
    import pyarrow.parquet as pq
    for prefix in ("sessions_meta", "messages"):
        schemas = {p: pq.read_schema(PROCESSED / f"{prefix}_{p}.parquet").remove_metadata()
                   for p in PLATFORMS}
        first = schemas[PLATFORMS[0]]
        assert all(s.equals(first) for s in schemas.values()), prefix


def test_session_ids_unique_across_platforms():
    ids = pd.concat([load(p)[0]["session_id"] for p in PLATFORMS])
    assert ids.is_unique


# --- Platform-specific data quality rules ---

def test_claude_web_counts():
    sessions, messages = load("claude_web")
    assert len(sessions) == 75            # 98 of 173 conversations are empty
    assert len(messages) == 1846          # current branch only
    assert messages["kind"].value_counts().to_dict() == {
        "text": 1212, "thinking": 274, "tool_use": 180, "tool_result": 180}
    assert messages["is_human_input"].sum() == 592


def test_chatgpt_branch_copies_removed():
    sessions, messages = load("chatgpt")
    assert len(sessions) == 685
    # a message id may belong to one conversation only
    assert (messages.groupby("msg_id")["session_id"].nunique() == 1).all()
    branch = sessions.loc[sessions["session_title"] == "Branch · 完善服务器需求", "session_id"].iloc[0]
    assert messages.loc[messages["session_id"] == branch, "msg_id"].nunique() == 12


def test_gemini_counts():
    sessions, messages = load("gemini")
    assert len(sessions) == 170
    assert messages["is_human_input"].sum() == 2773  # one per "Prompted" card


def test_claude_code_resumed_sessions_merged():
    sessions, messages = load("claude_code")
    assert len(sessions) == 64  # 168 transcript files
    assert (messages.groupby("msg_id")["session_id"].nunique() == 1).all()
    human = messages.loc[messages["is_human_input"], "text"].str.strip()
    assert not human.str.startswith(NOT_TYPED_BY_USER).any()


def test_session_tags():
    path = PROCESSED / "session_tags.parquet"
    if not path.exists():
        pytest.skip("run `python -m aiusage.tagging merge` first")
    tags = pd.read_parquet(path)
    all_sessions = pd.concat([load(p)[0]["session_id"] for p in PLATFORMS])
    assert tags["session_id"].isin(all_sessions).all()
    assert not tags.duplicated(["session_id", "tag"]).any()
    assert set(tags["tag_source"]) <= {"ai", "auto"}
    activity = tags[tags["tag_type"] == "activity"].groupby("session_id").size()
    assert (activity == 1).all()                      # exactly one activity tag
    assert tags["tag"].str.fullmatch(r"[a-z0-9:.\-]+").all()  # normalised spelling


def test_codex_excludes_copies_and_non_user_threads():
    sessions, messages = load("codex")
    assert len(sessions) == 66
    assert not sessions["session_id"].isin(excluded_thread_ids()).any()
    human = messages.loc[messages["is_human_input"], "text"].str.lstrip()
    assert not human.str.startswith(INJECTED_PREFIXES).any()
    assert SKIP_THREAD_SOURCES == {"guardian_review", "subagent"}
