-- Distribution of prompts per session, in buckets, per platform.
SELECT
    platform,
    CASE
        WHEN user_turns = 1   THEN '1'
        WHEN user_turns = 2   THEN '2'
        WHEN user_turns <= 5  THEN '3-5'
        WHEN user_turns <= 10 THEN '6-10'
        WHEN user_turns <= 30 THEN '11-30'
        ELSE '31+'
    END                                                                     AS bucket,
    MIN(user_turns)                                                         AS bucket_order,
    COUNT(*)                                                                AS sessions,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY platform), 1) AS pct
FROM sessions
GROUP BY platform, bucket
ORDER BY platform, bucket_order;
