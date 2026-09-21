"""Build Data/aiusage.duckdb: runs every sql/NN_*.sql in name order.

    python -m aiusage.build_db
"""
from __future__ import annotations

import duckdb

from aiusage.common import ROOT

DB_PATH = ROOT / "Data" / "aiusage.duckdb"
SQL_DIR = ROOT / "sql"


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(DB_PATH), read_only=read_only)
    # Resolve relative file paths in SQL against the project folder.
    con.execute(f"SET file_search_path = '{ROOT.as_posix()}'")
    return con


def build() -> None:
    con = connect()
    for sql_file in sorted(SQL_DIR.glob("[0-9][0-9]_*.sql")):
        con.execute(sql_file.read_text(encoding="utf-8"))
        print(f"ran {sql_file.name}")
    for table in ("sessions_meta", "messages", "session_tags", "sessions"):
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {n:,} rows")
    con.close()


if __name__ == "__main__":
    build()
