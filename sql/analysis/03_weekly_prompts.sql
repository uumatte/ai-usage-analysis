-- Prompts typed per week and platform (Hong Kong time).
-- A prompt can span several text blocks, so count distinct messages.
-- The single 2023 conversation is left out of the time series.
SELECT
    DATE_TRUNC('week', m.ts_local)::DATE            AS week,
    s.platform,
    COUNT(DISTINCT m.session_id || '/' || m.msg_id) AS prompts
FROM messages AS m
JOIN sessions AS s USING (session_id)
WHERE m.is_human_input
  AND m.ts_local >= DATE '2026-01-01'
GROUP BY ALL
ORDER BY week, s.platform;
