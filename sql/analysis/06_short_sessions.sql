-- "Abandonment": sessions with only one or two prompts, per platform.
SELECT
    platform,
    source,
    COUNT(*)                                                             AS sessions,
    COUNT(*) FILTER (WHERE user_turns = 1)                               AS one_prompt,
    COUNT(*) FILTER (WHERE user_turns <= 2)                              AS up_to_two_prompts,
    ROUND(100.0 * COUNT(*) FILTER (WHERE user_turns <= 2) / COUNT(*), 1) AS short_pct
FROM sessions
GROUP BY platform, source
ORDER BY short_pct DESC;
