# Eval report: anthropic

Model: claude-sonnet-5-5 | Dataset seed: 42 | Claims: 200 (200 ok, 0 failed)

> Test split after: 37 full re-runs with new prompts + 163 consistency-only refreshes reusing saved extractions.

## Summary

| Metric | Value |
| --- | --- |
| Field accuracy | 99.8% |
| Classification accuracy | 100.0% |
| Route accuracy | 78.5% |
| Latency p50 / p95 (total) | 7.66s / 19.52s |
| Latency p50 / p95 (extraction) | 3.76s / 11.06s |
| Cost per claim | $0.010302 |
| Total cost | $2.0603 |

## Field accuracy by field

| Field | Accuracy |
| --- | --- |
| policy_number | 100.0% |
| claimant_name | 100.0% |
| date_of_loss | 100.0% |
| loss_type | 99.0% |
| incident_location | 100.0% |
| incident_description | 99.5% |
| estimated_damage_amount | 100.0% |
| vehicle_or_property_details | 100.0% |

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
| ready_for_adjuster | 16 | 38 | 0 |
| needs_more_info | 1 | 80 | 0 |
| likely_not_covered | 0 | 4 | 61 |

## Calibration (accuracy per confidence bucket)

| Confidence bucket | Fields | Accuracy |
| --- | --- | --- |
| 0.0-0.1 | 108 | 100.0% |
| 0.1-0.2 | 2 | 100.0% |
| 0.3-0.4 | 32 | 100.0% |
| 0.4-0.5 | 4 | 100.0% |
| 0.5-0.6 | 1 | 100.0% |
| 0.6-0.7 | 1 | 0.0% |
| 0.7-0.8 | 12 | 100.0% |
| 0.8-0.9 | 31 | 96.8% |
| 0.9-1.0 | 1409 | 99.9% |

## Conflict detection

Conflict claims accept either document's value for field accuracy; this metric asks separately whether triage flagged the conflict. A conflict counts as flagged when a triage reason reports a consistency problem.

Conflict claims: 26, flagged: 24, detection rate: 92.3%

## Photo-mismatch detection

Mismatch claims pair an incident description with a damage photo from another loss type's pool (for example, a collision description with a flood photo). A mismatch counts as flagged when a triage reason reports a consistency problem, the same signal as conflict detection.

Mismatch claims: 12, flagged: 12, detection rate: 100.0%

## Consistency judge precision

Detection rates alone flatter the judge: most flagged claims have no real conflict or mismatch. A claim is "real" when the labels carry a conflict or a photo mismatch; "flagged" when a triage reason reports a consistency problem.

Flagged: 115, real: 38, true positives: 36, false positives: 79, false negatives: 2

Precision: 31.3%
False-alarm rate (clean claims flagged): 48.8%
Recall: 94.7%

## Dangerous errors

Claims routed to likely_not_covered when the label says otherwise. No route is an auto-denial: every route goes to an adjuster, and likely_not_covered is the coverage-review queue. Pointing an adjuster toward denial is still the most serious error class.

None.

## Breakdowns

| Slice | Claims | Field accuracy | Route accuracy |
| --- | --- | --- | --- |
| clean | 139 | 99.8% | 70.5% |
| messy | 61 | 99.8% | 96.7% |
| scanned | 20 | 100.0% | 90.0% |
| text | 180 | 99.8% | 77.2% |

## Breakdown by loss type

| Loss type | Claims | Field accuracy | Route accuracy |
| --- | --- | --- | --- |
| auto | 76 | 99.8% | 65.8% |
| property | 54 | 100.0% | 77.8% |
| theft | 31 | 99.2% | 96.8% |
| water damage | 39 | 100.0% | 89.7% |

## Breakdown by scan strength

| Scan strength | Claims | Field accuracy | Route accuracy |
| --- | --- | --- | --- |
| light | 1 | 100.0% | 100.0% |
| medium | 7 | 100.0% | 85.7% |
| heavy | 12 | 100.0% | 91.7% |
| text | 180 | 99.8% | 77.2% |
