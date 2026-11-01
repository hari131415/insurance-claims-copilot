# Eval report: anthropic

Model: claude-sonnet-5-5 | Dataset seed: 42 | Claims: 200 (200 ok, 0 failed)

> Test-split baseline: original full-run records for the 200 test claims.

## Summary

| Metric | Value |
| --- | --- |
| Field accuracy | 97.6% |
| Classification accuracy | 100.0% |
| Route accuracy | 76.5% |
| Latency p50 / p95 (total) | 7.68s / 20.13s |
| Latency p50 / p95 (extraction) | 3.88s / 11.20s |
| Cost per claim | $0.020712 |
| Total cost | $4.1424 |

## Field accuracy by field

| Field | Accuracy |
| --- | --- |
| policy_number | 100.0% |
| claimant_name | 100.0% |
| date_of_loss | 100.0% |
| loss_type | 92.5% |
| incident_location | 100.0% |
| incident_description | 94.0% |
| estimated_damage_amount | 100.0% |
| vehicle_or_property_details | 94.0% |

## Classification accuracy by document type

| Document type | Accuracy |
| --- | --- |
| claim_form | 100.0% |
| damage_photo | 100.0% |
| police_report | 100.0% |
| repair_estimate | 100.0% |

## Route confusion (rows: expected, columns: predicted)

| Expected \ Predicted | ready_for_adjuster | needs_more_info | likely_not_covered |
| --- | --- | --- | --- |
| ready_for_adjuster | 10 | 44 | 0 |
| needs_more_info | 1 | 78 | 2 |
| likely_not_covered | 0 | 0 | 65 |

## Calibration (accuracy per confidence bucket)

| Confidence bucket | Fields | Accuracy |
| --- | --- | --- |
| 0.0-0.1 | 65 | 100.0% |
| 0.1-0.2 | 8 | 100.0% |
| 0.2-0.3 | 1 | 100.0% |
| 0.3-0.4 | 33 | 97.0% |
| 0.4-0.5 | 11 | 36.4% |
| 0.5-0.6 | 12 | 8.3% |
| 0.6-0.7 | 12 | 0.0% |
| 0.7-0.8 | 13 | 92.3% |
| 0.8-0.9 | 35 | 88.6% |
| 0.9-1.0 | 1410 | 99.8% |

## Conflict detection

Conflict claims accept either document's value for field accuracy; this metric asks separately whether triage flagged the conflict. A conflict counts as flagged when a triage reason reports a consistency problem.

Conflict claims: 26, flagged: 24, detection rate: 92.3%

## Photo-mismatch detection

Mismatch claims pair an incident description with a damage photo from another loss type's pool (for example, a collision description with a flood photo). A mismatch counts as flagged when a triage reason reports a consistency problem, the same signal as conflict detection.

Mismatch claims: 12, flagged: 12, detection rate: 100.0%

## Consistency judge precision

Detection rates alone flatter the judge: most flagged claims have no real conflict or mismatch. A claim is "real" when the labels carry a conflict or a photo mismatch; "flagged" when a triage reason reports a consistency problem.

Flagged: 130, real: 38, true positives: 36, false positives: 94, false negatives: 2

Precision: 27.7%
False-alarm rate (clean claims flagged): 58.0%
Recall: 94.7%

## Dangerous errors

Claims routed to likely_not_covered when the label says otherwise. No route is an auto-denial: every route goes to an adjuster, and likely_not_covered is the coverage-review queue. Pointing an adjuster toward denial is still the most serious error class.

- claim-0018: expected needs_more_info, routed likely_not_covered; Loss type auto is excluded under this policy.
- claim-0222: expected needs_more_info, routed likely_not_covered; Loss type water damage is excluded under this policy.

## Breakdowns

| Slice | Claims | Field accuracy | Route accuracy |
| --- | --- | --- | --- |
| clean | 139 | 96.7% | 69.1% |
| messy | 61 | 99.6% | 93.4% |
| scanned | 20 | 100.0% | 90.0% |
| text | 180 | 97.3% | 75.0% |

## Breakdown by loss type

| Loss type | Claims | Field accuracy | Route accuracy |
| --- | --- | --- | --- |
| auto | 76 | 97.9% | 64.5% |
| property | 54 | 96.5% | 75.9% |
| theft | 31 | 99.2% | 96.8% |
| water damage | 39 | 97.1% | 84.6% |

## Breakdown by scan strength

| Scan strength | Claims | Field accuracy | Route accuracy |
| --- | --- | --- | --- |
| light | 1 | 100.0% | 100.0% |
| medium | 7 | 100.0% | 85.7% |
| heavy | 12 | 100.0% | 91.7% |
| text | 180 | 97.3% | 75.0% |
