"""Export Gold tables and views to CSV for Power BI import.

Power BI Desktop (on the Windows VM) imports these files directly — no ODBC
driver needed. Re-running the pipeline refreshes the files; Power BI refresh
picks up the new data.
"""
from __future__ import annotations

import duckdb

from . import config

EXPORT_OBJECTS = [
    # star schema
    "dim_date", "dim_community", "dim_unit", "dim_resident",
    "dim_resident_care_level",
    "fact_resident_day", "fact_monthly_revenue", "fact_shift",
    "fact_incident", "fact_lease", "fact_review", "fact_lead",
    # convenience views
    "v_monthly_occupancy", "v_labor_cost_per_resident_day",
    "v_incident_rate_by_community", "v_incident_rate_by_care_level",
    "v_top_moveout_reasons", "v_avg_los_by_care_level",
    "v_care_level_review_candidates", "v_reviews_summary",
]


def export_gold(con: duckdb.DuckDBPyConnection) -> dict:
    config.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    stats = {}
    for obj in EXPORT_OBJECTS:
        target = config.EXPORT_DIR / f"{obj}.csv"
        con.execute(f"COPY (SELECT * FROM gold.{obj}) TO '{target}' (HEADER, DELIMITER ',')")
        stats[obj] = str(target.name)
    return stats
