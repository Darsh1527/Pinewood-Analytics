"""Pinewood Analytics API — exposes the Gold layer with role-based access.

Run:    uvicorn api.main:app --reload
Docs:   http://127.0.0.1:8000/docs   (OpenAPI/Swagger, free with FastAPI)

Every endpoint requires a bearer token (see api/generate_tokens.py) and every
query is filtered server-side to the communities the token is entitled to.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date

import duckdb
from fastapi import Depends, FastAPI, HTTPException, Query

from pipeline import config
from . import auth

_con: duckdb.DuckDBPyConnection | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _con
    # read_only: the API must never mutate the warehouse
    _con = duckdb.connect(str(config.DB_PATH), read_only=True)
    yield
    _con.close()


app = FastAPI(
    title="Pinewood Senior Living Analytics API",
    description="Occupancy, revenue, labor and care-quality metrics from the "
                "Gold layer. All endpoints require a bearer token; results are "
                "filtered to the communities your role is entitled to see.",
    version="1.0.0",
    lifespan=lifespan,
)


def db() -> duckdb.DuckDBPyConnection:
    if _con is None:
        raise HTTPException(503, "Database not ready")
    return _con


def _rows(sql: str, params: list) -> list[dict]:
    cur = db().execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _scope(user: dict) -> list[str]:
    return auth.allowed_communities(user, db())


def _ph(seq) -> str:
    return ",".join("?" for _ in seq)


# --------------------------------------------------------------------- routes


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.get("/me", tags=["meta"])
def me(user: dict = Depends(auth.get_current_user)):
    """Who am I and what can I see? Useful for demo and debugging."""
    return {"user": user["sub"], "role": user["role"],
            "communities": _scope(user)}


@app.get("/occupancy", tags=["metrics"])
def occupancy(
    community_id: str | None = None,
    start: date = Query(default=date.fromisoformat(config.WINDOW_START)),
    end: date = Query(default=date.fromisoformat(config.WINDOW_END)),
    user: dict = Depends(auth.get_current_user),
):
    """Monthly occupancy rate by community (resident-days / available unit-days)."""
    scope = auth.enforce_community_param(community_id, _scope(user))
    return _rows(
        f"""SELECT month_start, community_id, community_name, region,
                   resident_days, unit_count, occupancy_pct
            FROM gold.v_monthly_occupancy
            WHERE community_id IN ({_ph(scope)})
              AND month_start BETWEEN date_trunc('month', ?::DATE) AND ?::DATE
            ORDER BY month_start, community_id""",
        [*scope, start, end],
    )


@app.get("/move-outs/reasons", tags=["metrics"])
def moveout_reasons(
    community_id: str | None = None,
    period: str = Query(default="12m", pattern="^(3m|6m|12m)$",
                        description="trailing window: 3m, 6m or 12m"),
    user: dict = Depends(auth.get_current_user),
):
    """Move-out reasons as % of total move-outs, trailing period."""
    scope = auth.enforce_community_param(community_id, _scope(user))
    months = {"3m": 3, "6m": 6, "12m": 12}[period]
    return _rows(
        f"""WITH anchor AS (SELECT MAX(date_key) AS max_d FROM gold.fact_resident_day),
            mo AS (
                SELECT l.community_id, COALESCE(l.move_out_reason,'Unknown') AS reason
                FROM gold.fact_lease l CROSS JOIN anchor a
                WHERE l.move_out_date IS NOT NULL
                  AND l.move_out_date > a.max_d - INTERVAL {months} MONTH
                  AND l.community_id IN ({_ph(scope)}))
            SELECT community_id, reason, COUNT(*) AS move_outs,
                   ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY community_id), 1)
                       AS pct_of_moveouts
            FROM mo GROUP BY 1, 2 ORDER BY 1, 3 DESC""",
        scope,
    )


@app.get("/incidents/summary", tags=["metrics"])
def incidents_summary(
    region: str | None = None,
    start: date = Query(default=date.fromisoformat(config.WINDOW_START)),
    end: date = Query(default=date.fromisoformat(config.WINDOW_END)),
    user: dict = Depends(auth.get_current_user),
):
    """Incident counts, severity mix and rate per 100 resident-days by community."""
    scope = _scope(user)
    if region is not None:
        region_scope = [r[0] for r in db().execute(
            "SELECT community_id FROM gold.dim_community WHERE region = ?", [region]
        ).fetchall()]
        scope = [c for c in scope if c in region_scope]
        if not scope:
            raise HTTPException(403, f"Not authorized for region {region}")
    return _rows(
        f"""WITH census AS (
                SELECT community_id, COUNT(*) AS resident_days
                FROM gold.fact_resident_day
                WHERE date_key BETWEEN ? AND ? AND community_id IN ({_ph(scope)})
                GROUP BY 1),
            inc AS (
                SELECT community_id, COUNT(*) AS incidents,
                       ROUND(AVG(severity), 2) AS avg_severity,
                       SUM(CASE WHEN severity >= 4 THEN 1 ELSE 0 END) AS severe_incidents
                FROM gold.fact_incident
                WHERE date_key BETWEEN ? AND ? AND community_id IN ({_ph(scope)})
                GROUP BY 1)
            SELECT c.community_id, dc.community_name, dc.region,
                   COALESCE(i.incidents, 0) AS incidents,
                   i.avg_severity, COALESCE(i.severe_incidents, 0) AS severe_incidents,
                   c.resident_days,
                   ROUND(100.0 * COALESCE(i.incidents, 0) / c.resident_days, 2)
                       AS incidents_per_100_resident_days
            FROM census c
            LEFT JOIN inc i USING (community_id)
            JOIN gold.dim_community dc USING (community_id)
            ORDER BY incidents_per_100_resident_days DESC""",
        [start, end, *scope, start, end, *scope],
    )


@app.get("/labor/cost", tags=["metrics"])
def labor_cost(
    community_id: str | None = None,
    start: date = Query(default=date.fromisoformat(config.WINDOW_START)),
    end: date = Query(default=date.fromisoformat(config.WINDOW_END)),
    user: dict = Depends(auth.get_current_user),
):
    """Monthly labor cost, hours, and cost per resident-day."""
    scope = auth.enforce_community_param(community_id, _scope(user))
    return _rows(
        f"""SELECT month_start, community_id, community_name, region,
                   labor_cost, labor_hours, resident_days, labor_cost_per_resident_day
            FROM gold.v_labor_cost_per_resident_day
            WHERE community_id IN ({_ph(scope)})
              AND month_start BETWEEN date_trunc('month', ?::DATE) AND ?::DATE
            ORDER BY month_start, community_id""",
        [*scope, start, end],
    )


@app.get("/reviews/summary", tags=["metrics"])
def reviews_summary(
    community_id: str | None = None,
    start: date = Query(default=date.fromisoformat(config.WINDOW_START)),
    end: date = Query(default=date.fromisoformat(config.WINDOW_END)),
    user: dict = Depends(auth.get_current_user),
):
    """Review volume, average rating and response performance by month."""
    scope = auth.enforce_community_param(community_id, _scope(user))
    return _rows(
        f"""SELECT month_start, community_id, community_name, region,
                   review_count, avg_rating, responded_count, avg_response_days
            FROM gold.v_reviews_summary
            WHERE community_id IN ({_ph(scope)})
              AND month_start BETWEEN date_trunc('month', ?::DATE) AND ?::DATE
            ORDER BY month_start, community_id""",
        [*scope, start, end],
    )
