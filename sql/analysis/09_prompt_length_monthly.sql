-- Do prompts get shorter or longer over time? Median characters per prompt.
WITH prompts AS (
    SELECT
        m.session_id,
        m.msg_id,
        s.source,
        MIN(m.ts_local) AS ts_local,
        SUM(m.n_chars)  AS chars
    FROM messages AS m
    JOIN sessions AS s USING (session_id)
    WHERE m.is_human_input
    GROUP BY m.session_id, m.msg_id, s.source
)
SELECT
    DATE_TRUNC('month', ts_local)::DATE AS month,
    source,
    COUNT(*)                            AS prompts,
    MEDIAN(chars)                       AS median_chars
FROM prompts
WHERE ts_local >= DATE '2026-01-01'
GROUP BY ALL
ORDER BY month, source;
