-- For common topics, which AI vendor was used? Division of labour or habit?
WITH counts AS (
    SELECT
        t.tag    AS topic,
        s.session_ai,
        COUNT(*) AS sessions
    FROM session_tags AS t
    JOIN sessions AS s USING (session_id)
    WHERE t.tag_type = 'topic'
    GROUP BY t.tag, s.session_ai
)
SELECT
    topic,
    session_ai,
    sessions,
    SUM(sessions) OVER (PARTITION BY topic)                              AS topic_sessions,
    ROUND(100.0 * sessions / SUM(sessions) OVER (PARTITION BY topic), 1) AS pct_of_topic
FROM counts
QUALIFY SUM(sessions) OVER (PARTITION BY topic) >= 15
ORDER BY topic_sessions DESC, topic, sessions DESC;
