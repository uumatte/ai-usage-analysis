-- Share of each AI vendor in the prompts of each month.
-- The window function SUM(...) OVER (PARTITION BY month) puts the month's
-- total on every row, so each row can be turned into a percentage.
WITH monthly AS (
    SELECT
        DATE_TRUNC('month', m.ts_local)::DATE           AS month,
        s.session_ai,
        COUNT(DISTINCT m.session_id || '/' || m.msg_id) AS prompts
    FROM messages AS m
    JOIN sessions AS s USING (session_id)
    WHERE m.is_human_input
      AND m.ts_local >= DATE '2026-01-01'
    GROUP BY ALL
)
SELECT
    month,
    session_ai,
    prompts,
    ROUND(100.0 * prompts / SUM(prompts) OVER (PARTITION BY month), 1) AS share_pct
FROM monthly
ORDER BY month, prompts DESC;
