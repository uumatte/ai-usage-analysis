"""Shared helpers for the parsers: the column contract, time and token
helpers, and save() which writes one platform's two Parquet files.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import tiktoken

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "Data" / "raw"
PROCESSED = ROOT / "Data" / "processed"

# Hong Kong has no daylight saving time, so a fixed +8h offset is exact.
LOCAL_OFFSET = timedelta(hours=8)

MAX_TOOL_TEXT = 2000    # tool text is stored truncated, but counted in full
TOKENIZE_PREFIX = 50_000  # longer texts are tokenised on a prefix and scaled up

SESSION_COLUMNS = [
    "session_id",
    "platform",            # chatgpt / claude_web / gemini / claude_code / codex
    "source",              # web / cli
    "session_ai",          # chatgpt / claude / gemini (the vendor)
    "session_model",
    "session_title",
    "project_path",        # CLI only
    "record_count",        # raw records/nodes in the export
    "api_context_tokens",  # CLI only: real context size of the last turn
    "api_output_tokens",   # CLI only: real output tokens reported by the API
]

MESSAGE_COLUMNS = [
    "session_id",
    "platform",
    "msg_id",          # id of the original message/record in the export
    "block_idx",       # position of this block inside that message
    "seq",             # order inside the session, 0-based
    "ts",              # UTC, timezone-naive
    "ts_local",        # Hong Kong time, timezone-naive
    "role",            # user / assistant / tool
    "kind",            # text / thinking / tool_use / tool_result
    "is_human_input",  # True only for text the user actually typed
    "text",
    "n_chars",
    "n_tokens",        # tiktoken o200k_base estimate, same for every platform
]

_ENCODER = tiktoken.get_encoding("o200k_base")


# --- Row builders ---

def session_row(**values) -> dict:
    """Build a session dict, filling unspecified columns with None."""
    unknown = set(values) - set(SESSION_COLUMNS)
    if unknown:
        raise KeyError(f"unknown session columns: {unknown}")
    return {col: values.get(col) for col in SESSION_COLUMNS}


def message_row(session_id, platform, msg_id, block_idx, ts, role, kind, text,
                is_human_input=False) -> dict:
    """Build a message dict. seq/ts_local/n_chars/n_tokens are added by save()."""
    return {
        "session_id": session_id,
        "platform": platform,
        "msg_id": str(msg_id),
        "block_idx": block_idx,
        "ts": ts,
        "role": role,
        "kind": kind,
        "is_human_input": bool(is_human_input),
        "text": text or "",
    }


# --- Small conversions ---

def to_naive_utc(dt: datetime) -> datetime:
    """Convert an aware datetime to naive UTC (naive input is assumed UTC)."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_iso(value: str) -> datetime:
    """'2026-09-08T12:33:46.172233Z' -> naive UTC datetime."""
    return to_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def from_epoch(seconds: float) -> datetime:
    """Unix timestamp (seconds) -> naive UTC datetime."""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)


def json_text(value) -> str:
    """Strings pass through; dicts/lists become readable JSON text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def read_jsonl(path: Path):
    """Yield one parsed object per line, skipping blank or corrupt lines."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def count_tokens(texts: list[str]) -> list[int]:
    """Estimate token counts for many texts at once (multi-threaded)."""
    prefixes = [t[:TOKENIZE_PREFIX] for t in texts]
    counts = _ENCODER.encode_ordinary_batch(prefixes, num_threads=8)
    result = []
    for text, prefix, tokens in zip(texts, prefixes, counts):
        n = len(tokens)
        if len(text) > len(prefix):  # scale the prefix estimate up
            n = round(n * len(text) / len(prefix))
        result.append(n)
    return result


# --- Saving ---

def save(platform: str, sessions: list[dict], messages: list[dict]):
    """Drop empty sessions, order the messages, add seq/tokens, write both Parquet files."""
    PROCESSED.mkdir(parents=True, exist_ok=True)

    msgs = pd.DataFrame(messages, columns=[c for c in MESSAGE_COLUMNS
                                           if c not in ("seq", "ts_local", "n_chars", "n_tokens")])
    sess = pd.DataFrame(sessions, columns=SESSION_COLUMNS)

    sess = sess[sess["session_id"].isin(msgs["session_id"])]
    msgs = msgs[msgs["session_id"].isin(sess["session_id"])].copy()

    # Parsers append rows in conversation order; a stable sort on ts keeps
    # that order for blocks that share a timestamp.
    msgs["ts"] = pd.to_datetime(msgs["ts"])
    msgs = msgs.sort_values(["session_id", "ts"], kind="stable").reset_index(drop=True)
    msgs["seq"] = msgs.groupby("session_id").cumcount()
    msgs["ts_local"] = msgs["ts"] + LOCAL_OFFSET

    msgs["n_chars"] = msgs["text"].str.len()
    msgs["n_tokens"] = count_tokens(msgs["text"].tolist())

    is_tool = msgs["kind"].isin(["tool_use", "tool_result"])
    msgs.loc[is_tool, "text"] = msgs.loc[is_tool, "text"].str.slice(0, MAX_TOOL_TEXT)

    msgs = msgs[MESSAGE_COLUMNS]
    # Explicit types: an all-empty column would otherwise be saved as type NULL
    # and could not be read together with the other platforms' files.
    for col in SESSION_COLUMNS:
        is_int = col in ("record_count", "api_context_tokens", "api_output_tokens")
        sess[col] = sess[col].astype("Int64" if is_int else "string")

    sess.to_parquet(PROCESSED / f"sessions_meta_{platform}.parquet", index=False)
    msgs.to_parquet(PROCESSED / f"messages_{platform}.parquet", index=False)

    human = msgs["is_human_input"].sum()
    print(f"[{platform}] sessions={len(sess)} message_blocks={len(msgs)} "
          f"human_inputs={human} tokens={msgs['n_tokens'].sum():,}")
    return sess, msgs
