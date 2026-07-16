# Pinewood Senior Living — Analytics Platform

End-to-end analytics platform for the Skypoint SDE assessment: messy CSV
extracts from five source systems → Bronze/Silver/Gold warehouse (DuckDB) →
validation framework → authenticated FastAPI service → Power BI executive
dashboard with row-level security.


```
raw CSVs → Python ingestion → Bronze / Silver / Gold (DuckDB)
        → validation report → FastAPI (JWT + RBAC)
        → Power BI (star schema import, DAX, RLS)
```

## Quick start (fresh machine)

Requires Python 3.10+.

```bash
git clone <this repo> && cd pinewood-analytics
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 1. put the dataset in place
unzip Pinewood_Dataset.zip -d /tmp/pinewood_src
cp /tmp/pinewood_src/candidate_package/data/*.csv data/raw/

# 2. run the pipeline (single command: bronze -> silver -> gold -> validation -> export)
python run_pipeline.py

# 3. generate the three test tokens (admin / regional director / executive director)
python -m api.generate_tokens

# 4. start the API  ->  http://127.0.0.1:8000/docs
uvicorn api.main:app --reload
```

Outputs of a run: `data/pinewood.duckdb` (warehouse), `logs/run_latest.json`
(run log: rows in/out/rejected, timings, anomalies), `validation/report_latest.md`
(COO-readable validation report), `powerbi/data/*.csv` (Gold export for Power BI).

Try the API:

```bash
export TOKEN=$(python -c "import json;print(json.load(open('api/test_tokens.json'))[0]['token'])")
curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8000/occupancy?community_id=C001"
```

## Repo layout

```
/pipeline          ingestion + transform code (bronze.py, silver.py, gold.py, export.py)
/sql               gold_ddl.sql (star schema DDL), gold_load.sql, views.sql
/api               FastAPI service, JWT auth, token generator
/powerbi           BUILD_GUIDE.md, exported gold CSVs, .pbix
/validation        validation module + latest report
/communication     email to IT, CFO incident response
/docs              ANOMALIES.md, STUDY_GUIDE.md, WALKTHROUGH_SCRIPT.md
run_pipeline.py    single-command entry point
```

## Architecture decisions (the short version)

**DuckDB** over SQLite/Parquet: single local file, real analytical SQL
(window functions used by SCD2 and the required views), native CSV export for
Power BI, zero infrastructure. The API opens it read-only.

**Bronze is incremental, Silver/Gold are rebuilt.** Bronze keeps an MD5 file
registry — already-landed files are skipped, changed files are replaced, new
monthly files land without touching the rest (drop next month's CSVs into
`data/raw` and rerun). Silver and Gold rebuild deterministically from Bronze
each run. At this data volume (~90k rows) a deterministic rebuild is simpler,
safer, and easier to reason about than incremental merge logic in every layer;
the incremental boundary sits where the expensive, stateful part is — file
ingestion.

**Schema drift** is absorbed at Bronze: every column lands as VARCHAR and new
columns (e.g. `mobility_status`, which appears only in April) trigger
`ALTER TABLE ADD COLUMN` instead of a crash.

**Nothing silently disappears.** Every rejected row or repaired field goes to
`silver.quarantine` with a reason and the original value; the validation
report summarizes it per run.

**Census-based occupancy.** The Yardi lease extract only includes leases
created or closed in-month, so it cannot reconstruct historical unit
occupancy. Occupancy = resident-days (PCC census) / unit-days (Yardi unit
snapshots). Couples sharing a unit slightly inflate the numerator; flagged to
the client as a definitional decision to ratify.

**Community master data doesn't exist in the sources** (the data dictionary
says derive or hard-code). `pipeline/config.py` seeds `dim_community` with a
documented, easily-replaced mapping: C001–C005 = OR (Pacific Northwest),
C006–C010 = AZ (Southwest), C011–C014 = TX (South).

## Star schema

Facts (grain in `sql/gold_ddl.sql` comments and below):

| Fact | Grain |
|---|---|
| fact_resident_day | one row per resident per day in census (atomic occupancy grain) |
| fact_monthly_revenue | one row per lease per month, revenue prorated by active days |
| fact_shift | one row per worked shift |
| fact_incident | one row per incident |
| fact_lease | one row per lease (accumulating snapshot) |
| fact_review | one row per Google review |
| fact_lead | one row per CRM lead (funnel accumulating snapshot) |

Dimensions: `dim_date`, `dim_community` (conformed across all facts),
`dim_unit`, `dim_resident`, and `dim_resident_care_level` — the **SCD Type 2**
care-level dimension: a resident moving AL → MC has two rows with
`effective_from`/`effective_to` ranges and `is_current`; history is preserved,
and fact_resident_day joins to the row valid on each census day.

Required views (`sql/views.sql`): monthly occupancy by community; average LOS
by care level (12-month discharges); top-3 move-out reasons by community
(% of move-outs, trailing 12m); labor cost per resident-day by community-month;
incident rate per 100 resident-days by community and by care level; care-level
review candidates (acuity +2 within 90 days).

## Anomalies found (13) — full detail in docs/ANOMALIES.md

| # | Anomaly | Handling |
|---|---|---|
| 1 | ADP `hourly_rate` contains the whole role→rate dict as a string, every row | Parse dict, extract the rate matching the row's role |
| 2 | Care-level naming drift from Feb (9 variants: AL/Assisted/Assisted Living…) | Normalized to canonical IL/AL/MC via mapping |
| 3 | March residents file uses MM/DD/YYYY for all dates (683 rows) | Multi-format date parser (ISO first, then US) |
| 4 | Acuity scores −5, 50, 99 (valid 1–10) | Field nulled, logged to quarantine, raise to client |
| 5 | 5 phantom communities (C905–C969) in Yardi units, every month | Rows quarantined; raise to client (likely test/disposed sites) |
| 6 | Near-duplicate resident identities in Feb (typo names, wrong community) | Identity resolution: keep row matching resident's modal community |
| 7 | Duplicate lease rows across monthly exports (44) | By-design re-export; dedup on lease_id keeping latest |
| 8 | Lead HL385264 duplicated with conflicting community & status | Kept latest export's version; flagged to client |
| 9 | Two residents discharged in 2026 (future-dated) | Discharge nulled (treated active), quarantined, raise to client |
| 10 | Schema drift: `mobility_status` column exists only in April | Absorbed at Bronze via dynamic ALTER TABLE; carried as nullable |
| 11 | HubSpot funnel chronology: 12 deposits before tour, 34 move-ins before deposit | Kept, surfaced as validation WARN — may be process reality |
| 12 | No community master data anywhere | Documented hard-coded mapping in config (assumption) |
| 13 | Acuity is static for every resident across all 6 snapshots, while care_history cites "Acuity Increase" as a change reason | Contradiction between PCC tables; the acuity-review view is correctly empty on this data; raise to client |

## API

JWT bearer auth (HS256; secret via `PINEWOOD_JWT_SECRET`, dev default
otherwise). Authorization is enforced **server-side**: the allowed community
list is derived from the signed token's role/region/community claims, never
from query parameters. Out-of-scope requests get 403, unauthenticated get 401.

| Endpoint | Params |
|---|---|
| GET /occupancy | community_id, start, end |
| GET /move-outs/reasons | community_id, period (3m/6m/12m) |
| GET /incidents/summary | region, start, end |
| GET /labor/cost | community_id, start, end |
| GET /reviews/summary | community_id, start, end |

Roles: `corporate_admin` (all 14), `regional_director` (region claim),
`executive_director` (community claim). `python -m api.generate_tokens`
writes all three to `api/test_tokens.json`. Swagger at `/docs`.

## Power BI

See `powerbi/BUILD_GUIDE.md` for the full model, every DAX measure with
explanation (including the context-transition measure), the two RLS roles,
and the dashboard layout. Relationships are single-direction dimension→fact;
dim_date role-plays move-in/move-out on fact_lease. Note: YoY revenue growth
is defined and correct but returns blank on this dataset — the extracts cover
Jan–Jun 2025 only, so there is no prior year to compare against.

## Validation framework

Runs automatically at the end of every pipeline execution
(`python -m validation.validate` standalone). Three families:
row-count reconciliation (source→Bronze→Silver→Gold), aggregate reconciliation
(labor hours/cost, resident-days recomputed independently, revenue proration
bounds; 0.5% tolerance), and business rules (no overlapping leases, occupancy
0–100%, no discharge-before-admit, no future events, ranges on acuity /
severity / rating / hours, SCD2 integrity: non-overlapping ranges, exactly one
current row). Output: `validation/report_latest.md` with severity and a
recommended action (fix in pipeline / quarantine / raise to client) per finding.
