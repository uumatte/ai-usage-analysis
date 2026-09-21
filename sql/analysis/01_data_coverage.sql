-- Which dates does each platform's data cover?
-- Coverage differs (web exports vs. CLI transcripts that get cleaned up), so
-- trends over time must be read with this table in mind.
SELECT
    platform,
    source,
    MIN(created_at_local)::DATE      AS first_day,
    MAX(last_message_at_local)::DATE AS last_day,
    COUNT(*)                         AS sessions,
    SUM(user_turns)                  AS prompts
FROM sessions
GROUP BY platform, source
ORDER BY first_day;
