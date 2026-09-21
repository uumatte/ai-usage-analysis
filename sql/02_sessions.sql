-- One row per session: the analysis table from the project plan.
--
-- Built from message-level rows so every platform is measured the same way:
--   user_turns       distinct messages the user actually typed (not records:
--                    a CLI "record" is often a tool call)
--   user_tokens      tokens the user typed (tool results excluded)
--   assistant_tokens tokens of the AI's visible text answers
--   tool_tokens      tokens of tool calls and tool outputs (CLI and Claude web)
--   total_tokens     context size of the LAST turn, never a sum over turns:
--                    the API's real number for CLI sessions, the estimated
--                    size of the whole transcript (without thinking) for web.
-- Sessions without a single typed prompt are left out.

CREATE OR REPLACE TABLE sessions AS
WITH message_stats AS (
    SELECT
        session_id,
        MIN(ts)                                                        AS created_at,
        MAX(ts)                                                        AS last_message_at,
        MIN(ts_local)                                                  AS created_at_local,
        MAX(ts_local)                                                  AS last_message_at_local,
        COUNT(DISTINCT msg_id) FILTER (WHERE is_human_input)           AS user_turns,
        COALESCE(SUM(n_tokens) FILTER (WHERE is_human_input), 0)       AS user_tokens,
        COALESCE(SUM(n_tokens) FILTER (WHERE role = 'assistant' AND kind = 'text'), 0)
                                                                       AS assistant_tokens,
        COALESCE(SUM(n_tokens) FILTER (WHERE kind IN ('tool_use', 'tool_result')), 0)
                                                                       AS tool_tokens,
        COALESCE(SUM(n_tokens) FILTER (WHERE kind = 'thinking'), 0)    AS thinking_tokens,
        COALESCE(SUM(n_tokens) FILTER (WHERE kind <> 'thinking'), 0)   AS transcript_tokens
    FROM messages
    GROUP BY session_id
),
first_prompt AS (
    -- the first typed prompt of each session, found with a window function
    SELECT session_id, LEFT(text, 200) AS first_user_message
    FROM messages
    WHERE is_human_input
    QUALIFY ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY seq) = 1
)
SELECT
    m.session_id,
    m.platform,
    m.source,
    m.session_ai,
    m.session_model,
    COALESCE(m.session_title, LEFT(f.first_user_message, 40))         AS session_title,
    CASE WHEN m.source = 'cli' THEN 'coding' ELSE 'chat' END           AS session_type,
    m.project_path,
    s.created_at,
    s.last_message_at,
    s.created_at_local,
    s.last_message_at_local,
    DATE_DIFF('day', s.created_at_local, s.last_message_at_local)      AS duration_days,
    s.user_turns,
    m.record_count,
    s.user_tokens,
    s.assistant_tokens,
    s.tool_tokens,
    s.thinking_tokens,
    COALESCE(m.api_context_tokens, s.transcript_tokens)                AS total_tokens,
    m.api_output_tokens,
    f.first_user_message
FROM sessions_meta AS m
JOIN message_stats AS s USING (session_id)
LEFT JOIN first_prompt AS f USING (session_id)
WHERE s.user_turns >= 1;
