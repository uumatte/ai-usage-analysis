-- Sessions the user came back to on a later day are usually real projects.
-- lift = how over-represented a topic is among those multi-day sessions.
WITH topic_counts AS (
    SELECT
        t.tag                                        AS topic,
        COUNT(*)                                     AS sessions,
        COUNT(*) FILTER (WHERE s.duration_days >= 1) AS multi_day_sessions
    FROM session_tags AS t
    JOIN sessions AS s USING (session_id)
    WHERE t.tag_type = 'topic'
    GROUP BY t.tag
),
totals AS (
    SELECT
        COUNT(*)                                   AS n,
        COUNT(*) FILTER (WHERE duration_days >= 1) AS n_multi_day
    FROM sessions
)
SELECT
    topic,
    sessions,
    multi_day_sessions,
    ROUND(100.0 * multi_day_sessions / sessions, 1)                               AS multi_day_pct,
    ROUND((multi_day_sessions::DOUBLE / n_multi_day) / (sessions::DOUBLE / n), 2) AS lift
FROM topic_counts
CROSS JOIN totals
WHERE sessions >= 10
ORDER BY lift DESC, sessions DESC;
