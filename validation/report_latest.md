# Pinewood Data Validation Report

Generated: 2026-07-14 12:05 UTC

**Result: 0 failed, 2 warnings, 39 checks total.**

This report is produced automatically after every pipeline run. A refresh should only be approved if there are no CRITICAL failures.

## Row reconciliation

| Check | Status | Severity | Detail | Recommended action |
|---|---|---|---|---|
| source -> bronze: pcc_residents | PASS | INFO | source files 4152 rows, bronze 4152 rows | — |
| source -> bronze: yardi_units | PASS | INFO | source files 5490 rows, bronze 5490 rows | — |
| source -> bronze: adp_shifts | PASS | INFO | source files 68071 rows, bronze 68071 rows | — |
| source -> bronze: gbp_reviews | PASS | INFO | source files 424 rows, bronze 424 rows | — |
| source -> bronze: pcc_care_history | PASS | INFO | source files 303 rows, bronze 303 rows | — |
| source -> bronze: hubspot_leads | PASS | INFO | source files 830 rows, bronze 830 rows | — |
| source -> bronze: pcc_incidents | PASS | INFO | source files 411 rows, bronze 411 rows | — |
| source -> bronze: yardi_leases | PASS | INFO | source files 346 rows, bronze 346 rows | — |
| bronze -> silver: pcc_residents | PASS | INFO | bronze 4152, silver 4149, dropped 3 (33 quarantine entries explain rejections/dedup) | — |
| bronze -> silver: pcc_incidents | PASS | INFO | bronze 411, silver 411, dropped 0 (0 quarantine entries explain rejections/dedup) | — |
| bronze -> silver: yardi_units | PASS | INFO | bronze 5490, silver 5460, dropped 30 (5 quarantine entries explain rejections/dedup) | — |
| bronze -> silver: yardi_leases | PASS | INFO | bronze 346, silver 302, dropped 44 (1 quarantine entries explain rejections/dedup) | — |
| bronze -> silver: adp_shifts | PASS | INFO | bronze 68071, silver 68071, dropped 0 (0 quarantine entries explain rejections/dedup) | — |
| bronze -> silver: gbp_reviews | PASS | INFO | bronze 424, silver 424, dropped 0 (0 quarantine entries explain rejections/dedup) | — |
| silver -> gold: incidents -> fact_incident | PASS | INFO | silver 411, gold 411 | — |
| silver -> gold: leases -> fact_lease | PASS | INFO | silver 302, gold 302 | — |
| silver -> gold: leads -> fact_lead | PASS | INFO | silver 829, gold 829 | — |
| silver -> gold: reviews -> fact_review | PASS | INFO | silver 424, gold 424 | — |
| silver -> gold: shifts -> fact_shift | PASS | INFO | silver 68071, gold 68071 | — |

## Aggregate reconciliation

| Check | Status | Severity | Detail | Recommended action |
|---|---|---|---|---|
| total labor hours | PASS | INFO | silver 565,188.00 h vs gold 565,188.00 h (diff 0.000%, tolerance 0.5%) | — |
| total labor cost | PASS | INFO | silver 12,666,480.00 $ vs gold 12,666,480.00 $ (diff 0.000%, tolerance 0.5%) | — |
| distinct residents | PASS | INFO | silver 823.00 vs gold 823.00 (diff 0.000%, tolerance 0.5%) | — |
| total incidents | PASS | INFO | silver 411.00 vs gold 411.00 (diff 0.000%, tolerance 0.5%) | — |
| total resident-days (independent recompute) | PASS | INFO | silver 120,457.00 vs gold 120,457.00 (diff 0.000%, tolerance 0.5%) | — |
| total prorated revenue (6 months) | PASS | INFO | gold total $5,090,143 — proration verified row-level by active_days/days_in_month bounds check below | — |
| revenue proration bounds | PASS | INFO | 0 rows violate 1 <= active_days <= days_in_month or revenue bounds | — |

## Business rules

| Check | Status | Severity | Detail | Recommended action |
|---|---|---|---|---|
| no overlapping leases per resident | PASS | INFO | 0 violating rows | — |
| no negative or >100% occupancy | PASS | INFO | 0 violating rows | — |
| no discharge before admit | PASS | INFO | 0 violating rows | — |
| no future-dated events (post window end) | PASS | INFO | 0 violating rows | — |
| acuity within 1-10 | PASS | INFO | 0 violating rows | — |
| incident severity within 1-5 | PASS | INFO | 0 violating rows | — |
| review rating within 1-5 | PASS | INFO | 0 violating rows | — |
| shift hours within 0-16 | PASS | INFO | 0 violating rows | — |
| all facts reference known communities | PASS | INFO | 0 violating rows | — |
| SCD2 ranges do not overlap per resident | PASS | INFO | 0 violating rows | — |
| exactly one current SCD2 row per resident | PASS | INFO | 0 violating rows | — |
| HubSpot funnel chronology (deposit before tour) | WARN | MEDIUM | 12 leads out of order — possibly process reality (deposits without tours), possibly CRM data entry | raise to client |
| HubSpot funnel chronology (move-in before deposit) | WARN | MEDIUM | 34 leads out of order — possibly process reality (deposits without tours), possibly CRM data entry | raise to client |

## Data quality events handled in this run (quarantine log)

| Source table | Reason | Action taken | Rows |
|---|---|---|---|
| hubspot_leads | conflicting duplicate lead across exports | kept latest export's version, raise to client | 1 |
| pcc_residents | acuity_score 99 outside 1-10 | field set NULL, raise to client | 6 |
| pcc_residents | future-dated discharge 2026-04-28 | discharge_date set NULL (treated as active), raise to client | 6 |
| pcc_residents | acuity_score 50 outside 1-10 | field set NULL, raise to client | 6 |
| pcc_residents | acuity_score -5 outside 1-10 | field set NULL, raise to client | 6 |
| pcc_residents | future-dated discharge 2026-09-17 | discharge_date set NULL (treated as active), raise to client | 6 |
| pcc_residents | duplicate identity in same snapshot (name/community conflict) | row rejected; kept row matching modal community | 3 |
| yardi_leases | lease re-exported in move-out month | deduplicated on lease_id (kept latest export) | 1 |
| yardi_units | unknown community C905 | row quarantined, raise to client | 1 |
| yardi_units | unknown community C934 | row quarantined, raise to client | 1 |
| yardi_units | unknown community C969 | row quarantined, raise to client | 1 |
| yardi_units | unknown community C951 | row quarantined, raise to client | 1 |
| yardi_units | unknown community C936 | row quarantined, raise to client | 1 |