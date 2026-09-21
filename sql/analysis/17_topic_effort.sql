-- Effort per topic: how many sessions, how long they run, and who does
-- the talking (user tokens per AI answer token).
SELECT
    t.tag                                                                     AS topic,
    COUNT(*)                                                                  AS sessions,
    ROUND(AVG(s.user_turns), 1)                                               AS avg_prompts,
    ROUND(AVG(s.user_tokens + s.assistant_tokens))                            AS avg_conversation_tokens,
    ROUND(SUM(s.user_tokens)::DOUBLE / NULLIF(SUM(s.assistant_tokens), 0), 3) AS user_per_ai_token
FROM session_tags AS t
JOIN sessions AS s USING (session_id)
WHERE t.tag_type = 'topic'
GROUP BY t.tag
HAVING COUNT(*) >= 10
ORDER BY sessions DESC;
