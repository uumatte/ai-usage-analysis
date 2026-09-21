-- Topic tags: how many sessions, prompts and tokens each one took.
SELECT
    t.tag                                    AS topic,
    COUNT(*)                                 AS sessions,
    SUM(s.user_turns)                        AS prompts,
    COUNT(*) FILTER (WHERE s.source = 'web') AS web_sessions,
    COUNT(*) FILTER (WHERE s.source = 'cli') AS cli_sessions,
    SUM(s.user_tokens + s.assistant_tokens)  AS conversation_tokens,
    MIN(s.created_at_local)::DATE            AS first_seen
FROM session_tags AS t
JOIN sessions AS s USING (session_id)
WHERE t.tag_type = 'topic'
GROUP BY t.tag
ORDER BY sessions DESC;
