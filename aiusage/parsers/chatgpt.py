"""Parser for the ChatGPT export (conversations-*.json).

Each conversation is a tree: the visible path runs from `current_node` back to
the root. "Branch in new chat" copies messages with the same ids, so
conversations are read oldest first and every message id is kept once.
"""
from __future__ import annotations

import json
from collections import Counter

from aiusage.common import RAW, from_epoch, message_row, session_row

PLATFORM = "chatgpt"


def load_conversations(raw_dir=RAW / "ChatGPT") -> list[dict]:
    conversations = []
    for path in sorted(raw_dir.glob("conversations-*.json")):
        with open(path, encoding="utf-8") as fh:
            conversations.extend(json.load(fh))
    return conversations


def visible_path(conv: dict) -> list[dict]:
    """Messages from the root to ``current_node``, in order."""
    path = []
    node_id = conv["current_node"]
    while node_id:
        node = conv["mapping"][node_id]
        if node.get("message"):
            path.append(node["message"])
        node_id = node.get("parent")
    path.reverse()
    return path


def part_text(part) -> str:
    """A part is either a plain string or a dict (image, audio transcription...)."""
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        return part.get("text") or ""  # audio_transcription has text; images do not
    return ""


def message_blocks(msg: dict):
    """Yield (block_idx, role, kind, text) for one ChatGPT message."""
    role = msg["author"]["role"]
    content = msg.get("content") or {}
    ctype = content.get("content_type")

    if ctype in ("text", "multimodal_text"):
        text = "\n".join(t for t in (part_text(p) for p in content.get("parts") or []) if t)
        if not text.strip():
            return
        if role == "user":
            yield 0, "user", "text", text
        elif role == "assistant":
            # A recipient other than "all" means the model is calling a tool.
            if msg.get("recipient") in (None, "all"):
                yield 0, "assistant", "text", text
            else:
                yield 0, "assistant", "tool_use", f"{msg['recipient']}: {text}"
        elif role == "tool":
            yield 0, "tool", "tool_result", text
        # role == "system": hidden instructions, skipped

    elif ctype == "thoughts":
        for i, thought in enumerate(content.get("thoughts") or []):
            text = thought.get("content") or thought.get("summary") or ""
            if text.strip():
                yield i, "assistant", "thinking", text


def parse(raw_dir=RAW / "ChatGPT"):
    conversations = sorted(load_conversations(raw_dir), key=lambda c: c.get("create_time") or 0)
    seen_message_ids: set[str] = set()
    sessions, messages = [], []

    for conv in conversations:
        sid = conv["conversation_id"]
        path = visible_path(conv)
        new_messages = [m for m in path if m["id"] not in seen_message_ids]
        seen_message_ids.update(m["id"] for m in path)

        models = Counter()
        rows = []
        for msg in new_messages:
            ts = from_epoch(msg.get("create_time") or conv["create_time"])
            slug = (msg.get("metadata") or {}).get("model_slug")
            if msg["author"]["role"] == "assistant" and slug:
                models[slug] += 1
            for block_idx, role, kind, text in message_blocks(msg):
                rows.append(message_row(sid, PLATFORM, msg["id"], block_idx, ts, role, kind, text,
                                        is_human_input=(role == "user")))
        if not rows:
            continue

        messages.extend(rows)
        sessions.append(session_row(
            session_id=sid,
            platform=PLATFORM,
            source="web",
            session_ai="chatgpt",
            session_model=models.most_common(1)[0][0] if models else conv.get("default_model_slug"),
            session_title=conv.get("title") or None,
            record_count=sum(1 for n in conv["mapping"].values() if n.get("message")),
        ))
    return sessions, messages
