# Anomaly Register — Pinewood Dataset

Every intentional (and incidental) data problem found, evidence, handling, and
what I'd do in production. Numbers match the README table.

## 1. ADP hourly_rate contains the entire pay-rate map

**Evidence:** every row in all six `adp_shifts` files:
`hourly_rate = "{'Caregiver': 16, 'Med Tech': 21, 'LPN': 31, 'RN': 46, ...}"`.
**Diagnosis:** export bug — ADP serialized the whole role→rate lookup into
each row instead of the scalar for that row's role.
**Handling:** `silver.clean_shifts` parses the dict (`ast.literal_eval`, safe —
no code execution) and extracts the value keyed by the row's own `role`. The
parser also accepts a plain number, so when ADP fixes the export nothing breaks.
Rows whose role isn't in the map are rejected to quarantine.
**Production:** raise with ADP/IT; add a validation check asserting the column
is scalar-numeric so a silent format change gets caught.

## 2. Care-level naming drift (Feb onward)

**Evidence:** Jan uses `IL/AL/MC`; Feb–Jun mix in `Assisted`,
`Assisted Living`, `Independent`, `Independent Living`, `Memory`,
`Memory Care` — 9 distinct spellings of 3 values.
**Handling:** canonical map in `config.CARE_LEVEL_MAP`, applied to residents
and care history. Unmappable values become NULL + quarantine entry (none in
this data, but the path exists for month 7).

## 3. March residents file switches to MM/DD/YYYY

**Evidence:** all 683 rows of `pcc_residents_2025_03.csv` — dob, admit,
discharge in US format; every other month is ISO.
**Handling:** `parse_date` tries ISO, then MM/DD/YYYY, per value (not per
file), and returns NULL rather than guessing on anything else. Ambiguity note:
a value like 04/05/2025 is formally ambiguous, but since all other months are
ISO and the March file is uniformly US-format, interpretation as MM/DD is safe;
cross-month consistency of each resident's admit_date confirms it.

## 4. Acuity scores out of range

**Evidence:** R01306 = −5, R01291 = 50, R01358 = 99 (valid range 1–10).
**Handling:** resident row kept, acuity nulled, quarantine entry, "raise to
client" — these look like fat-finger entries (50 and 99 plausibly meant 5 and
9), but guessing clinical values is not our call.

## 5. Phantom communities in Yardi units

**Evidence:** units U00911–U00915 belong to C905, C934, C936, C951, C969 in
every monthly snapshot — Pinewood has only C001–C014.
**Handling:** 30 rows quarantined. Not dropped silently: they'd inflate the
occupancy denominator by 5 units if misassigned, and they may be real (a
disposed community still in Yardi, or test records). Client question, not a
pipeline guess.

## 6. Near-duplicate resident identities (Feb)

**Evidence:** R01001 appears twice in Feb: "Michael Johnson"/C001 and
"Michaea Johnson"/C004 — same dob, admit date, acuity. Ditto R01146
("Mary"/"Mari" Taylor) and R01188 ("Elizabeth"/"Elizabeti" Lopez).
**Diagnosis:** botched merge/re-key in the Feb export.
**Handling:** identity resolution keeps the row whose community matches the
resident's modal (most frequent) community across all six snapshots; the
impostor row is quarantined. This is also why resident identity is stable
enough to join PCC ↔ Yardi on resident_id (verified: every lease resident_id
exists in PCC).

## 7. Duplicate lease rows across exports

**Evidence:** 44 lease_ids appear in two monthly files with identical content.
**Diagnosis:** by design — the data dictionary says a lease appears in its
creation month *and* the month its move-out was recorded.
**Handling:** dedup on lease_id keeping the latest export. Logged as one
summary quarantine entry (informational, not an error).

## 8. Conflicting duplicate lead

**Evidence:** HL385264 in March (C008, Lost) and June (C003, Won) — different
community *and* opposite outcome.
**Handling:** kept the June (latest) version under a last-export-wins rule,
flagged to client. One lead either way; immaterial to metrics but a good
canary for CRM hygiene.

## 9. Future-dated discharges

**Evidence:** R01443 discharged 2026-09-17, R01611 discharged 2026-04-28 —
both beyond the extract window (data ends 2025-06-30).
**Handling:** discharge nulled → resident treated as active in census (which
is what they are today); original value quarantined; raise to client. Possibly
"planned discharge" dates leaking into the discharge field.

## 10. Schema drift: mobility_status

**Evidence:** column exists only in `pcc_residents_2025_04.csv`
(Walker / Independent / Bedbound / Wheelchair).
**Handling:** Bronze adds new columns dynamically (ALTER TABLE), so the
pipeline doesn't crash; Silver carries it as nullable. Demonstrates the
requirement: new columns land without code changes.

## 11. HubSpot funnel chronology violations

**Evidence:** 12 leads with deposit before tour; 34 with move-in before deposit.
**Handling:** kept, surfaced as validation WARNINGS not failures — a family
can genuinely deposit without touring (out-of-state placement) and CRM dates
are often entered late. Flag to sales ops rather than "fix" in pipeline.

## 12. No community master data

**Evidence:** no file maps community_id to name/state/region; regions are
required for RLS.
**Handling:** documented hard-coded seed in `config.COMMUNITY_MASTER`
(C001–C005 OR / C006–C010 AZ / C011–C014 TX), one place to replace with the
client's real mapping. Assumption stated in README.

## 13. Acuity never changes, but care history says it does

**Evidence:** zero residents show any acuity change across six monthly
snapshots, yet `pcc_care_history` records dozens of care-level changes with
reason "Acuity Increase".
**Diagnosis:** the residents snapshot appears to carry a static admission-time
acuity, not the current clinical score.
**Handling:** the required "acuity +2 within 90 days" view is built and
correct — it is *legitimately empty* on this data, and I say so rather than
massaging it. Raised to client: if PCC can export acuity history, the view
lights up with no code change.
