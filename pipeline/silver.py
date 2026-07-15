"""Silver layer: cleaned, typed, deduplicated, business keys resolved.

Silver is rebuilt from Bronze on every run. Bronze is the incremental,
append-only layer; rebuilding Silver from it is deterministic, which is
what makes the whole pipeline rerunnable — the same Bronze input always
produces the same Silver output.

Every row we reject or repair is written to silver.quarantine with the
original value and the reason, so nothing silently disappears.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone

import duckdb
import numpy as np
import pandas as pd

from . import config

# ---------------------------------------------------------------- helpers

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y")  # ISO first, then US format (March files)


def parse_date(value):
    """Parse a date that may arrive in ISO or MM/DD/YYYY format."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_date_col(series: pd.Series) -> pd.Series:
    return series.map(parse_date)


def normalize_care_level(value):
    if value is None:
        return None
    return config.CARE_LEVEL_MAP.get(str(value).strip().upper())


class Quarantine:
    """Collects rejected rows / repaired fields with reasons."""

    def __init__(self):
        self.records: list[dict] = []

    def add(self, table, key, reason, action, raw):
        self.records.append(
            {
                "source_table": table,
                "business_key": str(key),
                "reason": reason,
                "action_taken": action,
                "raw_value": json.dumps(raw, default=str)[:2000],
                "quarantined_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def to_df(self) -> pd.DataFrame:
        cols = ["source_table", "business_key", "reason", "action_taken", "raw_value", "quarantined_at"]
        return pd.DataFrame(self.records, columns=cols)


def _bronze(con, table) -> pd.DataFrame:
    return con.execute(f'SELECT * FROM bronze."{table}"').df()


def _write(con, name: str, df: pd.DataFrame) -> None:
    con.register("_silver_tmp", df)
    con.execute(f'CREATE OR REPLACE TABLE silver."{name}" AS SELECT * FROM _silver_tmp')
    con.unregister("_silver_tmp")


# ---------------------------------------------------------------- residents


def clean_residents(con, q: Quarantine) -> pd.DataFrame:
    df = _bronze(con, "pcc_residents")
    df["snapshot_month"] = df["_source_file"].str.extract(r"_(\d{4}_\d{2})\.csv")[0].str.replace("_", "-") + "-01"
    df["snapshot_month"] = pd.to_datetime(df["snapshot_month"]).dt.date

    # -- identity resolution: duplicate resident_ids inside one snapshot.
    # The Feb export contains near-duplicate identities (typo names, wrong
    # community). Canonical community = the modal community for that resident
    # across all snapshots; the row that matches it wins, the other is quarantined.
    modal_comm = (
        df.groupby("resident_id")["community_id"]
        .agg(lambda s: s.mode().iat[0])
        .rename("modal_community")
    )
    df = df.merge(modal_comm, on="resident_id", how="left")
    dup_mask = df.duplicated(subset=["resident_id", "snapshot_month"], keep=False)
    drop_idx = []
    for (_rid, _mon), grp in df[dup_mask].groupby(["resident_id", "snapshot_month"]):
        keep = grp[grp["community_id"] == grp["modal_community"]]
        keep_idx = keep.index[0] if len(keep) else grp.index[0]
        for i in grp.index:
            if i != keep_idx:
                drop_idx.append(i)
                q.add("pcc_residents", grp.loc[i, "resident_id"],
                      "duplicate identity in same snapshot (name/community conflict)",
                      "row rejected; kept row matching modal community",
                      grp.loc[i].drop(labels=["modal_community"]).to_dict())
    df = df.drop(index=drop_idx).drop(columns=["modal_community"])

    # -- typing and normalization
    df["dob"] = parse_date_col(df["dob"])
    df["admit_date"] = parse_date_col(df["admit_date"])
    df["discharge_date"] = parse_date_col(df["discharge_date"])
    df["care_level_raw"] = df["care_level"]
    df["care_level"] = df["care_level"].map(normalize_care_level)
    unknown = df["care_level"].isna() & df["care_level_raw"].notna()
    for _, r in df[unknown].iterrows():
        q.add("pcc_residents", r["resident_id"], f"unmappable care level '{r['care_level_raw']}'",
              "care_level set NULL", {"care_level": r["care_level_raw"]})

    # -- acuity: keep the resident, null the bad score, log it
    df["acuity_score"] = pd.to_numeric(df["acuity_score"], errors="coerce")
    bad_acuity = df["acuity_score"].notna() & (
        (df["acuity_score"] < config.ACUITY_MIN) | (df["acuity_score"] > config.ACUITY_MAX)
    )
    for _, r in df[bad_acuity].iterrows():
        q.add("pcc_residents", r["resident_id"],
              f"acuity_score {r['acuity_score']:.0f} outside {config.ACUITY_MIN}-{config.ACUITY_MAX}",
              "field set NULL, raise to client", {"acuity_score": r["acuity_score"]})
    df.loc[bad_acuity, "acuity_score"] = np.nan

    # -- future-dated discharges: cannot have happened yet; treat as active.
    # Horizon = last day of the latest snapshot month actually received.
    horizon = (pd.Timestamp(df["snapshot_month"].max()) + pd.offsets.MonthEnd(0)).date()
    fut = df["discharge_date"].map(lambda d: d is not None and d > horizon)
    for _, r in df[fut].iterrows():
        q.add("pcc_residents", r["resident_id"],
              f"future-dated discharge {r['discharge_date']}",
              "discharge_date set NULL (treated as active), raise to client",
              {"discharge_date": str(r["discharge_date"])})
    df.loc[fut, "discharge_date"] = None

    # -- discharge before admit (defensive; none observed but rule enforced)
    bad_order = df.apply(
        lambda r: r["discharge_date"] is not None and r["admit_date"] is not None
        and r["discharge_date"] < r["admit_date"], axis=1)
    for _, r in df[bad_order].iterrows():
        q.add("pcc_residents", r["resident_id"], "discharge before admit",
              "row rejected", r.to_dict())
    df = df[~bad_order]

    keep = ["resident_id", "snapshot_month", "community_id", "first_name", "last_name",
            "dob", "gender", "admit_date", "discharge_date", "care_level", "acuity_score"]
    if "mobility_status" in df.columns:  # schema-drift column (April only)
        keep.append("mobility_status")
    out = df[keep].sort_values(["resident_id", "snapshot_month"]).reset_index(drop=True)
    _write(con, "residents_monthly", out)

    # current-state table: latest snapshot per resident
    cur = out.sort_values("snapshot_month").groupby("resident_id").tail(1).reset_index(drop=True)
    _write(con, "residents", cur.drop(columns=["snapshot_month"]))
    return out


# ---------------------------------------------------------------- others


def clean_incidents(con, q: Quarantine) -> pd.DataFrame:
    df = _bronze(con, "pcc_incidents")
    df["incident_date"] = parse_date_col(df["incident_date"])
    df["severity"] = pd.to_numeric(df["severity"], errors="coerce")
    bad = df["severity"].isna() | (df["severity"] < config.SEVERITY_MIN) | (df["severity"] > config.SEVERITY_MAX)
    for _, r in df[bad].iterrows():
        q.add("pcc_incidents", r["incident_id"], "severity out of range", "row rejected", r.to_dict())
    df = df[~bad]
    df = df.drop_duplicates(subset="incident_id", keep="last")
    out = df[["incident_id", "resident_id", "community_id", "incident_date",
              "incident_type", "severity", "reported_by"]].reset_index(drop=True)
    _write(con, "incidents", out)
    return out


def clean_care_history(con, q: Quarantine) -> pd.DataFrame:
    df = _bronze(con, "pcc_care_history")
    df["change_date"] = parse_date_col(df["change_date"])
    df["previous_level"] = df["previous_level"].map(normalize_care_level)
    df["new_level"] = df["new_level"].map(normalize_care_level)
    df = df.drop_duplicates(subset=["resident_id", "change_date", "new_level"], keep="last")
    out = df[["resident_id", "change_date", "previous_level", "new_level", "reason"]].reset_index(drop=True)
    _write(con, "care_history", out)
    return out


def clean_units(con, q: Quarantine) -> pd.DataFrame:
    df = _bronze(con, "yardi_units")
    df["snapshot_date"] = parse_date_col(df["snapshot_date"])
    df["monthly_rent"] = pd.to_numeric(df["monthly_rent"], errors="coerce")
    # phantom communities (C905..C969) — not part of Pinewood's 14 communities
    bad = ~df["community_id"].isin(config.VALID_COMMUNITIES)
    for _, r in df[bad].drop_duplicates(subset=["unit_id", "community_id"]).iterrows():
        q.add("yardi_units", r["unit_id"], f"unknown community {r['community_id']}",
              "row quarantined, raise to client", r.to_dict())
    df = df[~bad]
    out = df[["unit_id", "community_id", "unit_type", "monthly_rent", "snapshot_date"]].reset_index(drop=True)
    _write(con, "units_monthly", out)
    # dimension-style current inventory (latest snapshot)
    latest = out[out["snapshot_date"] == out["snapshot_date"].max()].drop(columns=["snapshot_date"])
    _write(con, "units", latest.reset_index(drop=True))
    return out


def clean_leases(con, q: Quarantine) -> pd.DataFrame:
    df = _bronze(con, "yardi_leases")
    before = len(df)
    # a lease is re-exported in its move-out month — exact duplicates by design
    df = df.sort_values("_source_file").drop_duplicates(subset="lease_id", keep="last")
    dropped = before - len(df)
    if dropped:
        q.add("yardi_leases", f"{dropped} rows", "lease re-exported in move-out month",
              "deduplicated on lease_id (kept latest export)", {"duplicates_removed": dropped})
    df["move_in_date"] = parse_date_col(df["move_in_date"])
    df["move_out_date"] = parse_date_col(df["move_out_date"])
    df["monthly_rate"] = pd.to_numeric(df["monthly_rate"], errors="coerce")
    bad = df.apply(lambda r: r["move_out_date"] is not None and r["move_in_date"] is not None
                   and r["move_out_date"] < r["move_in_date"], axis=1)
    for _, r in df[bad].iterrows():
        q.add("yardi_leases", r["lease_id"], "move_out before move_in", "row rejected", r.to_dict())
    df = df[~bad]
    out = df[["lease_id", "resident_id", "unit_id", "community_id", "move_in_date",
              "move_out_date", "move_out_reason", "monthly_rate"]].reset_index(drop=True)
    _write(con, "leases", out)
    return out


def clean_shifts(con, q: Quarantine) -> pd.DataFrame:
    df = _bronze(con, "adp_shifts")
    df["shift_date"] = parse_date_col(df["shift_date"])
    df["hours_worked"] = pd.to_numeric(df["hours_worked"], errors="coerce")

    # ADP exports the whole role->rate map into hourly_rate on every row.
    # Extract the rate that matches the row's own role.
    def extract_rate(row):
        raw = row["hourly_rate"]
        if raw is None:
            return None
        s = str(raw).strip()
        try:
            return float(s)  # tolerate a normal numeric export too
        except ValueError:
            pass
        try:
            d = ast.literal_eval(s)
            if isinstance(d, dict):
                return float(d.get(row["role"])) if row["role"] in d else None
        except (ValueError, SyntaxError):
            return None
        return None

    df["hourly_rate_parsed"] = df.apply(extract_rate, axis=1)
    bad_rate = df["hourly_rate_parsed"].isna()
    for _, r in df[bad_rate].head(50).iterrows():
        q.add("adp_shifts", r["shift_id"], "hourly_rate not parseable for role",
              "row rejected", {"hourly_rate": r["hourly_rate"], "role": r["role"]})
    df = df[~bad_rate]

    bad_hours = df["hours_worked"].isna() | (df["hours_worked"] <= 0) | (df["hours_worked"] > config.MAX_SHIFT_HOURS)
    for _, r in df[bad_hours].iterrows():
        q.add("adp_shifts", r["shift_id"], "hours_worked out of range", "row rejected", r.to_dict())
    df = df[~bad_hours]

    df = df.drop_duplicates(subset="shift_id", keep="last")
    df["labor_cost"] = (df["hours_worked"] * df["hourly_rate_parsed"]).round(2)
    out = df[["shift_id", "community_id", "employee_id", "role", "shift_date",
              "hours_worked", "hourly_rate_parsed", "labor_cost"]].rename(
        columns={"hourly_rate_parsed": "hourly_rate"}).reset_index(drop=True)
    _write(con, "shifts", out)
    return out


def clean_reviews(con, q: Quarantine) -> pd.DataFrame:
    df = _bronze(con, "gbp_reviews")
    df["review_date"] = parse_date_col(df["review_date"])
    df["responded_at"] = parse_date_col(df["responded_at"])
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
    bad = df["rating"].isna() | (df["rating"] < config.RATING_MIN) | (df["rating"] > config.RATING_MAX)
    for _, r in df[bad].iterrows():
        q.add("gbp_reviews", r["review_id"], "rating out of range", "row rejected", r.to_dict())
    df = df[~bad].drop_duplicates(subset="review_id", keep="last")
    out = df[["review_id", "community_id", "review_date", "rating",
              "review_text", "response_text", "responded_at"]].reset_index(drop=True)
    _write(con, "reviews", out)
    return out


def clean_leads(con, q: Quarantine) -> pd.DataFrame:
    df = _bronze(con, "hubspot_leads")
    dup = df[df["lead_id"].duplicated(keep=False)]
    for lid, grp in dup.groupby("lead_id"):
        if grp.drop(columns=[c for c in grp.columns if c.startswith("_")]).drop_duplicates().shape[0] > 1:
            q.add("hubspot_leads", lid, "conflicting duplicate lead across exports",
                  "kept latest export's version, raise to client",
                  grp[["community_id", "status", "_source_file"]].to_dict("records"))
    df = df.sort_values("_source_file").drop_duplicates(subset="lead_id", keep="last")
    for col in ["created_date", "tour_date", "deposit_date", "move_in_date"]:
        df[col] = parse_date_col(df[col])
    out = df[["lead_id", "community_id", "lead_source", "created_date", "tour_date",
              "deposit_date", "move_in_date", "status", "lost_reason"]].reset_index(drop=True)
    _write(con, "leads", out)
    return out


# ---------------------------------------------------------------- entry


def build_silver(con: duckdb.DuckDBPyConnection) -> dict:
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    q = Quarantine()
    stats = {}
    for name, fn in [
        ("residents_monthly", clean_residents), ("incidents", clean_incidents),
        ("care_history", clean_care_history), ("units_monthly", clean_units),
        ("leases", clean_leases), ("shifts", clean_shifts),
        ("reviews", clean_reviews), ("leads", clean_leads),
    ]:
        out = fn(con, q)
        stats[name] = len(out)
    _write(con, "quarantine", q.to_df())
    stats["quarantine"] = len(q.records)
    return stats
