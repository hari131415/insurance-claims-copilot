"""Replay saved raw eval results through the triage graph's deterministic nodes.

No API calls: the consistency verdict is recovered from the saved
reasons ("Consistency check flagged..." means inconsistent), and
extraction, classifications, and policy lookup are rebuilt from the
saved record. Use it to measure triage-rule changes (confidence
threshold, exclusion bar) before spending anything on a live run.

--threshold and --exclusion-bar override the triage module's
constants for the replay. With no overrides, the replay runs the
code's current constants, which must reproduce the saved routes
exactly; that reproduction is the harness's self-check.

--simulate-stated nulls every field the labels say is absent,
modelling the extraction-prompt fix ("return null when a field
isn't stated in any document; never infer") without a live run.
The app itself cannot tell an inferred value from a stated one,
which is why the prompt fix is the companion to the exclusion bar.

Run from the repo root with the backend venv:
    backend/.venv/bin/python evals/replay.py --results evals/results/anthropic_raw.json
    backend/.venv/bin/python evals/replay.py --results evals/results/anthropic_raw.json \\
        --threshold 0.7 --exclusion-bar 0.9 --simulate-stated
"""

import argparse
import json
import os
from collections import Counter
from pathlib import Path

# Same proxy pin as the runner: provider SDKs misparse bracketed
# IPv6 entries in no_proxy when building httpx clients.
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

from claims_copilot.agents import triage as triage_module  # noqa: E402
from claims_copilot.schemas import (  # noqa: E402
    ConsistencyResult,
    DocumentType,
    ExtractedClaim,
    FieldProvenance,
    TriageFile,
)
from claims_copilot.services.policies import (  # noqa: E402
    default_policies_path,
    get_policy,
    load_policies,
)

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
FLAG_PREFIX = "Consistency check flagged a problem: "


def _content_type(filename: str) -> str:
    return "application/pdf" if filename.endswith(".pdf") else "image/jpeg"


def triage_with_consistency(
    claim: ExtractedClaim,
    files: list[TriageFile],
    policies: list,
    consistency: ConsistencyResult | None,
) -> tuple[str | None, list[str]]:
    """Run the deterministic triage nodes with a given consistency verdict.

    consistency=None means the check is skipped (or there is no
    description): the verdict stays None and decide_route adds the
    skipped note itself when the graph would have skipped. A
    missing severity on an inconsistent verdict fails safe as
    major, matching the triage node's rule.
    """
    policy = get_policy(policies, claim.policy_number.value)
    state: triage_module.TriageState = {
        "claim": claim,
        "policy": policy,
        "files": files,
        "checks": [],
        "hard_failures": [],
        "soft_issues": [],
        "limit_notes": [],
        "consistency": consistency,
        "route": None,
        "reasons": [],
    }

    def merge(updates: dict) -> None:
        for key in ("checks", "hard_failures", "soft_issues", "limit_notes"):
            state[key].extend(updates.get(key, []))

    merge(triage_module.validate_extraction(state))
    merge(triage_module.check_coverage(state))
    merge(triage_module.check_required_documents(state))
    if consistency is not None and not consistency.consistent and consistency.severity != "minor":
        state["soft_issues"].append(f"Consistency check flagged a problem: {consistency.notes}")
    result = triage_module.decide_route(state)
    return result["route"].value, result["reasons"]


def build_claim_and_files(
    record: dict,
    label_claim: dict | None,
    simulate_stated: bool = False,
) -> tuple[ExtractedClaim, list[TriageFile]]:
    """Rebuild a saved record's extraction and file list for triage."""
    fields = {}
    for key in FIELD_KEYS:
        entry = record["fields"][key]
        value = entry["predicted"]
        if simulate_stated and label_claim is not None and label_claim["fields"][key] is None:
            value = None
        confidence = entry["confidence"] if entry["confidence"] is not None else 0.0
        fields[key] = FieldProvenance(value=value, confidence=confidence, source_file=None)
    claim = ExtractedClaim(**fields)
    files = [
        TriageFile(
            filename=item["filename"],
            content_type=_content_type(item["filename"]),
            document_type=DocumentType(item["predicted"])
            if item["predicted"]
            else DocumentType.other,
        )
        for item in record["classifications"]
    ]
    return claim, files


def replay_record(
    record: dict,
    label_claim: dict | None,
    policies: list,
    simulate_stated: bool = False,
) -> tuple[str | None, list[str]]:
    """Recompute one claim's route from its saved record.

    Returns (route, reasons). Failed records have no extraction to
    replay and pass through with their saved route (None). A saved
    flag replays as an inconsistent verdict with no severity, which
    the triage rule treats as major.
    """
    if record["status"] != "ok":
        return record["route_predicted"], record["reasons_predicted"]
    claim, files = build_claim_and_files(record, label_claim, simulate_stated)
    policy = get_policy(policies, claim.policy_number.value)
    state_policy = {"claim": claim, "policy": policy}
    if triage_module._consistency_skipped(state_policy) or not claim.incident_description.value:  # type: ignore[arg-type]
        consistency = None
    else:
        flagged_reason = next(
            (reason for reason in record["reasons_predicted"] if reason.startswith(FLAG_PREFIX)),
            None,
        )
        if flagged_reason is not None:
            consistency = ConsistencyResult(
                consistent=False, notes=flagged_reason[len(FLAG_PREFIX) :]
            )
        else:
            consistency = ConsistencyResult(consistent=True, notes="")
    return triage_with_consistency(claim, files, policies, consistency)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay saved eval results through triage.")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=Path("evals/data"))
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--exclusion-bar", type=float, default=None)
    parser.add_argument("--simulate-stated", action="store_true")
    args = parser.parse_args()

    if args.threshold is not None:
        triage_module.LOW_CONFIDENCE_THRESHOLD = args.threshold
    if args.exclusion_bar is not None:
        triage_module.EXCLUSION_CONFIDENCE_BAR = args.exclusion_bar
    print(
        f"replay: threshold={triage_module.LOW_CONFIDENCE_THRESHOLD} "
        f"exclusion_bar={triage_module.EXCLUSION_CONFIDENCE_BAR} "
        f"simulate_stated={args.simulate_stated}"
    )

    raw = json.loads(args.results.read_text())
    labels = json.loads((args.data / "labels.json").read_text())
    by_id = {claim["claim_id"]: claim for claim in labels["claims"]}
    policies = load_policies(default_policies_path())

    replayed = []
    mismatches = []
    for record in raw["claims"]:
        route, reasons = replay_record(
            record, by_id.get(record["claim_id"]), policies, args.simulate_stated
        )
        replayed.append((record, route, reasons))
        if route != record["route_predicted"]:
            mismatches.append(record["claim_id"])
    ok = [item for item in replayed if item[0]["status"] == "ok"]
    correct = [item for item in ok if item[1] == item[0]["route_expected"]]
    dangerous = [
        item
        for item in ok
        if item[1] == "likely_not_covered" and item[0]["route_expected"] != "likely_not_covered"
    ]
    saved_correct_deny = [
        item
        for item in ok
        if item[0]["route_predicted"] == "likely_not_covered"
        and item[0]["route_expected"] == "likely_not_covered"
    ]
    lost_deny = [item for item in saved_correct_deny if item[1] != "likely_not_covered"]
    ready_misses = [
        item
        for item in ok
        if item[0]["route_expected"] == "ready_for_adjuster" and item[1] == "needs_more_info"
    ]
    confusion = Counter((item[0]["route_expected"], item[1]) for item in ok)
    print(f"claims: {len(replayed)} ok: {len(ok)} failed: {len(replayed) - len(ok)}")
    print(f"route match vs saved: {len(replayed) - len(mismatches)}/{len(replayed)}")
    if mismatches:
        print(f"mismatched: {mismatches[:10]}")
    print(f"route accuracy: {len(correct)}/{len(ok)} = {len(correct) / len(ok):.1%}")
    print(f"dangerous errors: {len(dangerous)} {[item[0]['claim_id'] for item in dangerous]}")
    print(f"correct coverage-review routings lost: {len(lost_deny)}")
    print(f"ready -> needs_more_info: {len(ready_misses)}")
    print("confusion (expected -> replayed):")
    for expected in ("ready_for_adjuster", "needs_more_info", "likely_not_covered"):
        row = {
            p: confusion.get((expected, p), 0)
            for p in ("ready_for_adjuster", "needs_more_info", "likely_not_covered")
        }
        print(f"  {expected}: {row}")


if __name__ == "__main__":
    main()
