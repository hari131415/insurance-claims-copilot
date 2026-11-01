# Eval report: anthropic

Model: claude-sonnet-5-5 | Dataset seed: 42 | Claims: 300 (300 ok, 0 failed)

> Anthropic full run: all 300 claims, budget cap 8.75 USD not triggered.

## Summary

| Metric | Value |
| --- | --- |
| Field accuracy | 97.2% |
| Classification accuracy | 100.0% |
| Route accuracy | 75.7% |
| Latency p50 / p95 (total) | 7.66s / 21.36s |
| Latency p50 / p95 (extraction) | 3.88s / 9.38s |
| Cost per claim | $0.020407 |
| Total cost | $6.1220 |

## Field accuracy by field

| Field | Accuracy |
| --- | --- |
| policy_number | 99.7% |
| claimant_name | 100.0% |
| date_of_loss | 100.0% |
| loss_type | 92.0% |
| incident_location | 100.0% |
| incident_description | 92.7% |
| estimated_damage_amount | 100.0% |
| vehicle_or_property_details | 93.7% |

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
| ready_for_adjuster | 13 | 68 | 0 |
| needs_more_info | 2 | 119 | 3 |
| likely_not_covered | 0 | 0 | 95 |

## Calibration (accuracy per confidence bucket)

| Confidence bucket | Fields | Accuracy |
| --- | --- | --- |
| 0.0-0.1 | 100 | 100.0% |
| 0.1-0.2 | 12 | 100.0% |
| 0.2-0.3 | 1 | 100.0% |
| 0.3-0.4 | 55 | 92.7% |
| 0.4-0.5 | 24 | 29.2% |
| 0.5-0.6 | 15 | 6.7% |
| 0.6-0.7 | 13 | 0.0% |
| 0.7-0.8 | 13 | 92.3% |
| 0.8-0.9 | 53 | 83.0% |
| 0.9-1.0 | 2114 | 99.6% |

## Conflict detection

Conflict claims accept either document's value for field accuracy; this metric asks separately whether triage flagged the conflict. A conflict counts as flagged when a triage reason reports a consistency problem.

Conflict claims: 30, flagged: 27, detection rate: 90.0%

## Photo-mismatch detection

Mismatch claims pair an incident description with a damage photo from another loss type's pool (for example, a collision description with a flood photo). A mismatch counts as flagged when a triage reason reports a consistency problem, the same signal as conflict detection.

Mismatch claims: 18, flagged: 18, detection rate: 100.0%

## Consistency judge precision

Detection rates alone flatter the judge: most flagged claims have no real conflict or mismatch. A claim is "real" when the labels carry a conflict or a photo mismatch; "flagged" when a triage reason reports a consistency problem.

Flagged: 192, real: 48, true positives: 45, false positives: 147, false negatives: 3

Precision: 23.4%
False-alarm rate (clean claims flagged): 58.3%
Recall: 93.8%

## Dangerous errors

Claims routed to likely_not_covered when the label says otherwise. No route is an auto-denial: every route goes to an adjuster, and likely_not_covered is the coverage-review queue. Pointing an adjuster toward denial is still the most serious error class.

- claim-0018: expected needs_more_info, routed likely_not_covered; Loss type auto is excluded under this policy.
- claim-0052: expected needs_more_info, routed likely_not_covered; Loss type water damage is excluded under this policy.
- claim-0222: expected needs_more_info, routed likely_not_covered; Loss type water damage is excluded under this policy.

## Breakdowns

| Slice | Claims | Field accuracy | Route accuracy |
| --- | --- | --- | --- |
| clean | 210 | 96.9% | 68.6% |
| messy | 90 | 98.1% | 92.2% |
| scanned | 30 | 98.8% | 93.3% |
| text | 270 | 97.1% | 73.7% |

## Breakdown by loss type

| Loss type | Claims | Field accuracy | Route accuracy |
| --- | --- | --- | --- |
| auto | 105 | 97.0% | 69.5% |
| property | 86 | 97.2% | 73.3% |
| theft | 48 | 97.7% | 83.3% |
| water damage | 61 | 97.3% | 83.6% |

## Breakdown by scan strength

| Scan strength | Claims | Field accuracy | Route accuracy |
| --- | --- | --- | --- |
| light | 4 | 100.0% | 100.0% |
| medium | 10 | 97.5% | 90.0% |
| heavy | 16 | 99.2% | 93.8% |
| text | 270 | 97.1% | 73.7% |
