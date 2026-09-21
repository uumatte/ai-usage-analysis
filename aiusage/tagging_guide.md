# Tagging guide

You are tagging sessions from one person's AI chat history (ChatGPT, Claude,
Gemini, Claude Code, Codex). Each input line is JSON:

```json
{"session_id": "...", "platform": "chatgpt", "title": "...", "project": "...",
 "n_prompts": 12, "prompts": ["first prompt", "second", "third", "...", "..."]}
```

`prompts` are the user's own words (first 3, plus 2 from later in the
session), each cut to 200 characters. Most are in Chinese. `project` is the
folder a coding session ran in, if any.

## What to output

For every session, a list of tags:

1. **Exactly one activity tag** — what the user was doing:

   | tag | meaning |
   |---|---|
   | `build` | writing or changing code, configuring software, making a mod/app/site |
   | `debug` | fixing an error, crash or misbehaviour |
   | `plan` | specifying requirements or designing before building |
   | `learn` | understanding a concept, studying, practising a language |
   | `lookup` | a quick factual question |
   | `advice` | personal decisions: health, looks, habits, career, relationships, shopping |
   | `write` | drafting, editing, proofreading or translating text |
   | `create` | images, stories, game or product ideas |
   | `play` | games, role-play, quizzes for fun |
   | `chat` | casual or emotional conversation without a task |

2. **1 to 5 topic tags** — what it was about, taken from the tag library
   (`Data/tags/tag_library.json`). Pick the most specific tags that fit;
   add a broader one only if it adds information (e.g. `minecraft-modding`
   already implies `minecraft`, so do not add both unless the session is
   also about the game itself).

3. If no library tag fits a clear topic, you may add a new one written as
   `new:<tag>`. Use this rarely — only when the topic is clearly distinct
   from every library tag.

## Tag spelling

* English, lowercase, words joined by hyphens: `skin-care`, `hong-kong-internship`.
* Nouns for topics, not sentences. No platform names as topics (the platform
  is already known), except when the session is *about* an AI product,
  e.g. `ai-tools`.
* Never put personal names, secrets or quotes in tags.

## Output format

Write one JSON object mapping every session_id in your batch to its tag list,
activity tag first. Include every session in the batch, even vague ones
(then use the best guess, e.g. `["chat", "small-talk"]`).

```json
{
  "0f3c...": ["build", "minecraft-modding", "flight-physics"],
  "a91e...": ["advice", "skin-care"]
}
```
