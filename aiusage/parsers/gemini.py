"""Parser for the Gemini Google Takeout export (My Activity.html).

Takeout has no conversations, only one HTML card per prompt. Cards are grouped
into sessions by the gemini.google.com/app/<id> link; inside a card the
timestamp separates the prompt from the answer.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup, NavigableString

from aiusage.common import RAW, message_row, session_row, to_naive_utc

PLATFORM = "gemini"

TS_RE = re.compile(
    r"^(\d{1,2}) ([A-Za-z]{3,5}) (\d{4}), (\d{2}):(\d{2}):(\d{2}) GMT([+-])(\d{2}):(\d{2})$")
MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6, "June": 6,
          "Jul": 7, "July": 7, "Aug": 8, "Sep": 9, "Sept": 9, "Oct": 10, "Nov": 11, "Dec": 12}
APP_LINK_RE = re.compile(r"gemini\.google\.com/app/([0-9a-f]+)")
PROMPT_PREFIX = "Prompted"


def parse_timestamp(text: str) -> datetime | None:
    m = TS_RE.match(text.strip())
    if not m:
        return None
    day, mon, year, hh, mm, ss, sign, off_h, off_m = m.groups()
    offset = timedelta(hours=int(off_h), minutes=int(off_m))
    tz = timezone(offset if sign == "+" else -offset)
    local = datetime(int(year), MONTHS[mon], int(day), int(hh), int(mm), int(ss), tzinfo=tz)
    return to_naive_utc(local)


def parse_card(card) -> dict | None:
    """Return {'ts', 'prompt', 'response', 'conv_id'} for a prompt card, else None."""
    content = card.select_one(".content-cell")
    children = list(content.children)
    ts_index = next((i for i, node in enumerate(children)
                     if isinstance(node, NavigableString) and TS_RE.match(node.strip())), None)
    if ts_index is None:
        return None

    # Text nodes before the timestamp are the prompt (one node per line);
    # <a> tags before it are attachment links and are ignored.
    lines = [str(n).strip() for n in children[:ts_index]
             if isinstance(n, NavigableString) and str(n).strip()]
    if not lines or not lines[0].startswith(PROMPT_PREFIX):
        return None
    lines[0] = lines[0][len(PROMPT_PREFIX):].lstrip("\xa0 ")
    prompt = "\n".join(lines).strip()

    response_html = "".join(str(n) for n in children[ts_index + 1:])
    response = BeautifulSoup(response_html, "html.parser").get_text("\n", strip=True)

    caption = card.select_one(".mdl-typography--caption")
    link = APP_LINK_RE.search(str(caption)) if caption else None
    return {
        "ts": parse_timestamp(str(children[ts_index])),
        "prompt": prompt,
        "response": response,
        "conv_id": link.group(1) if link else None,
    }


def parse(path=RAW / "Gemini" / "My Activity.html"):
    with open(path, encoding="utf-8") as fh:
        soup = BeautifulSoup(fh.read(), "html.parser")

    cards = []
    for idx, card in enumerate(soup.select("div.outer-cell")):
        parsed = parse_card(card)
        if parsed is None:
            continue
        parsed["card_idx"] = idx
        parsed["conv_id"] = parsed["conv_id"] or f"gemini-card-{idx}"
        cards.append(parsed)

    by_conv: dict[str, list[dict]] = {}
    for card in cards:
        by_conv.setdefault(card["conv_id"], []).append(card)

    sessions, messages = [], []
    for sid, conv_cards in by_conv.items():
        conv_cards.sort(key=lambda c: c["ts"])  # the log is newest-first
        sessions.append(session_row(
            session_id=sid,
            platform=PLATFORM,
            source="web",
            session_ai="gemini",
            record_count=len(conv_cards),
        ))
        for card in conv_cards:
            msg_id = f"card-{card['card_idx']}"
            messages.append(message_row(sid, PLATFORM, msg_id, 0, card["ts"], "user", "text",
                                        card["prompt"], is_human_input=bool(card["prompt"])))
            if card["response"]:
                messages.append(message_row(sid, PLATFORM, msg_id, 1, card["ts"], "assistant",
                                            "text", card["response"]))
    return sessions, messages
