# Eval report: mock

Model: n/a (mock) | Dataset seed: 42 | Claims: 300 (300 ok, 0 failed)

> Mock results are the floor: the mock provider does not read document contents.

## Summary

| Metric | Value |
| --- | --- |
| Field accuracy | 3.9% |
| Classification accuracy | 35.2% |
| Route accuracy | 41.3% |
| Latency p50 / p95 (total) | 0.13s / 0.15s |
| Latency p50 / p95 (extraction) | 0.05s / 0.06s |
| Cost per claim | $0.000000 |
| Total cost | $0.0000 |

## Field accuracy by field

| Field | Accuracy |
| --- | --- |
| policy_number | 4.7% |
| claimant_name | 0.0% |
| date_of_loss | 0.0% |
| loss_type | 26.7% |
| incident_location | 0.0% |
| incident_description | 0.0% |
| estimated_damage_amount | 0.0% |
| vehicle_or_property_details | 0.0% |

## Classification accuracy by document type

| Document type | Accuracy |
| --- | --- |
| claim_form | 0.0% |
| damage_photo | 100.0% |
| police_report | 0.0% |
| repair_estimate | 0.0% |

## Route confusion (rows: expected, columns: predicted)

| Expected \ Predicted | ready_for_adjuster | needs_more_info | likely_not_covered |
| --- | --- | --- | --- |
| ready_for_adjuster | 0 | 81 | 0 |
| needs_more_info | 0 | 124 | 0 |
| likely_not_covered | 0 | 95 | 0 |

## Calibration (accuracy per confidence bucket)

| Confidence bucket | Fields | Accuracy |
| --- | --- | --- |
| 0.5-0.6 | 300 | 26.7% |
| 0.8-0.9 | 1800 | 0.0% |
| 0.9-1.0 | 300 | 4.7% |

## Conflict detection

Conflict claims accept either document's value for field accuracy; this metric asks separately whether triage flagged the conflict. A conflict counts as flagged when a triage reason reports a consistency problem.

Conflict claims: 30, flagged: 0, detection rate: 0.0%

## Photo-mismatch detection

Mismatch claims pair an incident description with a damage photo from another loss type's pool (for example, a collision description with a flood photo). A mismatch counts as flagged when a triage reason reports a consistency problem, the same signal as conflict detection.

Mismatch claims: 18, flagged: 0, detection rate: 0.0%

## Consistency judge precision

Detection rates alone flatter the judge: most flagged claims have no real conflict or mismatch. A claim is "real" when the labels carry a conflict or a photo mismatch; "flagged" when a triage reason reports a consistency problem.

Flagged: 0, real: 48, true positives: 0, false positives: 0, false negatives: 48

Precision: n/a
False-alarm rate (clean claims flagged): 0.0%
Recall: 0.0%

## Dangerous errors

Claims routed to likely_not_covered when the label says otherwise. No route is an auto-denial: every route goes to an adjuster, and likely_not_covered is the coverage-review queue. Pointing an adjuster toward denial is still the most serious error class.

None.

## Breakdowns

| Slice | Claims | Field accuracy | Route accuracy |
| --- | --- | --- | --- |
| clean | 210 | 4.4% | 26.2% |
| messy | 90 | 2.8% | 76.7% |
| scanned | 30 | 3.8% | 73.3% |
| text | 270 | 3.9% | 37.8% |

## Breakdown by loss type

| Loss type | Claims | Field accuracy | Route accuracy |
| --- | --- | --- | --- |
| auto | 105 | 0.6% | 40.0% |
| property | 86 | 12.4% | 45.3% |
| theft | 48 | 0.3% | 33.3% |
| water damage | 61 | 0.6% | 44.3% |

## Breakdown by scan strength

| Scan strength | Claims | Field accuracy | Route accuracy |
| --- | --- | --- | --- |
| light | 4 | 12.5% | 50.0% |
| medium | 10 | 0.0% | 70.0% |
| heavy | 16 | 3.9% | 81.2% |
| text | 270 | 3.9% | 37.8% |
