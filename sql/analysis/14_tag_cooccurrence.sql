-- Which topics appear together? A self-join pairs every two topic tags of
-- the same session; a.tag < b.tag keeps each pair once.
-- lift > 1 means the pair occurs more often than if tags were independent.
WITH topic_tags AS (
    SELECT session_id, tag FROM session_tags WHERE tag_type = 'topic'
),
tag_counts AS (
    SELECT tag, COUNT(*) AS n FROM topic_tags GROUP BY tag
),
total AS (
    SELECT COUNT(DISTINCT session_id) AS n_sessions FROM topic_tags
)
SELECT
    a.tag                                                             AS tag_a,
    b.tag                                                             AS tag_b,
    COUNT(*)                                                          AS sessions,
    ROUND(COUNT(*) * ANY_VALUE(total.n_sessions)
          / (ANY_VALUE(ca.n) * ANY_VALUE(cb.n))::DOUBLE, 1)          AS lift
FROM topic_tags AS a
JOIN topic_tags AS b  ON a.session_id = b.session_id AND a.tag < b.tag
JOIN tag_counts AS ca ON ca.tag = a.tag
JOIN tag_counts AS cb ON cb.tag = b.tag
CROSS JOIN total
GROUP BY a.tag, b.tag
HAVING COUNT(*) >= 3
ORDER BY sessions DESC, lift DESC;
