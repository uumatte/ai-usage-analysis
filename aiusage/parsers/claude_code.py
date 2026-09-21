"""Parser for Claude Code transcripts (one JSONL file per session).

Resuming copies the whole history into a new file, so files are read oldest
first, records seen before are skipped and the file is merged into that earlier
session. Usage is counted once per message id, and only text the user really
typed counts as human input (most `user` records are tool results).
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from aiusage.common import RAW, json_text, message_row, parse_iso, read_jsonl, session_row

PLATFORM = "claude_code"

NOT_TYPED_BY_USER = (
    "<command-name>", "<command-message>", "<command-args>",
    "<local-command-stdout>", "<local-command-stderr>", "<local-command-caveat>",
    "<task-notification>", "<system-reminder>",
    "<bash-input>", "<bash-stdout>", "<bash-stderr>",
    "[Request interrupted", "Caveat:", "This session is being continued",
)


def is_typed_by_user(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith(NOT_TYPED_BY_USER)


def tool_result_text(content) -> str:
    """tool_result content is a string or a list of {type: text/image} blocks."""
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return json_text(content)


def first_timestamp(path: Path) -> str:
    for record in read_jsonl(path):
        if record.get("timestamp"):
            return record["timestamp"]
    return "9999"


class SessionGroup:
    """Everything collected for one (possibly merged) session."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.project_path = None
        self.custom_title = None
        self.ai_title = None
        self.models = Counter()
        self.record_count = 0
        self.output_tokens = 0
        self.last_usage_ts = None
        self.last_context_tokens = None

    def to_row(self) -> dict:
        return session_row(
            session_id=self.session_id,
            platform=PLATFORM,
            source="cli",
            session_ai="claude",
            session_model=self.models.most_common(1)[0][0] if self.models else None,
            session_title=self.custom_title or self.ai_title,
            project_path=self.project_path,
            record_count=self.record_count,
            api_context_tokens=self.last_context_tokens,
            api_output_tokens=self.output_tokens,
        )


def user_blocks(record: dict):
    """Yield (block_idx, role, kind, text, is_human) for a user record."""
    if record.get("isMeta") or record.get("isCompactSummary"):
        return
    content = record["message"].get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    for i, block in enumerate(content or []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and is_typed_by_user(block.get("text", "")):
            yield i, "user", "text", block["text"], True
        elif block.get("type") == "tool_result":
            yield i, "tool", "tool_result", tool_result_text(block.get("content")), False
        # images and injected text are skipped


def assistant_blocks(record: dict):
    """Yield (block_idx, role, kind, text, is_human) for an assistant record."""
    for i, block in enumerate(record["message"].get("content") or []):
        btype = block.get("type", "")
        if btype == "text":
            yield i, "assistant", "text", block.get("text", ""), False
        elif btype == "thinking":
            # Newer versions store only a signature, so the text is often empty.
            yield i, "assistant", "thinking", block.get("thinking", ""), False
        elif btype.endswith("tool_use"):
            yield i, "assistant", "tool_use", f"{block.get('name')}: {json_text(block.get('input'))}", False
        elif btype.endswith("tool_result"):
            yield i, "tool", "tool_result", tool_result_text(block.get("content")), False


def queued_prompt_text(attachment: dict) -> str:
    prompt = attachment.get("prompt")
    if isinstance(prompt, list):
        return "\n".join(b.get("text", "") for b in prompt if isinstance(b, dict))
    return prompt or ""


def parse(raw_dir=RAW / "Claude Code"):
    files = [p for p in raw_dir.rglob("*.jsonl") if "subagents" not in p.parts]
    files.sort(key=first_timestamp)

    record_owner: dict[str, str] = {}   # record uuid -> session id that first had it
    counted_message_ids: set[str] = set()
    groups: dict[str, SessionGroup] = {}
    messages = []

    for path in files:
        records = list(read_jsonl(path))
        # A file that shares records with an earlier file continues that session.
        owner = next((record_owner[r["uuid"]] for r in records if r.get("uuid") in record_owner), None)
        group = groups.setdefault(owner or path.stem, SessionGroup(owner or path.stem))
        sid = group.session_id

        for record in records:
            uuid = record.get("uuid")
            if uuid:
                if uuid in record_owner:
                    continue  # copied history from the earlier file
                record_owner[uuid] = sid
            group.record_count += 1

            rtype = record.get("type")
            if rtype == "custom-title":
                group.custom_title = record.get("customTitle") or group.custom_title
                continue
            if rtype == "ai-title":
                group.ai_title = record.get("aiTitle") or group.ai_title
                continue
            if record.get("isSidechain") or not record.get("timestamp"):
                continue
            if group.project_path is None and record.get("cwd"):
                group.project_path = record["cwd"]

            ts = parse_iso(record["timestamp"])
            blocks = []
            if rtype == "user" and isinstance(record.get("message"), dict):
                blocks = list(user_blocks(record))
            elif rtype == "assistant" and isinstance(record.get("message"), dict):
                msg = record["message"]
                if msg.get("model") == "<synthetic>":  # locally generated error text
                    continue
                if msg.get("model"):
                    group.models[msg["model"]] += 1
                usage = msg.get("usage") or {}
                if msg.get("id") and usage and msg["id"] not in counted_message_ids:
                    counted_message_ids.add(msg["id"])
                    group.output_tokens += usage.get("output_tokens") or 0
                    if group.last_usage_ts is None or ts >= group.last_usage_ts:
                        group.last_usage_ts = ts
                        group.last_context_tokens = sum(usage.get(k) or 0 for k in (
                            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
                blocks = list(assistant_blocks(record))
            elif rtype == "attachment":
                att = record.get("attachment") or {}
                if att.get("type") == "queued_command" and att.get("commandMode") == "prompt":
                    text = queued_prompt_text(att)
                    if is_typed_by_user(text):
                        blocks = [(0, "user", "text", text, True)]

            for block_idx, role, kind, text, is_human in blocks:
                messages.append(message_row(sid, PLATFORM, uuid or f"{path.stem}:{ts}", block_idx,
                                            ts, role, kind, text, is_human_input=is_human))

    sessions = [g.to_row() for g in groups.values()]
    return sessions, messages
