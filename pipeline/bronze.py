"""Bronze layer: land raw CSVs exactly as they arrived, plus lineage metadata.

Design:
- Every column is loaded as VARCHAR. No typing, no cleaning. Bronze is an
  audit copy of the source.
- Each row gets _source_file, _source_system, _ingested_at, _batch_id.
- Idempotent + incremental: a file registry stores an MD5 of every file
  already loaded. Unchanged files are skipped; changed files are replaced
  (delete-by-file then insert), so reruns never duplicate rows.
- Schema drift tolerant: new columns in newer files trigger ALTER TABLE
  ADD COLUMN, never a crash.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

import duckdb
import pandas as pd

from . import config

FILE_RE = re.compile(r"^([a-z_]+)_(\d{4})_(\d{2})\.csv$")


def _md5(path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _ensure_registry(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    con.execute(
        """CREATE TABLE IF NOT EXISTS meta.file_registry (
               file_name  VARCHAR PRIMARY KEY,
               file_hash  VARCHAR NOT NULL,
               table_name VARCHAR NOT NULL,
               row_count  BIGINT,
               loaded_at  TIMESTAMP
           )"""
    )


def _sync_columns(con, table: str, df_cols: list[str]) -> None:
    """Add any columns present in the file but missing from the table (drift)."""
    existing = {
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='bronze' AND table_name=?",
            [table],
        ).fetchall()
    }
    for col in df_cols:
        if col not in existing:
            con.execute(f'ALTER TABLE bronze."{table}" ADD COLUMN "{col}" VARCHAR')


def load_bronze(con: duckdb.DuckDBPyConnection, batch_id: str) -> dict:
    """Land every raw CSV. Returns per-table stats for the run log."""
    _ensure_registry(con)
    stats: dict = {"files_loaded": [], "files_skipped": [], "tables": {}}
    now = datetime.now(timezone.utc).isoformat()

    for path in sorted(config.RAW_DATA_DIR.glob("*.csv")):
        m = FILE_RE.match(path.name)
        if not m:
            stats.setdefault("files_ignored", []).append(path.name)
            continue
        table = m.group(1)
        if table not in config.SOURCE_TABLES:
            stats.setdefault("files_ignored", []).append(path.name)
            continue

        file_hash = _md5(path)
        prev = con.execute(
            "SELECT file_hash FROM meta.file_registry WHERE file_name=?", [path.name]
        ).fetchone()
        if prev and prev[0] == file_hash:
            stats["files_skipped"].append(path.name)  # incremental: already landed
            continue

        # read everything as string; empty string -> NULL
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        df = df.replace({"": None})
        df["_source_file"] = path.name
        df["_source_system"] = config.SOURCE_TABLES[table]
        df["_ingested_at"] = now
        df["_batch_id"] = batch_id

        cols = ", ".join(f'"{c}" VARCHAR' for c in df.columns)
        con.execute(f'CREATE TABLE IF NOT EXISTS bronze."{table}" ({cols})')
        _sync_columns(con, table, list(df.columns))

        # replace-by-file keeps reruns idempotent even if a file was corrected
        con.execute(f'DELETE FROM bronze."{table}" WHERE _source_file = ?', [path.name])
        col_list = ", ".join(f'"{c}"' for c in df.columns)
        con.register("_incoming", df)
        con.execute(f'INSERT INTO bronze."{table}" ({col_list}) SELECT {col_list} FROM _incoming')
        con.unregister("_incoming")

        con.execute(
            "INSERT OR REPLACE INTO meta.file_registry VALUES (?,?,?,?,now())",
            [path.name, file_hash, table, len(df)],
        )
        stats["files_loaded"].append({"file": path.name, "rows": len(df)})

    for table in config.SOURCE_TABLES:
        try:
            n = con.execute(f'SELECT count(*) FROM bronze."{table}"').fetchone()[0]
        except duckdb.CatalogException:
            n = 0
        stats["tables"][table] = n
    return stats
