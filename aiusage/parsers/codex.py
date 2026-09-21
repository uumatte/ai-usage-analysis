"""Parser for Codex transcripts (rollout-*.jsonl).

Skips threads imported from Claude Code and threads not started by the user,
groups the files of one thread, and takes the typed text out of messages that
also carry injected context.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict

from aiusage.common import RAW, ROOT, json_text, message_row, parse_iso, read_jsonl, session_row

PLATFORM = "codex"

SKIP_THREAD_SOURCES = {"guardian_review", "subagent"}
EXCLUDED_MANIFEST = ROOT / "Data" / "excluded" / "codex_imported_from_claude_code" / "manifest.csv"

INJECTED_PREFIXES = (
    "<environment_context>", "<recommended_plugins>", "<turn_aborted>",
    "<user_instructions>", "<permissions", "<INSTRUCTIONS>", "<external_",
    "# AGENTS.md", "# Files mentioned by the user", "# Files pasted by the user",
    "# Chrome tabs", "# In app browser", "# Hint",
)
REQUEST_MARKERS = ("## My request for Codex:", "## My request:")
IMAGE_TAG_RE = re.compile(r"<image\b[^>]*>.*?</image>|<image\b[^>]*/?>", re.S)


def excluded_thread_ids() -> set[str]:
    if not EXCLUDED_MANIFEST.exists():
        return set()
    with open(EXCLUDED_MANIFEST, encoding="utf-8-sig") as fh:
        return {row["codex_thread_id"] for row in csv.DictReader(fh)}


def load_titles(raw_dir) -> dict[str, str]:
    index = raw_dir / "session_index.jsonl"
    if not index.exists():
        return {}
    return {row["id"]: row.get("thread_name") for row in read_jsonl(index)}


def typed_user_text(content: list) -> str:
    """Extract what the user actually typed from a user message, or ''."""
    full = "".join(c.get("text", "") for c in content if c.get("type") == "input_text")
    for marker in REQUEST_MARKERS:
        if marker in full:
            return IMAGE_TAG_RE.sub("", full.split(marker, 1)[1]).strip()
    if full.lstrip().startswith(INJECTED_PREFIXES):
        return ""
    return IMAGE_TAG_RE.sub("", full).strip()


def output_text(output) -> str:
    """Tool output is a string or a list of {type, text} items."""
    if isinstance(output, list):
        return "\n".join(item.get("text", "") for item in output if isinstance(item, dict))
    return json_text(output)


def files_by_thread(raw_dir) -> dict[str, list]:
    """thread id -> its rollout files, oldest first (file names start with a timestamp)."""
    threads = defaultdict(list)
    for path in sorted(raw_dir.rglob("rollout-*.jsonl"), key=lambda p: p.name):
        first = next(read_jsonl(path))  # the first line is this file's own session_meta
        threads[first["payload"]["id"]].append(path)
    return threads


def parse(raw_dir=RAW / "Codex"):
    excluded = excluded_thread_ids()
    titles = load_titles(raw_dir)
    sessions, messages = [], []

    for sid, paths in files_by_thread(raw_dir).items():
        meta = next(read_jsonl(paths[0]))["payload"]
        if sid in excluded or meta.get("thread_source") in SKIP_THREAD_SOURCES:
            continue

        # (file stem, line number, record) for every record of this thread
        records = [(path.stem, line_no, record)
                   for path in paths
                   for line_no, record in enumerate(read_jsonl(path))]
        seen_records: set[str] = set()
        models = Counter()
        last_token_info = None
        rows = []
        for file_stem, line_no, record in records:
            fingerprint = hashlib.md5(json.dumps(record, sort_keys=True).encode()).hexdigest()
            if fingerprint in seen_records:
                continue
            seen_records.add(fingerprint)

            rtype = record.get("type")
            payload = record.get("payload") or {}
            if rtype == "turn_context" and payload.get("model"):
                models[payload["model"]] += 1
            if rtype == "event_msg" and payload.get("type") == "token_count" and payload.get("info"):
                last_token_info = payload["info"]
            if rtype != "response_item":
                continue

            ts = parse_iso(record["timestamp"])
            ptype = payload.get("type")
            block = None  # (role, kind, text, is_human)
            if ptype == "message" and payload.get("role") == "user":
                text = typed_user_text(payload.get("content") or [])
                if text:
                    block = ("user", "text", text, True)
            elif ptype == "message" and payload.get("role") == "assistant":
                text = "".join(c.get("text", "") for c in payload.get("content") or []
                               if c.get("type") == "output_text")
                if text.strip():
                    block = ("assistant", "text", text, False)
            elif ptype == "reasoning":
                text = "\n".join(s.get("text", "") for s in payload.get("summary") or []
                                 if isinstance(s, dict))
                if text.strip():
                    block = ("assistant", "thinking", text, False)
            elif ptype in ("function_call", "custom_tool_call"):
                args = payload.get("arguments") if ptype == "function_call" else payload.get("input")
                block = ("assistant", "tool_use", f"{payload.get('name')}: {json_text(args)}", False)
            elif ptype in ("function_call_output", "custom_tool_call_output"):
                block = ("tool", "tool_result", output_text(payload.get("output")), False)
            # developer messages, agent_message and compaction items are skipped

            if block:
                role, kind, text, is_human = block
                # file_stem keeps ids unique when a thread spans several files
                rows.append(message_row(sid, PLATFORM, f"{file_stem}:{line_no}", 0, ts, role, kind,
                                        text, is_human_input=is_human))

        if not rows:
            continue
        messages.extend(rows)
        provider = meta.get("model_provider") or "openai"
        usage = last_token_info or {}
        sessions.append(session_row(
            session_id=sid,
            platform=PLATFORM,
            source="cli",
            session_ai="chatgpt" if provider == "openai" else provider,
            session_model=models.most_common(1)[0][0] if models else None,
            session_title=titles.get(sid),
            project_path=meta.get("cwd"),
            record_count=len(records),
            api_context_tokens=(usage.get("last_token_usage") or {}).get("input_tokens"),
            api_output_tokens=(usage.get("total_token_usage") or {}).get("output_tokens"),
        ))
    return sessions, messages
