"""Gold layer: star schema build. All transformation logic lives in /sql
so it can be reviewed as SQL; this module only orchestrates."""
from __future__ import annotations

import duckdb

from . import config


def _analysis_window(con) -> tuple[str, str]:
    """Derive the analysis window from the data itself (first snapshot month
    through the last day of the latest snapshot month), so census facts are
    never extrapolated beyond the months actually received. Falls back to
    config if silver is empty. The window is persisted to meta.analysis_window
    so the validation layer reconciles against the same boundaries."""
    row = con.execute(
        "SELECT MIN(snapshot_month), last_day(MAX(snapshot_month)) "
        "FROM silver.residents_monthly"
    ).fetchone()
    start = str(row[0] or config.WINDOW_START)
    end = str(row[1] or config.WINDOW_END)
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    con.execute(
        "CREATE OR REPLACE TABLE meta.analysis_window AS "
        f"SELECT DATE '{start}' AS window_start, DATE '{end}' AS window_end"
    )
    return start, end


def _run_sql_file(con: duckdb.DuckDBPyConnection, path, window=None) -> None:
    sql = path.read_text()
    start, end = window or (config.WINDOW_START, config.WINDOW_END)
    sql = sql.replace(":window_start", f"DATE '{start}'")
    sql = sql.replace(":window_end", f"DATE '{end}'")
    con.execute(sql)


def build_gold(con: duckdb.DuckDBPyConnection) -> dict:
    window = _analysis_window(con)
    _run_sql_file(con, config.SQL_DIR / "gold_ddl.sql")

    # dim_community is seeded from config: no master file exists in the sources
    # (documented assumption — replace with the client's real mapping later).
    con.executemany(
        "INSERT INTO gold.dim_community VALUES (?,?,?,?,?)",
        [(cid, *attrs) for cid, attrs in config.COMMUNITY_MASTER.items()],
    )

    _run_sql_file(con, config.SQL_DIR / "gold_load.sql", window)
    _run_sql_file(con, config.SQL_DIR / "views.sql", window)

    stats = {}
    for (t,) in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='gold' AND table_type='BASE TABLE'"
    ).fetchall():
        stats[t] = con.execute(f'SELECT count(*) FROM gold."{t}"').fetchone()[0]
    return stats
