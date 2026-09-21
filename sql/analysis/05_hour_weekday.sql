-- When are prompts typed? Weekday (1 = Monday) x hour, Hong Kong time.
SELECT
    ISODOW(m.ts_local)                              AS weekday,
    HOUR(m.ts_local)                                AS hour,
    COUNT(DISTINCT m.session_id || '/' || m.msg_id) AS prompts
FROM messages AS m
JOIN sessions AS s USING (session_id)
WHERE m.is_human_input
GROUP BY ALL
ORDER BY weekday, hour;
