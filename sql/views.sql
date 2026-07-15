-- ============================================================================
-- Required analytical views (Part 2 of the assessment).
-- All views read only from Gold. "Trailing 12 months" is anchored to the last
-- date present in fact_resident_day so the logic keeps working as new months
-- of data arrive.
-- ============================================================================

-- 1. Monthly occupancy rate by community.
--    occupancy % = resident-days / (unit count that month * days in month).
--    Census-based because the Yardi lease extract only contains leases that
--    changed in-month, so it cannot reconstruct full unit occupancy history.
CREATE OR REPLACE VIEW gold.v_monthly_occupancy AS
WITH resident_days AS (
    SELECT d.month_start, f.community_id, COUNT(*) AS resident_days
    FROM gold.fact_resident_day f
    JOIN gold.dim_date d ON d.date_key = f.date_key
    GROUP BY 1, 2
),
unit_capacity AS (
    SELECT date_trunc('month', snapshot_date)::DATE AS month_start,
           community_id, COUNT(*) AS unit_count
    FROM silver.units_monthly
    GROUP BY 1, 2
),
month_days AS (
    SELECT month_start, COUNT(*) AS days_in_month
    FROM gold.dim_date GROUP BY 1
)
SELECT r.month_start,
       r.community_id,
       c.community_name,
       c.region,
       r.resident_days,
       u.unit_count,
       u.unit_count * m.days_in_month                                   AS available_unit_days,
       ROUND(100.0 * r.resident_days / (u.unit_count * m.days_in_month), 1) AS occupancy_pct
FROM resident_days r
JOIN unit_capacity u USING (month_start, community_id)
JOIN month_days m USING (month_start)
JOIN gold.dim_community c ON c.community_id = r.community_id
ORDER BY r.month_start, r.community_id;

-- 2. Average length of stay by care level, residents discharged in the last
--    12 months. Care level = level held at discharge (SCD2 lookup).
CREATE OR REPLACE VIEW gold.v_avg_los_by_care_level AS
WITH anchor AS (SELECT MAX(date_key) AS max_d FROM gold.fact_resident_day),
discharged AS (
    SELECT r.resident_id, r.admit_date, r.discharge_date,
           COALESCE(scd.care_level, r.current_care_level) AS care_level_at_discharge,
           r.discharge_date - r.admit_date AS los_days
    FROM gold.dim_resident r
    CROSS JOIN anchor a
    LEFT JOIN gold.dim_resident_care_level scd
      ON scd.resident_id = r.resident_id
     AND r.discharge_date - 1 >= scd.effective_from
     AND (scd.effective_to IS NULL OR r.discharge_date - 1 <= scd.effective_to)
    WHERE r.discharge_date IS NOT NULL
      AND r.discharge_date > a.max_d - INTERVAL 12 MONTH
)
SELECT care_level_at_discharge AS care_level,
       COUNT(*)                AS discharged_residents,
       ROUND(AVG(los_days), 1) AS avg_length_of_stay_days,
       ROUND(MEDIAN(los_days), 1) AS median_length_of_stay_days
FROM discharged
GROUP BY 1
ORDER BY 1;

-- 3. Top three move-out reasons by community, trailing 12 months,
--    as a percentage of that community's total move-outs.
CREATE OR REPLACE VIEW gold.v_top_moveout_reasons AS
WITH anchor AS (SELECT MAX(date_key) AS max_d FROM gold.fact_resident_day),
moveouts AS (
    SELECT l.community_id, COALESCE(l.move_out_reason, 'Unknown') AS move_out_reason
    FROM gold.fact_lease l
    CROSS JOIN anchor a
    WHERE l.move_out_date IS NOT NULL
      AND l.move_out_date > a.max_d - INTERVAL 12 MONTH
),
counted AS (
    SELECT community_id, move_out_reason,
           COUNT(*) AS reason_count,
           SUM(COUNT(*)) OVER (PARTITION BY community_id) AS total_moveouts,
           ROW_NUMBER() OVER (PARTITION BY community_id ORDER BY COUNT(*) DESC, move_out_reason) AS rk
    FROM moveouts
    GROUP BY 1, 2
)
SELECT c.community_id, dc.community_name, c.rk AS rank,
       c.move_out_reason, c.reason_count, c.total_moveouts,
       ROUND(100.0 * c.reason_count / c.total_moveouts, 1) AS pct_of_moveouts
FROM counted c
JOIN gold.dim_community dc USING (community_id)
WHERE c.rk <= 3
ORDER BY c.community_id, c.rk;

-- 4. Labor cost per resident-day by community by month.
CREATE OR REPLACE VIEW gold.v_labor_cost_per_resident_day AS
WITH labor AS (
    SELECT d.month_start, s.community_id,
           SUM(s.labor_cost) AS labor_cost, SUM(s.hours_worked) AS labor_hours
    FROM gold.fact_shift s
    JOIN gold.dim_date d ON d.date_key = s.date_key
    GROUP BY 1, 2
),
census AS (
    SELECT d.month_start, f.community_id, COUNT(*) AS resident_days
    FROM gold.fact_resident_day f
    JOIN gold.dim_date d ON d.date_key = f.date_key
    GROUP BY 1, 2
)
SELECT l.month_start, l.community_id, c.community_name, c.region,
       l.labor_cost, l.labor_hours, cs.resident_days,
       ROUND(l.labor_cost / cs.resident_days, 2) AS labor_cost_per_resident_day
FROM labor l
JOIN census cs USING (month_start, community_id)
JOIN gold.dim_community c ON c.community_id = l.community_id
ORDER BY l.month_start, l.community_id;

-- 5a. Incident rate per 100 resident-days by community (monthly).
CREATE OR REPLACE VIEW gold.v_incident_rate_by_community AS
WITH incidents AS (
    SELECT d.month_start, i.community_id, COUNT(*) AS incident_count
    FROM gold.fact_incident i
    JOIN gold.dim_date d ON d.date_key = i.date_key
    GROUP BY 1, 2
),
census AS (
    SELECT d.month_start, f.community_id, COUNT(*) AS resident_days
    FROM gold.fact_resident_day f
    JOIN gold.dim_date d ON d.date_key = f.date_key
    GROUP BY 1, 2
)
SELECT cs.month_start, cs.community_id, c.community_name, c.region,
       COALESCE(i.incident_count, 0) AS incident_count,
       cs.resident_days,
       ROUND(100.0 * COALESCE(i.incident_count, 0) / cs.resident_days, 2)
           AS incidents_per_100_resident_days
FROM census cs
LEFT JOIN incidents i USING (month_start, community_id)
JOIN gold.dim_community c ON c.community_id = cs.community_id
ORDER BY cs.month_start, cs.community_id;

-- 5b. Incident rate per 100 resident-days by care level (whole window).
--     Incidents are attributed to the care level the resident held on the
--     incident date via the SCD2 dimension.
CREATE OR REPLACE VIEW gold.v_incident_rate_by_care_level AS
WITH incidents AS (
    SELECT COALESCE(scd.care_level, 'Unknown') AS care_level, COUNT(*) AS incident_count
    FROM gold.fact_incident i
    LEFT JOIN gold.dim_resident_care_level scd
      ON scd.resident_id = i.resident_id
     AND i.date_key >= scd.effective_from
     AND (scd.effective_to IS NULL OR i.date_key <= scd.effective_to)
    GROUP BY 1
),
census AS (
    SELECT COALESCE(care_level, 'Unknown') AS care_level, COUNT(*) AS resident_days
    FROM gold.fact_resident_day
    GROUP BY 1
)
SELECT cs.care_level,
       COALESCE(i.incident_count, 0) AS incident_count,
       cs.resident_days,
       ROUND(100.0 * COALESCE(i.incident_count, 0) / cs.resident_days, 2)
           AS incidents_per_100_resident_days
FROM census cs
LEFT JOIN incidents i USING (care_level)
ORDER BY cs.care_level;

-- 6. Care-level review candidates: acuity rose by >= 2 points within any
--    90-day window. Acuity is only observed in monthly snapshots, so we
--    compare every pair of snapshots for a resident no more than 90 days apart.
CREATE OR REPLACE VIEW gold.v_care_level_review_candidates AS
WITH snaps AS (
    SELECT resident_id, snapshot_month, acuity_score
    FROM silver.residents_monthly
    WHERE acuity_score IS NOT NULL
),
pairs AS (
    SELECT a.resident_id,
           a.snapshot_month AS from_month, a.acuity_score AS from_acuity,
           b.snapshot_month AS to_month,   b.acuity_score AS to_acuity,
           b.acuity_score - a.acuity_score AS acuity_increase
    FROM snaps a
    JOIN snaps b
      ON a.resident_id = b.resident_id
     AND b.snapshot_month > a.snapshot_month
     AND b.snapshot_month <= a.snapshot_month + INTERVAL 90 DAY
),
best AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY resident_id ORDER BY acuity_increase DESC, to_month) AS rk
    FROM pairs WHERE acuity_increase >= 2
)
SELECT b.resident_id,
       r.first_name, r.last_name, r.community_id, c.community_name,
       r.current_care_level,
       b.from_month, b.from_acuity, b.to_month, b.to_acuity, b.acuity_increase
FROM best b
JOIN gold.dim_resident r USING (resident_id)
JOIN gold.dim_community c ON c.community_id = r.community_id
WHERE b.rk = 1
ORDER BY b.acuity_increase DESC, b.resident_id;

-- ---------------------------------------------------------------- API helpers
CREATE OR REPLACE VIEW gold.v_reviews_summary AS
SELECT d.month_start, f.community_id, c.community_name, c.region,
       COUNT(*) AS review_count,
       ROUND(AVG(f.rating), 2) AS avg_rating,
       SUM(CASE WHEN f.was_responded THEN 1 ELSE 0 END) AS responded_count,
       ROUND(AVG(f.response_days), 1) AS avg_response_days
FROM gold.fact_review f
JOIN gold.dim_date d ON d.date_key = f.date_key
JOIN gold.dim_community c ON c.community_id = f.community_id
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2;
