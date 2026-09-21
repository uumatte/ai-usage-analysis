"""Tagging, step 1 and 3 (step 2 is the model, see tag_api.py).

    python -m aiusage.tagging prepare   # session summaries -> Data/tags/inputs/
    python -m aiusage.tagging merge     # model output -> session_tags.parquet

Sessions listed in Data/private_sessions.csv are never sent to a model.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import PureWindowsPath

import pandas as pd

from aiusage.common import PROCESSED, ROOT

TAGS_DIR = ROOT / "Data" / "tags"
INPUTS = TAGS_DIR / "inputs"
OUTPUTS = TAGS_DIR / "outputs"
REVIEW = TAGS_DIR / "review"
LIBRARY = TAGS_DIR / "tag_library.json"
PRIVATE = ROOT / "Data" / "private_sessions.csv"

PLATFORMS = ["claude_web", "chatgpt", "gemini", "claude_code", "codex"]
BATCH_SIZE = 220
SAMPLE_SIZE = 220
PROMPT_CHARS = 200       # characters kept from each prompt
PROMPTS_PER_SESSION = 5  # first 3 + 2 spread over the rest of the session
MAX_TAGS = 10

# Exactly one activity tag per session: what the user was doing.
ACTIVITIES = {
    "build": "writing or changing code, configuring software, making a mod/app/site",
    "debug": "fixing an error, crash or misbehaviour",
    "plan": "specifying requirements or designing before building",
    "learn": "understanding a concept, studying, practising a language",
    "lookup": "a quick factual question",
    "advice": "personal decisions: health, looks, habits, career, relationships, shopping",
    "write": "drafting, editing, proofreading or translating text",
    "create": "images, stories, game or product ideas",
    "play": "games, role-play, quizzes for fun",
    "chat": "casual or emotional conversation without a task",
}

# Folder names that say nothing about the project: drive roots, home
# folders and the general-purpose folders the CLIs were started in.
GENERIC_FOLDERS = {"", "c:", "c:\\", "user", "users", "desktop", "documents", "downloads",
                   "claude", "codex", "codexxx", "chatgpt", "project"}
# Codex creates a throw-away folder per chat, named after the prompt:
# Documents\Codex\2026-09-10\referenced-chatgpt-conversation-this-is-an
CODEX_CHAT_FOLDER_RE = re.compile(r"\\Documents\\Codex\\\d{4}-\d{2}-\d{2}\\", re.I)
HASH_LIKE_RE = re.compile(r"[0-9a-f-]{32,}")


# --- Step 1: prepare ---

def load_all():
    sessions = pd.concat([pd.read_parquet(PROCESSED / f"sessions_meta_{p}.parquet") for p in PLATFORMS])
    messages = pd.concat([pd.read_parquet(PROCESSED / f"messages_{p}.parquet",
                                          columns=["session_id", "seq", "is_human_input", "text"])
                          for p in PLATFORMS])
    return sessions, messages


def private_session_ids() -> set[str]:
    if not PRIVATE.exists():
        return set()
    return set(pd.read_csv(PRIVATE, dtype=str)["session_id"].dropna())


def pick_prompts(prompts: list[str]) -> list[str]:
    """First 3 prompts plus 2 spread over the rest, each shortened."""
    if len(prompts) > PROMPTS_PER_SESSION:
        rest = prompts[3:]
        step = len(rest) / 2
        prompts = prompts[:3] + [rest[int(step * 0.5)], rest[int(step * 1.5)]]
    return [" ".join(p.split())[:PROMPT_CHARS] for p in prompts]


def project_name(path) -> str | None:
    """Folder name of a real project, or None for generic/auto-created folders."""
    if not isinstance(path, str) or not path:
        return None
    p = PureWindowsPath(path)
    name = p.name.lower()
    if (name in GENERIC_FOLDERS
            or "downloads" in (part.lower() for part in p.parts)
            or CODEX_CHAT_FOLDER_RE.search(path)
            or HASH_LIKE_RE.fullmatch(name)):
        return None
    return p.name


def build_summaries() -> pd.DataFrame:
    sessions, messages = load_all()
    human = messages[messages["is_human_input"]].sort_values(["session_id", "seq"])
    prompts = human.groupby("session_id")["text"].apply(list)

    rows = []
    for s in sessions.itertuples():
        if s.session_id not in prompts.index:
            continue  # nothing typed by the user, nothing to tag
        rows.append({
            "session_id": s.session_id,
            "platform": s.platform,
            "title": s.session_title,
            "project": project_name(s.project_path),
            "n_prompts": len(prompts[s.session_id]),
            "prompts": pick_prompts(prompts[s.session_id]),
        })
    df = pd.DataFrame(rows)
    return df[~df["session_id"].isin(private_session_ids())].reset_index(drop=True)


def write_jsonl(df: pd.DataFrame, path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for row in df.to_dict("records"):
            # pandas turns missing values into NaN, which is not valid JSON
            row = {k: (None if isinstance(v, float) and v != v else v) for k, v in row.items()}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def prepare() -> None:
    INPUTS.mkdir(parents=True, exist_ok=True)
    df = build_summaries().sample(frac=1, random_state=42)  # shuffle so batches mix platforms

    # Sample for the tag library: every platform represented, rest proportional.
    parts = []
    for _, group in df.groupby("platform"):
        parts.append(group.head(max(25, round(SAMPLE_SIZE * len(group) / len(df)))))
    per_platform = pd.concat(parts)
    write_jsonl(per_platform, INPUTS / "sample.jsonl")

    for i in range(0, len(df), BATCH_SIZE):
        write_jsonl(df.iloc[i:i + BATCH_SIZE], INPUTS / f"batch_{i // BATCH_SIZE + 1:02d}.jsonl")
    print(f"{len(df)} sessions -> {-(-len(df) // BATCH_SIZE)} batches, sample of {len(per_platform)}")


# --- Step 3: merge ---

def normalise(tag: str) -> str:
    """'Minecraft Modding ' -> 'minecraft-modding'."""
    tag = tag.strip().lower()
    tag = re.sub(r"[\s_]+", "-", tag)
    return re.sub(r"-{2,}", "-", tag).strip("-")


def merge() -> None:
    REVIEW.mkdir(parents=True, exist_ok=True)
    library = {normalise(t["tag"]) for t in json.loads(LIBRARY.read_text(encoding="utf-8"))}
    summaries = build_summaries().set_index("session_id")

    rows, new_tags, problems = [], [], []
    for path in sorted(OUTPUTS.glob("batch_*.json")):
        assigned = json.loads(path.read_text(encoding="utf-8"))
        for session_id, tags in assigned.items():
            if session_id not in summaries.index:
                problems.append((path.name, session_id, "unknown session id"))
                continue
            activities = [normalise(t) for t in tags if normalise(t) in ACTIVITIES]
            if len(activities) != 1:
                problems.append((path.name, session_id, f"expected 1 activity tag, got {activities}"))
            for raw in tags[:MAX_TAGS]:
                tag = normalise(raw)
                if tag in ACTIVITIES:
                    tag_type = "activity"
                elif tag.startswith("new:"):
                    tag = normalise(tag[4:])
                    tag_type = "topic"
                    new_tags.append((tag, session_id))
                elif tag in library:
                    tag_type = "topic"
                else:
                    problems.append((path.name, session_id, f"tag not in library: {raw}"))
                    tag_type = "topic"
                    new_tags.append((tag, session_id))
                rows.append((session_id, tag, "ai", tag_type))

    # Rule-based tags: the project folder of CLI sessions, no model needed.
    for session_id, s in summaries.iterrows():
        if pd.notna(s["project"]):  # NaN counts as True in a plain `if`
            rows.append((session_id, f"project:{normalise(s['project'])}", "auto", "project"))

    tags = pd.DataFrame(rows, columns=["session_id", "tag", "tag_source", "tag_type"]).drop_duplicates()
    tags.to_parquet(PROCESSED / "session_tags.parquet", index=False)

    tagged = set(tags.loc[tags["tag_source"] == "ai", "session_id"])
    missing = summaries[~summaries.index.isin(tagged)].reset_index()[["session_id", "platform", "title"]]
    missing.to_csv(REVIEW / "untagged_sessions.csv", index=False, encoding="utf-8-sig")

    new = pd.DataFrame(new_tags, columns=["tag", "session_id"])
    new["title"] = new["session_id"].map(summaries["title"])
    (new.groupby("tag").agg(count=("session_id", "size"), example_titles=("title", lambda t: " | ".join(map(str, t.head(3)))))
        .sort_values("count", ascending=False)
        .to_csv(REVIEW / "new_tags.csv", encoding="utf-8-sig"))
    pd.DataFrame(problems, columns=["file", "session_id", "problem"]).to_csv(
        REVIEW / "problems.csv", index=False, encoding="utf-8-sig")

    print(f"tags: {len(tags)} rows for {tags['session_id'].nunique()} sessions | "
          f"untagged: {len(missing)} | new tags: {new['tag'].nunique()} | problems: {len(problems)}")


if __name__ == "__main__":
    {"prepare": prepare, "merge": merge}[sys.argv[1]]()
