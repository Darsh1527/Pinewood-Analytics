-- ============================================================================
-- Gold layer load. Runs after gold_ddl.sql. Pure INSERT ... SELECT from Silver,
-- so it is fully deterministic and rerunnable (DDL recreates tables each run).
-- dim_community is seeded by the pipeline from config (no master file exists).
-- Parameters :window_start / :window_end are substituted by the pipeline.
-- ============================================================================

-- ---------------------------------------------------------------- dim_date
INSERT INTO gold.dim_date
SELECT d                                            AS date_key,
       year(d)                                      AS year,
       quarter(d)                                   AS quarter,
       month(d)                                     AS month,
       date_trunc('month', d)::DATE                 AS month_start,
       strftime(d, '%B')                            AS month_name,
       day(d)                                       AS day,
       dayname(d)                                   AS day_of_week,
       dayofweek(d) IN (0, 6)                       AS is_weekend
FROM (SELECT unnest(generate_series(DATE '2020-01-01', DATE '2026-12-31', INTERVAL 1 DAY))::DATE AS d);

-- ---------------------------------------------------------------- dim_unit
INSERT INTO gold.dim_unit
SELECT unit_id, community_id, unit_type, monthly_rent
FROM silver.units;

-- ---------------------------------------------------------------- dim_resident
INSERT INTO gold.dim_resident
SELECT resident_id, community_id, first_name, last_name, dob, gender,
       admit_date, discharge_date, care_level, acuity_score
FROM silver.residents;

-- ------------------------------------------------- dim_resident_care_level (SCD2)
-- Build the event stream, then derive validity ranges with LEAD().
-- Three event sources:
--   1. every care-level change from PCC care history
--   2. an anchor row for residents whose FIRST history event has a
--      previous_level (they held that level from admission)
--   3. residents with no history at all: their snapshot level from admit_date
INSERT INTO gold.dim_resident_care_level
WITH change_events AS (
    SELECT resident_id, change_date AS effective_from, new_level AS care_level, reason
    FROM silver.care_history
    WHERE new_level IS NOT NULL AND change_date IS NOT NULL
),
first_events AS (
    SELECT resident_id, previous_level,
           ROW_NUMBER() OVER (PARTITION BY resident_id ORDER BY change_date) AS rn,
           change_date
    FROM silver.care_history
),
anchors AS (                       -- level held before the first recorded change
    SELECT f.resident_id, r.admit_date AS effective_from,
           f.previous_level AS care_level, 'Initial level (inferred)' AS reason
    FROM first_events f
    JOIN silver.residents r USING (resident_id)
    WHERE f.rn = 1 AND f.previous_level IS NOT NULL
      AND r.admit_date < f.change_date
),
no_history AS (                    -- never changed level in our window
    SELECT r.resident_id, r.admit_date AS effective_from,
           r.care_level, 'Initial level (no change history)' AS reason
    FROM silver.residents r
    WHERE r.care_level IS NOT NULL
      AND r.resident_id NOT IN (SELECT DISTINCT resident_id FROM silver.care_history)
),
all_events AS (
    SELECT * FROM change_events
    UNION ALL SELECT * FROM anchors
    UNION ALL SELECT * FROM no_history
),
ranged AS (
    SELECT resident_id, care_level, effective_from, reason,
           LEAD(effective_from) OVER (PARTITION BY resident_id ORDER BY effective_from)
               - INTERVAL 1 DAY AS next_minus_one
    FROM all_events
)
SELECT ROW_NUMBER() OVER (ORDER BY resident_id, effective_from) AS care_level_key,
       r.resident_id,
       r.care_level,
       r.effective_from,
       CASE WHEN r.next_minus_one IS NOT NULL THEN r.next_minus_one::DATE
            ELSE d.discharge_date END                    AS effective_to,
       r.next_minus_one IS NULL                          AS is_current,
       r.reason                                          AS change_reason
FROM ranged r
JOIN gold.dim_resident d USING (resident_id);

-- ---------------------------------------------------------------- fact_resident_day
-- Census rule: a resident occupies a bed on day d when admit_date <= d and
-- (discharge is NULL or discharge > d). The discharge day itself is not a
-- census day (midnight-census convention, matches industry practice).
INSERT INTO gold.fact_resident_day
SELECT dd.date_key,
       r.resident_id,
       r.community_id,
       scd.care_level_key,
       scd.care_level
FROM gold.dim_resident r
JOIN gold.dim_date dd
  ON dd.date_key BETWEEN :window_start AND :window_end
 AND r.admit_date <= dd.date_key
 AND (r.discharge_date IS NULL OR r.discharge_date > dd.date_key)
LEFT JOIN gold.dim_resident_care_level scd
  ON scd.resident_id = r.resident_id
 AND dd.date_key >= scd.effective_from
 AND (scd.effective_to IS NULL OR dd.date_key <= scd.effective_to);

-- ---------------------------------------------------------------- fact_monthly_revenue
-- Revenue is prorated: rate * active_days / days_in_month.
INSERT INTO gold.fact_monthly_revenue
WITH months AS (
    SELECT DISTINCT month_start,
           (month_start + INTERVAL 1 MONTH - INTERVAL 1 DAY)::DATE AS month_end
    FROM gold.dim_date
    WHERE date_key BETWEEN :window_start AND :window_end
)
SELECT m.month_start,
       l.lease_id, l.resident_id, l.unit_id, l.community_id,
       (LEAST(COALESCE(l.move_out_date - INTERVAL 1 DAY, m.month_end), m.month_end)::DATE
          - GREATEST(l.move_in_date, m.month_start) + 1)         AS active_days,
       (m.month_end - m.month_start + 1)                          AS days_in_month,
       l.monthly_rate,
       ROUND(l.monthly_rate *
             (LEAST(COALESCE(l.move_out_date - INTERVAL 1 DAY, m.month_end), m.month_end)::DATE
                - GREATEST(l.move_in_date, m.month_start) + 1)
             / (m.month_end - m.month_start + 1), 2)              AS revenue
FROM silver.leases l
JOIN months m
  ON l.move_in_date <= m.month_end
 AND (l.move_out_date IS NULL OR l.move_out_date > m.month_start)
WHERE l.monthly_rate IS NOT NULL AND l.move_in_date IS NOT NULL;

-- ---------------------------------------------------------------- transaction facts
INSERT INTO gold.fact_shift
SELECT shift_id, shift_date, community_id, employee_id, role,
       hours_worked, hourly_rate, labor_cost
FROM silver.shifts
WHERE shift_date IS NOT NULL;

INSERT INTO gold.fact_incident
SELECT i.incident_id, i.incident_date, i.resident_id, i.community_id,
       i.incident_type, i.severity, i.reported_by
FROM silver.incidents i
WHERE i.incident_date IS NOT NULL;

INSERT INTO gold.fact_lease
SELECT lease_id, resident_id, unit_id, community_id,
       move_in_date, move_out_date, move_out_reason, monthly_rate
FROM silver.leases;

INSERT INTO gold.fact_review
SELECT review_id, review_date, community_id, rating,
       responded_at IS NOT NULL,
       (responded_at - review_date)
FROM silver.reviews
WHERE review_date IS NOT NULL;

INSERT INTO gold.fact_lead
SELECT lead_id, community_id, lead_source, created_date, tour_date,
       deposit_date, move_in_date, status, lost_reason,
       tour_date IS NOT NULL,
       deposit_date IS NOT NULL,
       status = 'Won'
FROM silver.leads;
