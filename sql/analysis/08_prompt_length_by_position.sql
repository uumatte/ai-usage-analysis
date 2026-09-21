-- Is the first prompt of a session longer than the follow-ups?
-- ROW_NUMBER() numbers the prompts inside each session in order.
WITH prompts AS (
    SELECT
        m.session_id,
        s.source,
        m.msg_id,
        MIN(m.seq)     AS first_seq,
        SUM(m.n_chars) AS chars          -- one prompt may have several text blocks
    FROM messages AS m
    JOIN sessions AS s USING (session_id)
    WHERE m.is_human_input
    GROUP BY m.session_id, s.source, m.msg_id
),
numbered AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY first_seq) AS prompt_no
    FROM prompts
)
SELECT
    source,
    prompt_no,
    COUNT(*)             AS prompts,
    MEDIAN(chars)        AS median_chars,
    ROUND(AVG(chars), 1) AS avg_chars
FROM numbered
WHERE prompt_no <= 10
GROUP BY source, prompt_no
ORDER BY source, prompt_no;
