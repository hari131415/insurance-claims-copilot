"""Tests for the eval replay harness and the triage rules it verifies.

The replay lives in evals/, outside the backend package, so it is
loaded by path. No API calls: records are synthetic, policies are
the real sample policies, and the consistency verdict comes from
the record's saved reasons (empty here means consistent).
"""

import importlib.util
from datetime import timedelta
from pathlib import Path

from claims_copilot.agents import triage as triage_module
from claims_copilot.schemas import LossType
from claims_copilot.services.policies import (
    default_policies_path,
    load_policies,
    required_documents_for,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICIES = load_policies(default_policies_path())


def _load_replay():
    path = REPO_ROOT / "evals" / "replay.py"
    spec = importlib.util.spec_from_file_location("eval_replay", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


replay = _load_replay()
FIELD_KEYS = replay.FIELD_KEYS


def _policy_with_exclusion():
    for policy in POLICIES:
        if policy.excluded_loss_types:
            return policy
    raise AssertionError("no sample policy excludes a loss type")


def _fields(policy, loss_type, loss_confidence=0.95, date_confidence=0.95):
    date_of_loss = policy.active_from + timedelta(days=30)
    values = {
        "policy_number": policy.policy_number,
        "claimant_name": "Alex Rivera",
        "date_of_loss": date_of_loss.isoformat(),
        "loss_type": loss_type.value if loss_type else None,
        "incident_location": "1 Main St, Newark, NJ",
        "incident_description": "Damage described in the form.",
        "estimated_damage_amount": policy.deductible + 1000,
        "vehicle_or_property_details": "Vehicle details.",
    }
    confidences = {
        "date_of_loss": date_confidence,
        "loss_type": loss_confidence,
    }
    return {
        key: {"predicted": values[key], "confidence": confidences.get(key, 0.95)}
        for key in FIELD_KEYS
    }


def _classifications(policy, loss_type):
    requirements = required_documents_for(policy, loss_type)
    return [
        {"filename": f"doc_{index}.pdf", "predicted": requirement.document_type.value}
        for index, requirement in enumerate(requirements)
    ]


def _record(fields, classifications, reasons=None):
    return {
        "claim_id": "claim-9999",
        "status": "ok",
        "route_predicted": "needs_more_info",
        "reasons_predicted": reasons or [],
        "fields": fields,
        "classifications": classifications,
    }


def test_exclusion_demoted_below_bar():
    policy = _policy_with_exclusion()
    loss_type = policy.excluded_loss_types[0]
    record = _record(
        _fields(policy, loss_type, loss_confidence=0.5),
        _classifications(policy, loss_type),
    )
    route, reasons = replay.replay_record(record, None, POLICIES)
    assert route == "needs_more_info"
    assert any("exclusion not applied" in reason for reason in reasons)


def test_exclusion_kept_above_bar():
    policy = _policy_with_exclusion()
    loss_type = policy.excluded_loss_types[0]
    record = _record(
        _fields(policy, loss_type, loss_confidence=0.95),
        _classifications(policy, loss_type),
    )
    route, _ = replay.replay_record(record, None, POLICIES)
    assert route == "likely_not_covered"


def test_simulate_stated_nulls_inferred_loss_type():
    policy = _policy_with_exclusion()
    loss_type = policy.excluded_loss_types[0]
    record = _record(
        _fields(policy, loss_type, loss_confidence=0.95),
        _classifications(policy, loss_type),
    )
    label_claim = {
        "fields": {key: (None if key == "loss_type" else "present") for key in FIELD_KEYS}
    }
    route, reasons = replay.replay_record(record, label_claim, POLICIES, True)
    assert route == "needs_more_info"
    assert any("Loss type was not extracted" in reason for reason in reasons)


def test_threshold_flags_mid_band_date(monkeypatch):
    policy = POLICIES[0]
    loss_type = next(member for member in LossType if member not in policy.excluded_loss_types)
    record = _record(
        _fields(policy, loss_type, date_confidence=0.65),
        _classifications(policy, loss_type),
    )
    monkeypatch.setattr(triage_module, "LOW_CONFIDENCE_THRESHOLD", 0.6)
    route, _ = replay.replay_record(record, None, POLICIES)
    assert route == "ready_for_adjuster"
    monkeypatch.setattr(triage_module, "LOW_CONFIDENCE_THRESHOLD", 0.7)
    route, reasons = replay.replay_record(record, None, POLICIES)
    assert route == "needs_more_info"
    assert any("low confidence (0.65)" in reason for reason in reasons)
