-- Is exploration slowing down? New topic tags per week and the running total.
WITH first_seen AS (
    SELECT
        t.tag,
        DATE_TRUNC('week', MIN(s.created_at_local))::DATE AS week
    FROM session_tags AS t
    JOIN sessions AS s USING (session_id)
    WHERE t.tag_type = 'topic'
      AND s.created_at_local >= DATE '2026-01-01'
    GROUP BY t.tag
)
SELECT
    week,
    COUNT(*)                              AS new_topics,
    SUM(COUNT(*)) OVER (ORDER BY week)    AS cumulative_topics
FROM first_seen
GROUP BY week
ORDER BY week;
