"""Checks on the DuckDB database: the sessions table is recomputed with pandas
from the Parquet files and compared session by session.
"""
import pandas as pd
import pytest

from aiusage.build_db import DB_PATH, connect
from aiusage.common import PROCESSED

PLATFORMS = ["claude_web", "chatgpt", "gemini", "claude_code", "codex"]

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run `python -m aiusage.build_db` first")


@pytest.fixture(scope="module")
def con():
    connection = connect(read_only=True)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def sql_sessions(con):
    return con.execute("SELECT * FROM sessions").df().set_index("session_id")


@pytest.fixture(scope="module")
def pandas_sessions():
    meta = pd.concat([pd.read_parquet(PROCESSED / f"sessions_meta_{p}.parquet") for p in PLATFORMS])
    msgs = pd.concat([pd.read_parquet(PROCESSED / f"messages_{p}.parquet") for p in PLATFORMS])
    human = msgs[msgs["is_human_input"]]
    out = pd.DataFrame({
        "user_turns": human.groupby("session_id")["msg_id"].nunique(),
        "user_tokens": human.groupby("session_id")["n_tokens"].sum(),
    })
    is_answer = (msgs["role"] == "assistant") & (msgs["kind"] == "text")
    out["assistant_tokens"] = msgs[is_answer].groupby("session_id")["n_tokens"].sum()
    out["tool_tokens"] = msgs[msgs["kind"].isin(["tool_use", "tool_result"])].groupby("session_id")["n_tokens"].sum()
    out["created_at"] = msgs.groupby("session_id")["ts"].min()
    out["first_user_message"] = human.sort_values("seq").groupby("session_id")["text"].first().str.slice(0, 200)
    out = out[out["user_turns"] >= 1].fillna({"assistant_tokens": 0, "tool_tokens": 0})
    return out.join(meta.set_index("session_id")[["platform"]])


def test_same_sessions(sql_sessions, pandas_sessions):
    assert sql_sessions.index.is_unique
    assert set(sql_sessions.index) == set(pandas_sessions.index)
    assert len(sql_sessions) == 1054


@pytest.mark.parametrize("column", ["user_turns", "user_tokens", "assistant_tokens", "tool_tokens"])
def test_numbers_match_pandas(sql_sessions, pandas_sessions, column):
    joined = pandas_sessions[[column]].join(sql_sessions[[column]], rsuffix="_sql")
    assert (joined[column].astype("int64") == joined[f"{column}_sql"].astype("int64")).all()


def test_first_message_and_start_match_pandas(sql_sessions, pandas_sessions):
    joined = pandas_sessions[["created_at", "first_user_message"]].join(
        sql_sessions[["created_at", "first_user_message"]], rsuffix="_sql")
    assert (joined["created_at"] == joined["created_at_sql"]).all()
    assert (joined["first_user_message"] == joined["first_user_message_sql"]).all()


def test_session_invariants(sql_sessions):
    assert (sql_sessions["created_at"] <= sql_sessions["last_message_at"]).all()
    assert (sql_sessions["duration_days"] >= 0).all()
    assert (sql_sessions["user_turns"] >= 1).all()
    assert sql_sessions["session_title"].notna().all()
    cli = sql_sessions["source"] == "cli"
    assert (sql_sessions.loc[cli, "session_type"] == "coding").all()
    assert (sql_sessions.loc[~cli, "session_type"] == "chat").all()


def test_monthly_shares_add_up(con):
    sql = (DB_PATH.parent.parent / "sql" / "analysis" / "04_monthly_ai_share.sql").read_text(encoding="utf-8")
    shares = con.execute(sql).df().groupby("month")["share_pct"].sum()
    assert shares.between(99.8, 100.2).all()


def test_cooccurrence_pairs_counted_once(con):
    sql = (DB_PATH.parent.parent / "sql" / "analysis" / "14_tag_cooccurrence.sql").read_text(encoding="utf-8")
    pairs = con.execute(sql).df()
    assert (pairs["tag_a"] < pairs["tag_b"]).all()
    assert not pairs.duplicated(["tag_a", "tag_b"]).any()
