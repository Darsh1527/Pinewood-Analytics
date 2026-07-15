-- ============================================================================
-- Pinewood Gold layer — star schema DDL
--
-- Grain statements (also in README):
--   fact_resident_day    : one row per resident per calendar day the resident
--                          was in census (admit <= day < discharge). This is
--                          the atomic grain for occupancy, census, and all
--                          per-resident-day rates.
--   fact_monthly_revenue : one row per lease per calendar month with revenue
--                          prorated by active days in that month.
--   fact_shift           : one row per worked shift (transaction grain).
--   fact_incident        : one row per reported incident (transaction grain).
--   fact_lease           : one row per lease (accumulating snapshot: move-in,
--                          move-out, reason).
--   fact_review          : one row per Google review.
--   fact_lead            : one row per CRM lead (accumulating funnel snapshot).
--
-- Conformed dimensions: dim_date and dim_community are shared by every fact.
-- dim_resident is shared by resident_day, incident, lease.
-- dim_resident_care_level is the SCD2 dimension for care level.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS gold;

-- Rebuild is deterministic from Silver, so Gold is dropped and recreated on
-- every run. Facts are dropped before dimensions (FK dependency order).
DROP TABLE IF EXISTS gold.fact_resident_day;
DROP TABLE IF EXISTS gold.fact_monthly_revenue;
DROP TABLE IF EXISTS gold.fact_shift;
DROP TABLE IF EXISTS gold.fact_incident;
DROP TABLE IF EXISTS gold.fact_lease;
DROP TABLE IF EXISTS gold.fact_review;
DROP TABLE IF EXISTS gold.fact_lead;
DROP TABLE IF EXISTS gold.dim_resident_care_level;
DROP TABLE IF EXISTS gold.dim_resident CASCADE;
DROP TABLE IF EXISTS gold.dim_unit CASCADE;
DROP TABLE IF EXISTS gold.dim_community CASCADE;
DROP TABLE IF EXISTS gold.dim_date CASCADE;

-- ---------------------------------------------------------------- dimensions

CREATE OR REPLACE TABLE gold.dim_date (
    date_key      DATE PRIMARY KEY,          -- natural key: the calendar day
    year          INTEGER NOT NULL,
    quarter       INTEGER NOT NULL,
    month         INTEGER NOT NULL,
    month_start   DATE    NOT NULL,          -- first day of month (join helper)
    month_name    VARCHAR NOT NULL,
    day           INTEGER NOT NULL,
    day_of_week   VARCHAR NOT NULL,
    is_weekend    BOOLEAN NOT NULL
);

CREATE OR REPLACE TABLE gold.dim_community (
    community_id   VARCHAR PRIMARY KEY,      -- C001..C014, natural key from sources
    community_name VARCHAR NOT NULL,
    city           VARCHAR,
    state          VARCHAR NOT NULL,         -- OR / AZ / TX
    region         VARCHAR NOT NULL          -- drives RLS: Pacific Northwest / Southwest / South
);

CREATE OR REPLACE TABLE gold.dim_unit (
    unit_id      VARCHAR PRIMARY KEY,
    community_id VARCHAR NOT NULL REFERENCES gold.dim_community (community_id),
    unit_type    VARCHAR NOT NULL,           -- IL / AL / MC
    list_rent    DECIMAL(10,2)               -- latest snapshot base rent
);

CREATE OR REPLACE TABLE gold.dim_resident (
    resident_id        VARCHAR PRIMARY KEY,  -- PCC id; also used by Yardi leases
    community_id       VARCHAR NOT NULL REFERENCES gold.dim_community (community_id),
    first_name         VARCHAR,
    last_name          VARCHAR,
    dob                DATE,
    gender             VARCHAR,
    admit_date         DATE,
    discharge_date     DATE,                 -- NULL = still active
    current_care_level VARCHAR,              -- convenience denormalization (SCD1)
    current_acuity     INTEGER
);

-- SCD Type 2: full care-level history. A resident moving AL -> MC gets a new
-- row; the old row is closed with effective_to = day before the change.
CREATE OR REPLACE TABLE gold.dim_resident_care_level (
    care_level_key BIGINT PRIMARY KEY,       -- surrogate key
    resident_id    VARCHAR NOT NULL REFERENCES gold.dim_resident (resident_id),
    care_level     VARCHAR NOT NULL,         -- IL / AL / MC
    effective_from DATE NOT NULL,
    effective_to   DATE,                     -- NULL = open-ended (current)
    is_current     BOOLEAN NOT NULL,
    change_reason  VARCHAR
);

-- --------------------------------------------------------------------- facts

CREATE OR REPLACE TABLE gold.fact_resident_day (
    date_key       DATE    NOT NULL REFERENCES gold.dim_date (date_key),
    resident_id    VARCHAR NOT NULL REFERENCES gold.dim_resident (resident_id),
    community_id   VARCHAR NOT NULL REFERENCES gold.dim_community (community_id),
    care_level_key BIGINT  REFERENCES gold.dim_resident_care_level (care_level_key),
    care_level     VARCHAR,                  -- denormalized for query convenience
    PRIMARY KEY (date_key, resident_id)
);

CREATE OR REPLACE TABLE gold.fact_monthly_revenue (
    month_start   DATE    NOT NULL,          -- joins dim_date.month_start
    lease_id      VARCHAR NOT NULL,
    resident_id   VARCHAR REFERENCES gold.dim_resident (resident_id),
    unit_id       VARCHAR,
    community_id  VARCHAR NOT NULL REFERENCES gold.dim_community (community_id),
    active_days   INTEGER NOT NULL,          -- lease-active days in the month
    days_in_month INTEGER NOT NULL,
    monthly_rate  DECIMAL(10,2) NOT NULL,
    revenue       DECIMAL(12,2) NOT NULL,    -- monthly_rate * active_days / days_in_month
    PRIMARY KEY (month_start, lease_id)
);

CREATE OR REPLACE TABLE gold.fact_shift (
    shift_id     VARCHAR PRIMARY KEY,
    date_key     DATE    NOT NULL REFERENCES gold.dim_date (date_key),
    community_id VARCHAR NOT NULL REFERENCES gold.dim_community (community_id),
    employee_id  VARCHAR NOT NULL,
    role         VARCHAR NOT NULL,
    hours_worked DECIMAL(5,2) NOT NULL,
    hourly_rate  DECIMAL(8,2) NOT NULL,
    labor_cost   DECIMAL(10,2) NOT NULL
);

CREATE OR REPLACE TABLE gold.fact_incident (
    incident_id   VARCHAR PRIMARY KEY,
    date_key      DATE    NOT NULL REFERENCES gold.dim_date (date_key),
    resident_id   VARCHAR REFERENCES gold.dim_resident (resident_id),
    community_id  VARCHAR NOT NULL REFERENCES gold.dim_community (community_id),
    incident_type VARCHAR NOT NULL,
    severity      INTEGER NOT NULL,
    reported_by   VARCHAR
);

CREATE OR REPLACE TABLE gold.fact_lease (
    lease_id        VARCHAR PRIMARY KEY,
    resident_id     VARCHAR REFERENCES gold.dim_resident (resident_id),
    unit_id         VARCHAR,
    community_id    VARCHAR NOT NULL REFERENCES gold.dim_community (community_id),
    move_in_date    DATE,
    move_out_date   DATE,                    -- NULL = active lease
    move_out_reason VARCHAR,
    monthly_rate    DECIMAL(10,2)
);

CREATE OR REPLACE TABLE gold.fact_review (
    review_id     VARCHAR PRIMARY KEY,
    date_key      DATE    NOT NULL REFERENCES gold.dim_date (date_key),
    community_id  VARCHAR NOT NULL REFERENCES gold.dim_community (community_id),
    rating        INTEGER NOT NULL,
    was_responded BOOLEAN NOT NULL,
    response_days INTEGER                    -- days from review to response
);

CREATE OR REPLACE TABLE gold.fact_lead (
    lead_id       VARCHAR PRIMARY KEY,
    community_id  VARCHAR NOT NULL REFERENCES gold.dim_community (community_id),
    lead_source   VARCHAR,
    created_date  DATE,
    tour_date     DATE,
    deposit_date  DATE,
    move_in_date  DATE,
    status        VARCHAR,                   -- Won / Lost / Open
    lost_reason   VARCHAR,
    toured        BOOLEAN NOT NULL,
    deposited     BOOLEAN NOT NULL,
    converted     BOOLEAN NOT NULL           -- status = Won
);
