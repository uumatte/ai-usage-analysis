-- Load the Parquet files written by the parsers and the tagging step.
-- Paths are relative to the project folder (build_db.py sets file_search_path).
-- The * wildcard reads all five platforms at once: every parser writes the
-- same columns, so the files stack into one table.

CREATE OR REPLACE TABLE sessions_meta AS
SELECT * FROM read_parquet('Data/processed/sessions_meta_*.parquet');

CREATE OR REPLACE TABLE messages AS
SELECT * FROM read_parquet('Data/processed/messages_*.parquet');

CREATE OR REPLACE TABLE session_tags AS
SELECT * FROM read_parquet('Data/processed/session_tags.parquet');
