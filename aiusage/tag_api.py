"""Tagging step 2: send the batches to a model API.

    python -m aiusage.tag_api library --provider claude          # build the tag library
    python -m aiusage.tag_api tag --provider claude --dry-run    # count requests only
    python -m aiusage.tag_api tag --provider claude              # write outputs/batch_XX.json

Providers live in aiusage/llm/. This module only calls backend.call_json, so it
works the same for every provider. Runs cost money: try --dry-run first.
Chunks of 30 sessions, 4 in parallel, missing sessions retried once, failed
chunks skipped, and existing output files are resumed (--force re-tags).
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from aiusage.common import ROOT
from aiusage.llm import PROVIDERS, ModelRefused, Usage, get_backend
from aiusage.tagging import ACTIVITIES, INPUTS, LIBRARY, OUTPUTS

CHUNK_SIZE = 30     # sessions per request
MAX_WORKERS = 4     # requests running at the same time
GUIDE = ROOT / "aiusage" / "tagging_guide.md"

LIBRARY_INSTRUCTIONS = """\
You are building a topic tag library for a personal data-analysis project.
The data is one person's own AI chat history, used with their permission.
Treat it as private: never put personal details, names or quotes into tags
or descriptions.

The user message holds sampled sessions (JSON lines: title, project,
platform, a few of the user's prompts, mostly Chinese). The full dataset is
about 1,000 sessions from the same distribution.

Return 60-100 TOPIC tags (the 10 activity tags in the guide are fixed and
must not be included):
- English, lowercase, hyphen-joined, following the guide's spelling rules.
- Each tag should plausibly apply to at least ~3 sessions in the full
  dataset, yet be specific enough to be informative.
- No overlapping synonyms: keep one tag and mention the other wording in
  its description.
- Include a few broad fallback tags (e.g. general-knowledge, small-talk,
  unclear-topic) so every session can get a topic.
- Each description is one short line saying what belongs under the tag and,
  where useful, which neighbouring tag to use instead.
"""

TAGGING_INSTRUCTIONS = """\
You tag sessions from one person's AI chat history, following the tagging
guide and choosing topic tags from the tag library below. The data is
private: never copy personal details or prompt text into tags.

This is an API call, not a file-writing task: return the result in the
`sessions` list of the response schema, one entry per input session, with
the activity tag first. Every session_id in the user message must appear
exactly once.
"""

# Both schemas follow the strict-mode rules shared by the providers:
# every property required, no additional properties.
LIBRARY_SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"tag": {"type": "string"}, "description": {"type": "string"}},
                "required": ["tag", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["tags"],
    "additionalProperties": False,
}

TAGS_SCHEMA = {
    "type": "object",
    "properties": {
        "sessions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["session_id", "tags"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sessions"],
    "additionalProperties": False,
}


def jsonl_text(rows: list[dict]) -> str:
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


def read_jsonl_rows(path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# --- Step 2a: build the tag library ---

def build_library(backend) -> None:
    sample = read_jsonl_rows(INPUTS / "sample.jsonl")
    system = LIBRARY_INSTRUCTIONS + "\n\n# Tagging guide\n\n" + GUIDE.read_text(encoding="utf-8")
    data, usage = backend.call_json(system, jsonl_text(sample), LIBRARY_SCHEMA, "tag_library")

    tags = [t for t in data["tags"] if t["tag"] not in ACTIVITIES]
    LIBRARY.write_text(json.dumps(tags, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"tag library: {len(tags)} topic tags from {len(sample)} sampled sessions via "
          f"{backend.name}/{backend.model} (input {usage.input_tokens:,} / output {usage.output_tokens:,} tokens)")


# --- Step 2b: tag every batch ---

def tagging_system_prompt() -> str:
    """Instructions + guide + library: identical for every request, so it can be cached."""
    library = json.loads(LIBRARY.read_text(encoding="utf-8"))
    library_text = json.dumps(sorted(library, key=lambda t: t["tag"]), ensure_ascii=False, indent=1)
    return (TAGGING_INSTRUCTIONS
            + "\n\n# Tagging guide\n\n" + GUIDE.read_text(encoding="utf-8")
            + "\n\n# Tag library\n\n" + library_text)


def tag_chunk(backend, system: str, rows: list[dict]):
    """Tag one chunk; returns ({session_id: tags}, Usage). Ids not in the chunk are dropped."""
    wanted = {row["session_id"] for row in rows}
    user = f"Tag these {len(rows)} sessions.\n\n" + jsonl_text(rows)
    data, usage = backend.call_json(system, user, TAGS_SCHEMA, "session_tags")
    tagged = {item["session_id"]: item["tags"] for item in data["sessions"]
              if item["session_id"] in wanted and item["tags"]}
    return tagged, usage


def chunks(rows: list[dict], size: int) -> list[list[dict]]:
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def new_totals() -> dict:
    return {"requests": 0, "failed_chunks": 0, "usage": Usage()}


def add_usage(total: Usage, usage: Usage) -> None:
    total.input_tokens += usage.input_tokens
    total.cache_read_tokens += usage.cache_read_tokens
    total.cache_write_tokens += usage.cache_write_tokens
    total.output_tokens += usage.output_tokens


def tag_batch(backend, system: str, rows: list[dict], existing: dict, totals: dict) -> dict:
    """Tag the rows not in ``existing``; one retry for sessions a response left out."""
    result = dict(existing)
    skippable = backend.errors + (ModelRefused, RuntimeError, json.JSONDecodeError)
    for attempt in (1, 2):
        todo = [row for row in rows if row["session_id"] not in result]
        if not todo:
            break
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(tag_chunk, backend, system, part) for part in chunks(todo, CHUNK_SIZE)]
            for future in as_completed(futures):
                try:
                    tagged, usage = future.result()
                except skippable as err:
                    # the SDKs already retried rate limits and server errors
                    totals["failed_chunks"] += 1
                    print(f"    a chunk failed ({type(err).__name__}: {err}); its sessions stay untagged")
                    continue
                result.update(tagged)
                totals["requests"] += 1
                add_usage(totals["usage"], usage)
        if attempt == 1 and len(result) < len(rows):
            print(f"    retrying {len(rows) - len(result)} sessions missing from the responses")
    return result


def tag_all(backend, force: bool = False, dry_run: bool = False) -> dict:
    if not LIBRARY.exists():
        raise SystemExit("no tag library yet: run `python -m aiusage.tag_api library` first")
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    system = None if dry_run else tagging_system_prompt()
    totals = new_totals()
    planned = 0

    for batch_path in sorted(INPUTS.glob("batch_*.jsonl")):
        rows = read_jsonl_rows(batch_path)
        out_path = OUTPUTS / f"{batch_path.stem}.json"
        existing = {}
        if out_path.exists() and not force:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        todo = [row for row in rows if row["session_id"] not in existing]
        if not todo:
            print(f"{batch_path.stem}: complete, skipped")
            continue

        n_requests = -(-len(todo) // CHUNK_SIZE)
        planned += n_requests
        print(f"{batch_path.stem}: {len(todo)} sessions to tag in {n_requests} requests")
        if dry_run:
            continue

        result = tag_batch(backend, system, rows, existing, totals)
        ordered = {row["session_id"]: result[row["session_id"]] for row in rows if row["session_id"] in result}
        out_path.write_text(json.dumps(ordered, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"    wrote {out_path.name}: {len(ordered)}/{len(rows)} sessions tagged")

    if dry_run:
        print(f"dry run: {planned} requests would be sent (nothing was sent)")
        return totals
    u = totals["usage"]
    print(f"done via {backend.name}/{backend.model}: {totals['requests']} requests, "
          f"{totals['failed_chunks']} failed chunks | input {u.input_tokens:,} tokens "
          f"(cache read {u.cache_read_tokens:,}, cache write {u.cache_write_tokens:,}), "
          f"output {u.output_tokens:,}")
    print("next: python -m aiusage.tagging merge")
    return totals


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Tag sessions through a model API.")
    parser.add_argument("command", choices=["library", "tag"])
    parser.add_argument("--provider", choices=PROVIDERS, default="claude")
    parser.add_argument("--model", help="model name (Claude default: claude-opus-5; OpenAI: required "
                                        "unless OPENAI_MODEL is set)")
    parser.add_argument("--effort", help="reasoning effort, e.g. low / medium / high "
                                         "(Claude default: medium; OpenAI: only for reasoning models)")
    parser.add_argument("--base-url", help="override the provider's API address (e.g. a mainland endpoint)")
    parser.add_argument("--api-key-env", help="environment variable holding the API key, if not the default")
    parser.add_argument("--dry-run", action="store_true", help="count requests, send nothing")
    parser.add_argument("--force", action="store_true", help="re-tag batches that already have output")
    args = parser.parse_args(argv)

    if args.command == "tag" and args.dry_run:
        tag_all(backend=None, force=args.force, dry_run=True)
        return
    backend = get_backend(args.provider, model=args.model, effort=args.effort,
                          base_url=args.base_url, api_key_env=args.api_key_env)
    if args.command == "library":
        build_library(backend)
    else:
        tag_all(backend, force=args.force)


if __name__ == "__main__":
    main()
