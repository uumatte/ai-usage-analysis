# Data quality issues found and how they are handled

Snapshot of 2026-09-16. Numbers refer to that snapshot.

## Overview

| Platform | Raw input | Sessions after cleaning |
|---|---|---|
| ChatGPT (web) | 687 conversations in 7 JSON files | 685 |
| Claude (web) | 173 conversations in one JSON file | 75 |
| Gemini (web) | 2,785 activity cards in one HTML file | 170 |
| Claude Code (CLI) | 168 transcript files (+11 sub-agent files) | 64 |
| Codex (CLI) | 323 rollout files | 66 |
| **Total** | | **1,060** |

## Issues by platform

### ChatGPT
| Issue | Evidence | Handling |
|---|---|---|
| Conversations are trees (edits/regenerations create siblings) | 135 conversations have branches | Keep only the path from `current_node` back to the root |
| "Branch in new chat" copies earlier messages with the same ids | "Branch · 完善服务器需求": 1,012 of 1,029 messages are copies | Process oldest conversation first; keep each message id once |
| Model not stored per conversation | `default_model_slug` empty for 159 conversations | Most frequent `model_slug` among assistant messages |
| `reasoning_recap` is only "Thought for 8s" | 3,088 messages | Dropped; `thoughts` kept as `thinking` |
| Voice messages | 1,439 audio transcription parts | Transcription text used |

### Claude (web)
| Issue | Evidence | Handling |
|---|---|---|
| Empty conversations | 98 of 173 have no content at all (origin unknown) | Skipped |
| `text` field is lossy | 145 assistant messages show "This block is not supported on your current device yet." | Use `content` blocks only |
| Branches | 39 conversations | Keep branch ending at the latest non-empty message |
| No model information | whole export | `session_model` is NULL |

### Gemini
| Issue | Evidence | Handling |
|---|---|---|
| Takeout gives an activity log, not conversations | one HTML card per prompt | Group cards by the `gemini.google.com/app/<id>` link |
| Non-prompt cards | 12 cards: Branched, feedback, Canvas, draft selection | Skipped |
| Timestamps are localised text in GMT-04:00 | "8 Sept 2026, 02:29:14 GMT-04:00" | Parsed with a regex, converted to UTC |
| No title, model or tokens | whole export | NULL; tokens estimated |

### Claude Code
| Issue | Evidence | Handling |
|---|---|---|
| Resuming copies the whole history into a new file | 38,850 records appear in more than one file; one session spans 21 files | Read files oldest first, skip records whose `uuid` was seen, merge the file into the earlier session |
| One API response split into several records with identical `usage` | ~2/3 of responses split into 2–8 records | Count usage once per `message.id` |
| `user` records are mostly not typed by the user | ~95% are `tool_result`; also meta records, compaction summaries, slash-command output, interruptions | Only real typed text is `is_human_input` |
| Prompts typed while Claude is busy are stored as attachments | 429 `queued_command` prompt attachments in the raw files (before de-duplication) | Counted as human input |
| `cache_read_input_tokens` re-counts the whole context every turn | — | `api_context_tokens` = context of the last turn, never a sum |
| Sub-agent transcripts | 11 files | Skipped (not the user's own sessions) |

### Codex
| Issue | Evidence | Handling |
|---|---|---|
| Imported copies of Claude Code sessions | 162 of 323 files (listed in `~/.codex/external_agent_session_imports.json`) | Moved to `Data/excluded/` with a manifest; parser also checks the manifest |
| Threads not started by the user | `thread_source` guardian_review (40), subagent (29) | Skipped |
| One thread spread over several files | a thread resumed 12 times = 12 files with the same id | Files grouped by thread id; repeated records counted once |
| Injected context inside user messages | `<environment_context>`, `<recommended_plugins>`, "# Files mentioned by the user" | Typed text taken after "## My request:"; pure context skipped |
| Reasoning is encrypted | all reasoning items | Only the optional summary is used |
| Token counts are cumulative | `token_count` events | Last event gives output total and last context size |

## Cross-platform decisions

* **Tokens**: web exports have no token counts, so every text block gets a
  `tiktoken` `o200k_base` estimate — the same tokenizer everywhere, so
  platforms are comparable. CLI sessions additionally keep the API's real
  numbers in `api_context_tokens` / `api_output_tokens`.
* **Human input vs. records**: a CLI "record" can be a tool call; compare
  platforms with `is_human_input`, never with raw record counts.
* **Time**: stored as UTC (`ts`) and Hong Kong time (`ts_local`, UTC+8,
  no daylight saving).
* **Tool text**: token counts use the full text; only the first 2,000
  characters are stored.
