-- Which activities end after one or two prompts?
SELECT
    t.tag                                                                AS activity,
    COUNT(*)                                                             AS sessions,
    MEDIAN(s.user_turns)                                                 AS median_prompts,
    ROUND(100.0 * AVG(CASE WHEN s.user_turns <= 2 THEN 1 ELSE 0 END), 1) AS short_pct
FROM session_tags AS t
JOIN sessions AS s USING (session_id)
WHERE t.tag_type = 'activity'
GROUP BY t.tag
ORDER BY short_pct DESC;
