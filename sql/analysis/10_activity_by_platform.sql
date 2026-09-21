-- What kind of work is done on each platform? (one activity tag per session)
SELECT
    s.platform,
    t.tag                                                                     AS activity,
    COUNT(*)                                                                  AS sessions,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY s.platform), 1) AS pct_of_platform,
    SUM(s.user_turns)                                                         AS prompts
FROM sessions AS s
JOIN session_tags AS t USING (session_id)
WHERE t.tag_type = 'activity'
GROUP BY s.platform, t.tag
ORDER BY s.platform, sessions DESC;
