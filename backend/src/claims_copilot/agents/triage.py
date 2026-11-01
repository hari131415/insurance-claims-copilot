from datetime import UTC, datetime
from functools import partial
from operator import add
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from claims_copilot.providers.base import BaseProvider, InputFile
from claims_copilot.schemas import (
    ConsistencyResult,
    ExtractedClaim,
    TriageCheck,
    TriageFile,
    TriageResult,
    TriageRoute,
)
from claims_copilot.services.policies import (
    Policy,
    document_satisfied,
    get_policy,
    required_documents_for,
)

# Chosen from dev-split (seed 123, 100 claims) calibration only, so
# no test-split data tunes this constant. On dev, the 0.4-0.7 band
# is mostly wrong (3/17 correct) while buckets at 0.8+ are mostly
# right (712/722), so 0.7 is the lowest cut that flags the whole
# observed wrong band. Evidence is thin: the 0.5-0.7 buckets hold
# 4 fields on dev and the 0.7-0.8 bucket is empty, so 0.7 vs 0.8
# is indistinguishable on dev; 0.7 wins the tie as the lower bar.
# See decisions.md.
LOW_CONFIDENCE_THRESHOLD = 0.7

# A hard failure that sends a claim to the coverage-review queue
# (inactive policy, excluded loss type, amount at or below the
# deductible) needs more than a guess: the deciding field must
# clear this bar, or the failure is demoted to a soft issue and
# the claim routes to needs_more_info instead. The bar is
# dev-derived (seed 123, 100 claims, no test data): it sits above
# every sub-95% bucket, with 0.8-0.9 only 72.2% accurate on dev
# (13/18, a small bucket) against 99.3% for 0.9-1.0 (699/704),
# because pointing an adjuster toward denial is the most serious
# error class. The companion rule is in the extraction prompt: a
# field not stated in any document must come back null, so a
# present value is a stated value, not an inference.
EXCLUSION_CONFIDENCE_BAR = 0.9

_FIELD_LABELS = {
    "policy_number": "Policy number",
    "claimant_name": "Claimant name",
    "date_of_loss": "Date of loss",
    "loss_type": "Loss type",
    "incident_location": "Incident location",
    "incident_description": "Incident description",
    "estimated_damage_amount": "Estimated damage amount",
    "vehicle_or_property_details": "Vehicle or property details",
}


class TriageState(TypedDict):
    claim: ExtractedClaim
    policy: Policy | None
    files: list[TriageFile]
    checks: Annotated[list[TriageCheck], add]
    hard_failures: Annotated[list[str], add]
    soft_issues: Annotated[list[str], add]
    limit_notes: Annotated[list[str], add]
    consistency: ConsistencyResult | None
    route: TriageRoute | None
    reasons: list[str]


def validate_extraction(state: TriageState) -> dict:
    claim = state["claim"]
    checks: list[TriageCheck] = []
    issues: list[str] = []
    for name, label in _FIELD_LABELS.items():
        field = getattr(claim, name)
        if field.value is None:
            checks.append(
                TriageCheck(
                    name=f"extraction:{name}",
                    passed=False,
                    detail=f"{label} was not extracted.",
                )
            )
            issues.append(f"{label} was not extracted from the documents.")
        elif field.confidence < LOW_CONFIDENCE_THRESHOLD:
            checks.append(
                TriageCheck(
                    name=f"extraction:{name}",
                    passed=False,
                    detail=(
                        f"{label} confidence is {field.confidence:.2f}, "
                        f"below {LOW_CONFIDENCE_THRESHOLD}."
                    ),
                )
            )
            issues.append(
                f"{label} has low confidence ({field.confidence:.2f}) and needs adjuster review."
            )
        else:
            checks.append(
                TriageCheck(
                    name=f"extraction:{name}",
                    passed=True,
                    detail=f"{label} extracted with confidence {field.confidence:.2f}.",
                )
            )
    return {"checks": checks, "soft_issues": issues}


def check_coverage(state: TriageState) -> dict:
    claim = state["claim"]
    policy = state["policy"]
    checks: list[TriageCheck] = []
    hard: list[str] = []
    soft: list[str] = []
    notes: list[str] = []
    if policy is None:
        number = claim.policy_number.value or "unknown"
        checks.append(
            TriageCheck(
                name="coverage:policy_found",
                passed=False,
                detail=f"Policy {number} was not found.",
            )
        )
        soft.append(f"Policy {number} was not found in the system; verify the policy number.")
        return {"checks": checks, "soft_issues": soft}

    date_of_loss = claim.date_of_loss.value
    if date_of_loss is None:
        checks.append(
            TriageCheck(
                name="coverage:policy_active",
                passed=False,
                detail="Date of loss is missing.",
            )
        )
        soft.append("Date of loss is missing, so policy coverage dates could not be checked.")
    elif not (policy.active_from <= date_of_loss <= policy.active_to):
        checks.append(
            TriageCheck(
                name="coverage:policy_active",
                passed=False,
                detail=(
                    f"Policy active {policy.active_from} to {policy.active_to}; "
                    f"loss on {date_of_loss}."
                ),
            )
        )
        if claim.date_of_loss.confidence < EXCLUSION_CONFIDENCE_BAR:
            soft.append(
                "Policy inactivity not applied: date of loss reported at confidence "
                f"{claim.date_of_loss.confidence:.2f}, below the {EXCLUSION_CONFIDENCE_BAR} "
                "bar for routing to the coverage-review queue; needs adjuster review."
            )
        else:
            hard.append(f"Policy was not active on the date of loss ({date_of_loss}).")
    else:
        checks.append(
            TriageCheck(
                name="coverage:policy_active",
                passed=True,
                detail="Policy was active on the date of loss.",
            )
        )

    loss_type = claim.loss_type.value
    if loss_type is not None and loss_type in policy.excluded_loss_types:
        checks.append(
            TriageCheck(
                name="coverage:excluded_loss_type",
                passed=False,
                detail=f"Loss type {loss_type.value} is excluded.",
            )
        )
        if claim.loss_type.confidence < EXCLUSION_CONFIDENCE_BAR:
            soft.append(
                "Loss-type exclusion not applied: loss type "
                f"{loss_type.value} reported at confidence "
                f"{claim.loss_type.confidence:.2f}, below the {EXCLUSION_CONFIDENCE_BAR} "
                "bar for routing to the coverage-review queue; needs adjuster review."
            )
        else:
            hard.append(f"Loss type {loss_type.value} is excluded under this policy.")
    elif loss_type is not None:
        checks.append(
            TriageCheck(
                name="coverage:excluded_loss_type",
                passed=True,
                detail=f"Loss type {loss_type.value} is not excluded.",
            )
        )

    amount = claim.estimated_damage_amount.value
    if amount is not None:
        if amount <= policy.deductible:
            checks.append(
                TriageCheck(
                    name="coverage:deductible",
                    passed=False,
                    detail=(
                        f"Estimated damage ${amount:,.2f} is at or below "
                        f"the ${policy.deductible:,.2f} deductible."
                    ),
                )
            )
            if claim.estimated_damage_amount.confidence < EXCLUSION_CONFIDENCE_BAR:
                soft.append(
                    "Deductible exclusion not applied: estimated damage reported at "
                    f"confidence {claim.estimated_damage_amount.confidence:.2f}, below the "
                    f"{EXCLUSION_CONFIDENCE_BAR} bar for routing to the coverage-review "
                    "queue; needs adjuster review."
                )
            else:
                hard.append(
                    f"Estimated damage (${amount:,.2f}) is at or below the deductible "
                    f"(${policy.deductible:,.2f}), so no payout would be due."
                )
        else:
            checks.append(
                TriageCheck(
                    name="coverage:deductible",
                    passed=True,
                    detail=(f"Estimated damage exceeds the ${policy.deductible:,.2f} deductible."),
                )
            )
        if amount > policy.coverage_limit:
            checks.append(
                TriageCheck(
                    name="coverage:within_limit",
                    passed=False,
                    detail=(
                        f"Estimated damage ${amount:,.2f} exceeds "
                        f"the ${policy.coverage_limit:,.2f} limit."
                    ),
                )
            )
            notes.append(
                f"Estimated damage (${amount:,.2f}) exceeds the coverage limit "
                f"(${policy.coverage_limit:,.2f}); payout would be capped at the limit."
            )
        else:
            checks.append(
                TriageCheck(
                    name="coverage:within_limit",
                    passed=True,
                    detail="Estimated damage is within the coverage limit.",
                )
            )
    return {"checks": checks, "hard_failures": hard, "soft_issues": soft, "limit_notes": notes}


def check_required_documents(state: TriageState) -> dict:
    policy = state["policy"]
    if policy is None:
        return {}
    claim = state["claim"]
    required = required_documents_for(policy, claim.loss_type.value)
    checks: list[TriageCheck] = []
    issues: list[str] = []
    for requirement in required:
        if document_satisfied(requirement, state["files"]):
            checks.append(
                TriageCheck(
                    name=f"documents:{requirement.name}",
                    passed=True,
                    detail=f"Found: {requirement.name}.",
                )
            )
        else:
            checks.append(
                TriageCheck(
                    name=f"documents:{requirement.name}",
                    passed=False,
                    detail=f"Missing: {requirement.name}.",
                )
            )
            issues.append(f"Missing required document: {requirement.name}.")
    return {"checks": checks, "soft_issues": issues}


async def check_consistency(
    state: TriageState, provider: BaseProvider, files: list[InputFile]
) -> dict:
    description = state["claim"].incident_description.value
    if not description:
        return {}
    result = await provider.check_consistency(description, state["claim"], files)
    updates: dict = {"consistency": result}
    # Only a major contradiction routes to needs_more_info. A minor
    # one stays visible in the consistency result for the adjuster
    # but does not change the route. A missing severity (older
    # verdicts, or a model that omitted it) fails safe as major.
    if not result.consistent and result.severity != "minor":
        updates["soft_issues"] = [f"Consistency check flagged a problem: {result.notes}"]
    return updates


def _consistency_skipped(state: TriageState) -> bool:
    policy = state["policy"]
    if policy is None:
        return True
    date_of_loss = state["claim"].date_of_loss.value
    return date_of_loss is not None and not (policy.active_from <= date_of_loss <= policy.active_to)


def _route_after_documents(state: TriageState) -> str:
    return "decide_route" if _consistency_skipped(state) else "check_consistency"


def decide_route(state: TriageState) -> dict:
    if state["hard_failures"]:
        reasons = [*state["hard_failures"], *state["soft_issues"], *state["limit_notes"]]
        route = TriageRoute.likely_not_covered
    elif state["soft_issues"]:
        reasons = [*state["soft_issues"], *state["limit_notes"]]
        route = TriageRoute.needs_more_info
    else:
        reasons = ["All required checks passed.", *state["limit_notes"]]
        route = TriageRoute.ready_for_adjuster
    if state["consistency"] is None and _consistency_skipped(state):
        reasons.append(
            "Consistency check skipped: policy unknown or inactive on the date of loss, "
            "so the claim routes on deterministic checks alone."
        )
    return {"route": route, "reasons": reasons}


def build_triage_graph(provider: BaseProvider, inputs: list[InputFile]) -> CompiledStateGraph:
    graph = StateGraph(TriageState)
    graph.add_node("validate_extraction", validate_extraction)
    graph.add_node("check_coverage", check_coverage)
    graph.add_node("check_required_documents", check_required_documents)
    graph.add_node("check_consistency", partial(check_consistency, provider=provider, files=inputs))
    graph.add_node("decide_route", decide_route)
    graph.set_entry_point("validate_extraction")
    graph.add_edge("validate_extraction", "check_coverage")
    graph.add_edge("check_coverage", "check_required_documents")
    graph.add_conditional_edges(
        "check_required_documents",
        _route_after_documents,
        {"check_consistency": "check_consistency", "decide_route": "decide_route"},
    )
    graph.add_edge("check_consistency", "decide_route")
    graph.add_edge("decide_route", END)
    return graph.compile()


async def run_triage(
    claim: ExtractedClaim,
    files: list[TriageFile],
    inputs: list[InputFile],
    provider: BaseProvider,
    policies: list[Policy],
    policy_version: str,
) -> TriageResult:
    policy = get_policy(policies, claim.policy_number.value)
    graph = build_triage_graph(provider, inputs)
    state = await graph.ainvoke(
        {
            "claim": claim,
            "policy": policy,
            "files": files,
            "checks": [],
            "hard_failures": [],
            "soft_issues": [],
            "limit_notes": [],
            "consistency": None,
            "route": None,
            "reasons": [],
        }
    )
    route = state["route"]
    if route is None:
        raise RuntimeError("triage graph finished without a route")
    return TriageResult(
        route=route,
        reasons=state["reasons"],
        checks=state["checks"],
        consistency=state["consistency"],
        triaged_at=datetime.now(UTC),
        policy_version=policy_version,
    )
