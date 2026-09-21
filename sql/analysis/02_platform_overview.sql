-- Headline numbers per platform: how much was used, and how much text
-- the AI produced for each token the user typed.
SELECT
    platform,
    source,
    COUNT(*)                                                      AS sessions,
    SUM(user_turns)                                               AS prompts,
    MEDIAN(user_turns)                                            AS median_prompts_per_session,
    ROUND(AVG(user_turns), 1)                                     AS avg_prompts_per_session,
    SUM(user_tokens)                                              AS user_tokens,
    SUM(assistant_tokens)                                         AS assistant_tokens,
    SUM(tool_tokens)                                              AS tool_tokens,
    SUM(api_output_tokens)                                        AS api_output_tokens,
    ROUND(SUM(assistant_tokens) / NULLIF(SUM(user_tokens), 0), 1) AS answer_tokens_per_prompt_token
FROM sessions
GROUP BY platform, source
ORDER BY source DESC, sessions DESC;
