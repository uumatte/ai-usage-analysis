"""Parser for the claude.ai web export (conversations.json).

Skips empty conversations and messages, reads `content` blocks rather than the
lossy `text` field, and keeps only the branch ending at the newest message.
"""
from __future__ import annotations

import json

from aiusage.common import RAW, message_row, parse_iso, json_text, session_row

PLATFORM = "claude_web"

# block type -> key that holds its text. Other types (injected_prompt_block,
# flag) are system content and are skipped.
BLOCK_TEXT_KEY = {
    "text": "text",
    "thinking": "thinking",
    "tool_use": "input",
    "tool_result": "content",
}


def is_empty(msg: dict) -> bool:
    return not msg["content"] and not msg["text"].strip()


def current_branch(conv: dict) -> list[dict]:
    """Messages on the branch ending at the latest non-empty message."""
    non_empty = [m for m in conv["chat_messages"] if not is_empty(m)]
    if not non_empty:
        return []
    by_id = {m["uuid"]: m for m in conv["chat_messages"]}
    node = max(non_empty, key=lambda m: m["created_at"])
    path = []
    while node is not None:
        path.append(node)
        node = by_id.get(node["parent_message_uuid"])  # root's parent is not in by_id
    path.reverse()
    return [m for m in path if not is_empty(m)]


def parse(path=RAW / "Claude" / "conversations.json"):
    with open(path, encoding="utf-8") as fh:
        conversations = json.load(fh)

    sessions, messages = [], []
    for conv in conversations:
        branch = current_branch(conv)
        if not branch:
            continue
        sid = conv["uuid"]
        sessions.append(session_row(
            session_id=sid,
            platform=PLATFORM,
            source="web",
            session_ai="claude",
            session_model=None,  # the export has no model information
            session_title=conv["name"] or None,
            record_count=len(conv["chat_messages"]),
        ))

        for msg in branch:
            ts = parse_iso(msg["created_at"])
            for block_idx, block in enumerate(msg["content"]):
                kind = block["type"]
                if kind not in BLOCK_TEXT_KEY:
                    continue
                text = json_text(block.get(BLOCK_TEXT_KEY[kind]))
                if kind == "tool_use":
                    text = f"{block.get('name')}: {text}"

                if kind == "tool_result":
                    role = "tool"
                elif msg["sender"] == "human":
                    role = "user"
                else:
                    role = "assistant"

                messages.append(message_row(
                    sid, PLATFORM, msg["uuid"], block_idx, ts, role, kind, text,
                    is_human_input=(role == "user" and kind == "text" and bool(text.strip())),
                ))
    return sessions, messages
