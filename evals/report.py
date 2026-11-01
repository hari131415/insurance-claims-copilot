"""Build the eval report from saved raw results, without any API calls.

Reads one or more <provider>_raw.json files produced by evals/run.py and
writes, per provider, a markdown report and a per-claim CSV. With more
than one provider it also writes a side-by-side comparison report.

Metrics: field-level accuracy (exact match for IDs and dates,
normalized match for amounts and names, as recorded by the runner;
conflict claims accept either document's value), document
classification accuracy, triage route accuracy, p50/p95 latency, cost
per claim, calibration (accuracy per confidence bucket), and conflict
detection (whether triage flagged the conflict on conflict claims).
Breakdowns split clean vs messy claims and scanned vs text claims; a
claim counts as scanned when any of its files is an image-only PDF.
Further breakdowns split by loss type and by scan strength (the
strongest scan artifact level among a claim's files; claims with no
scanned files group under "text"). Older raw files predate the
scan_strength field, so their scanned claims group under "unknown".

Run from the repo root with the backend venv:
    backend/.venv/bin/python evals/report.py --results evals/results/mock_raw.json
    backend/.venv/bin/python evals/report.py --results evals/results/mock_raw.json \\
        evals/results/openai_raw.json --note "Mock results are the floor."
"""

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path

FIELD_KEYS = [
    "policy_number",
    "claimant_name",
    "date_of_loss",
    "loss_type",
    "incident_location",
    "incident_description",
    "estimated_damage_amount",
    "vehicle_or_property_details",
]
ROUTES = ["ready_for_adjuster", "needs_more_info", "likely_not_covered"]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(pct / 100 * len(ordered)) - 1))
    return ordered[index]


def _accuracy(pairs: list[bool]) -> float:
    return sum(pairs) / len(pairs) if pairs else 0.0


def compute_metrics(raw: dict) -> dict:
    claims = raw["claims"]
    ok = [claim for claim in claims if claim["status"] == "ok"]
    field_pairs = {
        key: [claim["fields"][key]["correct"] for claim in ok if key in claim["fields"]]
        for key in FIELD_KEYS
    }
    all_fields = [pair for pairs in field_pairs.values() for pair in pairs]
    class_pairs = [item["correct"] for claim in ok for item in claim["classifications"]]
    class_by_type: dict[str, list[bool]] = defaultdict(list)
    for claim in ok:
        for item in claim["classifications"]:
            class_by_type[item["expected"]].append(item["correct"])
    confusion: dict[str, Counter] = {route: Counter() for route in ROUTES}
    for claim in ok:
        if claim["route_predicted"] in ROUTES:
            confusion[claim["route_expected"]][claim["route_predicted"]] += 1
    buckets: dict[str, list[bool]] = defaultdict(list)
    for claim in ok:
        for key in FIELD_KEYS:
            entry = claim["fields"].get(key)
            if entry is None or entry["confidence"] is None:
                continue
            conf = float(entry["confidence"])
            index = min(9, int(conf * 10))
            buckets[f"{index / 10:.1f}-{(index + 1) / 10:.1f}"].append(entry["correct"])
    latencies = [claim["latency_total_s"] for claim in ok]
    extract_latencies = [claim["latency_extract_s"] for claim in ok]

    def summarize(subset: list[dict]) -> dict:
        fields = [
            claim["fields"][key]["correct"]
            for claim in subset
            for key in FIELD_KEYS
            if key in claim["fields"]
        ]
        routes = [claim["route_correct"] for claim in subset]
        return {
            "claims": len(subset),
            "field_accuracy": _accuracy(fields),
            "route_accuracy": _accuracy(routes),
        }

    def split(predicate: Callable[[dict], bool]) -> dict:
        return summarize([claim for claim in ok if predicate(claim)])

    loss_groups: dict[str, list[dict]] = defaultdict(list)
    for claim in ok:
        loss_groups[claim["loss_type"]].append(claim)
    by_loss_type = {loss_type: summarize(group) for loss_type, group in sorted(loss_groups.items())}

    def scan_strength(claim: dict) -> str:
        strength = claim.get("scan_strength")
        if strength:
            return str(strength)
        return "unknown" if claim["scanned"] else "text"

    strength_groups: dict[str, list[dict]] = defaultdict(list)
    for claim in ok:
        strength_groups[scan_strength(claim)].append(claim)
    by_scan_strength = {
        strength: summarize(strength_groups[strength])
        for strength in ["light", "medium", "heavy", "text", "unknown"]
        if strength in strength_groups
    }
    conflict_claims = [claim for claim in ok if claim.get("conflict_detected") is not None]
    conflict_detected = [claim for claim in conflict_claims if claim["conflict_detected"]]
    mismatch_claims = [claim for claim in ok if claim.get("mismatch_detected") is not None]
    mismatch_detected = [claim for claim in mismatch_claims if claim["mismatch_detected"]]
    # Consistency-judge precision: detection rates alone flatter the
    # judge, since most flagged claims have no real conflict or
    # mismatch. A claim is "real" when the labels carry a conflict or
    # a photo mismatch; "flagged" when a triage reason reports a
    # consistency problem. Precision is the flagged claims that are
    # real; the false-alarm rate is the clean claims that got flagged.
    flagged_ids = {
        claim["claim_id"]
        for claim in ok
        if any("Consistency check flagged" in reason for reason in claim["reasons_predicted"])
    }
    real_ids = {
        claim["claim_id"]
        for claim in ok
        if claim.get("conflict_detected") is not None or claim.get("mismatch_detected") is not None
    }
    all_ids = {claim["claim_id"] for claim in ok}
    tp = len(real_ids & flagged_ids)
    fp = len(flagged_ids - real_ids)
    fn = len(real_ids - flagged_ids)
    tn = len(all_ids - real_ids - flagged_ids)
    # A dangerous error routes a claim to likely_not_covered when the
    # label says otherwise. No route is an auto-denial; this is the
    # coverage-review queue, and pointing an adjuster toward denial
    # is still the most serious error class.
    # The deciding reason is the hard failure (exclusion, deductible,
    # or inactive policy) behind the route.
    dangerous = []
    for claim in ok:
        if claim["route_predicted"] != "likely_not_covered":
            continue
        if claim["route_expected"] == "likely_not_covered":
            continue
        deciding = [
            reason
            for reason in claim["reasons_predicted"]
            if "is excluded under this policy" in reason
            or "is at or below the deductible" in reason
            or "was not active on the date of loss" in reason
        ]
        dangerous.append(
            {
                "claim_id": claim["claim_id"],
                "route_expected": claim["route_expected"],
                "deciding_reasons": deciding,
            }
        )
    return {
        "conflict_claims": len(conflict_claims),
        "conflict_detected": len(conflict_detected),
        "conflict_detection_rate": _accuracy(
            [claim["conflict_detected"] for claim in conflict_claims]
        ),
        "mismatch_claims": len(mismatch_claims),
        "mismatch_detected": len(mismatch_detected),
        "mismatch_detection_rate": _accuracy(
            [claim["mismatch_detected"] for claim in mismatch_claims]
        ),
        "consistency_flagged": len(flagged_ids),
        "consistency_real": len(real_ids),
        "consistency_tp": tp,
        "consistency_fp": fp,
        "consistency_fn": fn,
        "consistency_tn": tn,
        "consistency_precision": (tp / (tp + fp) if tp + fp else None),
        "consistency_false_alarm_rate": (fp / (fp + tn) if fp + tn else None),
        "consistency_recall": (tp / (tp + fn) if tp + fn else None),
        "dangerous_errors": dangerous,
        "provider": raw["provider"],
        "model": raw.get("model"),
        "dataset_seed": raw.get("dataset_seed"),
        "dataset_count": raw.get("dataset_count"),
        "claims": len(claims),
        "ok": len(ok),
        "failed": len(claims) - len(ok),
        "field_accuracy": _accuracy(all_fields),
        "field_by_key": {key: _accuracy(pairs) for key, pairs in field_pairs.items()},
        "classification_accuracy": _accuracy(class_pairs),
        "classification_by_type": {
            doc_type: _accuracy(pairs) for doc_type, pairs in class_by_type.items()
        },
        "route_accuracy": _accuracy([claim["route_correct"] for claim in ok]),
        "confusion": {route: dict(confusion[route]) for route in ROUTES},
        "latency_p50": _percentile(latencies, 50),
        "latency_p95": _percentile(latencies, 95),
        "extract_p50": _percentile(extract_latencies, 50),
        "extract_p95": _percentile(extract_latencies, 95),
        "cost_total": sum(claim["cost_usd"] for claim in claims),
        "cost_per_claim": (
            sum(claim["cost_usd"] for claim in claims) / len(claims) if claims else 0.0
        ),
        "calibration": {
            bucket: {"n": len(pairs), "accuracy": _accuracy(pairs)}
            for bucket, pairs in sorted(buckets.items())
        },
        "clean": split(lambda claim: not claim["messy"]),
        "messy": split(lambda claim: claim["messy"]),
        "scanned": split(lambda claim: claim["scanned"]),
        "text": split(lambda claim: not claim["scanned"]),
        "by_loss_type": by_loss_type,
        "by_scan_strength": by_scan_strength,
    }


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _pct_or_na(value: float | None) -> str:
    return _pct(value) if value is not None else "n/a"


def render_report(metrics: dict, note: str | None) -> str:
    lines = [
        f"# Eval report: {metrics['provider']}",
        "",
        f"Model: {metrics['model'] or 'n/a (mock)'} | "
        f"Dataset seed: {metrics['dataset_seed']} | "
        f"Claims: {metrics['claims']} ({metrics['ok']} ok, {metrics['failed']} failed)",
        "",
    ]
    if note:
        lines += [f"> {note}", ""]
    lines += [
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Field accuracy | {_pct(metrics['field_accuracy'])} |",
        f"| Classification accuracy | {_pct(metrics['classification_accuracy'])} |",
        f"| Route accuracy | {_pct(metrics['route_accuracy'])} |",
        f"| Latency p50 / p95 (total) | {metrics['latency_p50']:.2f}s / "
        f"{metrics['latency_p95']:.2f}s |",
        f"| Latency p50 / p95 (extraction) | {metrics['extract_p50']:.2f}s / "
        f"{metrics['extract_p95']:.2f}s |",
        f"| Cost per claim | ${metrics['cost_per_claim']:.6f} |",
        f"| Total cost | ${metrics['cost_total']:.4f} |",
        "",
        "## Field accuracy by field",
        "",
        "| Field | Accuracy |",
        "| --- | --- |",
    ]
    for key in FIELD_KEYS:
        lines.append(f"| {key} | {_pct(metrics['field_by_key'][key])} |")
    lines += [
        "",
        "## Classification accuracy by document type",
        "",
        "| Document type | Accuracy |",
        "| --- | --- |",
    ]
    for doc_type in sorted(metrics["classification_by_type"]):
        lines.append(f"| {doc_type} | {_pct(metrics['classification_by_type'][doc_type])} |")
    lines += [
        "",
        "## Route confusion (rows: expected, columns: predicted)",
        "",
        "| Expected \\ Predicted | " + " | ".join(ROUTES) + " |",
        "| --- | " + " | ".join(["---"] * len(ROUTES)) + " |",
    ]
    for route in ROUTES:
        counts = [str(metrics["confusion"][route].get(predicted, 0)) for predicted in ROUTES]
        lines.append(f"| {route} | " + " | ".join(counts) + " |")
    lines += [
        "",
        "## Calibration (accuracy per confidence bucket)",
        "",
        "| Confidence bucket | Fields | Accuracy |",
        "| --- | --- | --- |",
    ]
    for bucket, entry in metrics["calibration"].items():
        lines.append(f"| {bucket} | {entry['n']} | {_pct(entry['accuracy'])} |")
    lines += [
        "",
        "## Conflict detection",
        "",
        "Conflict claims accept either document's value for field accuracy; "
        "this metric asks separately whether triage flagged the conflict. "
        "A conflict counts as flagged when a triage reason reports a "
        "consistency problem.",
        "",
        f"Conflict claims: {metrics['conflict_claims']}, flagged: "
        f"{metrics['conflict_detected']}, detection rate: "
        f"{_pct(metrics['conflict_detection_rate'])}",
        "",
        "## Photo-mismatch detection",
        "",
        "Mismatch claims pair an incident description with a damage photo "
        "from another loss type's pool (for example, a collision "
        "description with a flood photo). A mismatch counts as flagged "
        "when a triage reason reports a consistency problem, the same "
        "signal as conflict detection.",
        "",
        f"Mismatch claims: {metrics['mismatch_claims']}, flagged: "
        f"{metrics['mismatch_detected']}, detection rate: "
        f"{_pct(metrics['mismatch_detection_rate'])}",
        "",
        "## Consistency judge precision",
        "",
        "Detection rates alone flatter the judge: most flagged claims "
        'have no real conflict or mismatch. A claim is "real" when '
        'the labels carry a conflict or a photo mismatch; "flagged" '
        "when a triage reason reports a consistency problem.",
        "",
        f"Flagged: {metrics['consistency_flagged']}, real: "
        f"{metrics['consistency_real']}, true positives: "
        f"{metrics['consistency_tp']}, false positives: "
        f"{metrics['consistency_fp']}, false negatives: "
        f"{metrics['consistency_fn']}",
        "",
        f"Precision: {_pct_or_na(metrics['consistency_precision'])}",
        "False-alarm rate (clean claims flagged): "
        f"{_pct_or_na(metrics['consistency_false_alarm_rate'])}",
        f"Recall: {_pct_or_na(metrics['consistency_recall'])}",
        "",
        "## Dangerous errors",
        "",
        "Claims routed to likely_not_covered when the label says "
        "otherwise. No route is an auto-denial: every route goes "
        "to an adjuster, and likely_not_covered is the "
        "coverage-review queue. Pointing an adjuster toward "
        "denial is still the most serious error class.",
        "",
    ]
    if not metrics["dangerous_errors"]:
        lines.append("None.")
    else:
        for error in metrics["dangerous_errors"]:
            lines.append(
                f"- {error['claim_id']}: expected "
                f"{error['route_expected']}, routed likely_not_covered; "
                + "; ".join(error["deciding_reasons"])
            )
    lines += [
        "",
        "## Breakdowns",
        "",
        "| Slice | Claims | Field accuracy | Route accuracy |",
        "| --- | --- | --- | --- |",
    ]
    for name in ("clean", "messy", "scanned", "text"):
        entry = metrics[name]
        lines.append(
            f"| {name} | {entry['claims']} | {_pct(entry['field_accuracy'])} "
            f"| {_pct(entry['route_accuracy'])} |"
        )
    lines += [
        "",
        "## Breakdown by loss type",
        "",
        "| Loss type | Claims | Field accuracy | Route accuracy |",
        "| --- | --- | --- | --- |",
    ]
    for loss_type, entry in metrics["by_loss_type"].items():
        lines.append(
            f"| {loss_type} | {entry['claims']} | {_pct(entry['field_accuracy'])} "
            f"| {_pct(entry['route_accuracy'])} |"
        )
    lines += [
        "",
        "## Breakdown by scan strength",
        "",
        "| Scan strength | Claims | Field accuracy | Route accuracy |",
        "| --- | --- | --- | --- |",
    ]
    for strength, entry in metrics["by_scan_strength"].items():
        lines.append(
            f"| {strength} | {entry['claims']} | {_pct(entry['field_accuracy'])} "
            f"| {_pct(entry['route_accuracy'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_comparison(all_metrics: list[dict]) -> str:
    lines = [
        "# Provider comparison",
        "",
        "| Provider | Model | Field acc. | Class. acc. | Route acc. | "
        "Latency p50 / p95 | Cost / claim |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for metrics in all_metrics:
        lines.append(
            f"| {metrics['provider']} | {metrics['model'] or 'n/a'} "
            f"| {_pct(metrics['field_accuracy'])} "
            f"| {_pct(metrics['classification_accuracy'])} "
            f"| {_pct(metrics['route_accuracy'])} "
            f"| {metrics['latency_p50']:.2f}s / {metrics['latency_p95']:.2f}s "
            f"| ${metrics['cost_per_claim']:.6f} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_csv(raw: dict, path: Path) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "claim_id",
                "loss_type",
                "messy",
                "scanned",
                "scan_strength",
                "status",
                "route_expected",
                "route_predicted",
                "route_correct",
                "conflict_detected",
                "mismatch_detected",
                "cost_usd",
                "latency_total_s",
                "latency_extract_s",
                "latency_triage_s",
                "field_accuracy",
                "classification_accuracy",
                "error",
            ]
        )
        for claim in raw["claims"]:
            fields = [entry["correct"] for entry in claim["fields"].values()]
            classes = [item["correct"] for item in claim["classifications"]]
            writer.writerow(
                [
                    claim["claim_id"],
                    claim["loss_type"],
                    claim["messy"],
                    claim["scanned"],
                    claim.get("scan_strength", ""),
                    claim["status"],
                    claim["route_expected"],
                    claim["route_predicted"],
                    claim["route_correct"],
                    claim.get("conflict_detected", ""),
                    claim.get("mismatch_detected", ""),
                    claim["cost_usd"],
                    claim["latency_total_s"],
                    claim["latency_extract_s"],
                    claim["latency_triage_s"],
                    _accuracy(fields) if fields else "",
                    _accuracy(classes) if classes else "",
                    claim["error"] or "",
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build eval reports from raw results.")
    parser.add_argument("--results", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("evals/results"))
    parser.add_argument("--note", default=None)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    all_metrics = []
    for raw_path in args.results:
        raw = json.loads(raw_path.read_text())
        metrics = compute_metrics(raw)
        all_metrics.append(metrics)
        report_path = args.out / f"{raw['provider']}_report.md"
        report_path.write_text(render_report(metrics, args.note))
        csv_path = args.out / f"{raw['provider']}_results.csv"
        write_csv(raw, csv_path)
        print(f"wrote {report_path} and {csv_path}")
    if len(all_metrics) > 1:
        comparison_path = args.out / "comparison_report.md"
        comparison_path.write_text(render_comparison(all_metrics))
        print(f"wrote {comparison_path}")


if __name__ == "__main__":
    main()
