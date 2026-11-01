from datetime import date, timedelta

from claims_copilot.schemas import ExtractedClaim, FieldProvenance, LossType
from claims_copilot.services.confidence import adjust_claim_confidences


def _claim(**overrides) -> ExtractedClaim:
    base = ExtractedClaim(
        policy_number=FieldProvenance(value="POL-100001", confidence=0.9, source_file="form.pdf"),
        claimant_name=FieldProvenance(value="Alex Rivera", confidence=0.9, source_file="form.pdf"),
        date_of_loss=FieldProvenance(
            value=date(2026, 10, 20), confidence=0.9, source_file="form.pdf"
        ),
        loss_type=FieldProvenance(value=LossType.auto, confidence=0.9, source_file="photo.jpg"),
        incident_location=FieldProvenance(
            value="123 Main St", confidence=0.9, source_file="form.pdf"
        ),
        incident_description=FieldProvenance(
            value="Rear-end collision", confidence=0.9, source_file="form.pdf"
        ),
        estimated_damage_amount=FieldProvenance(
            value=4500.00, confidence=0.9, source_file="est.pdf"
        ),
        vehicle_or_property_details=FieldProvenance(
            value="2019 sedan", confidence=0.9, source_file="photo.jpg"
        ),
    )
    return base.model_copy(update=overrides)


def test_matching_known_policy_keeps_high_confidence() -> None:
    adjusted = adjust_claim_confidences(_claim(), {"POL-100001"})
    assert adjusted.policy_number.confidence == 0.9


def test_low_confidence_matching_policy_is_boosted_but_capped() -> None:
    claim = _claim(
        policy_number=FieldProvenance(value="POL-100001", confidence=0.2, source_file="form.pdf")
    )
    adjusted = adjust_claim_confidences(claim, {"POL-100001"})
    assert adjusted.policy_number.confidence == 0.9


def test_unknown_policy_number_is_capped_when_policies_are_loaded() -> None:
    adjusted = adjust_claim_confidences(_claim(), {"POL-999999"})
    assert adjusted.policy_number.confidence == 0.4


def test_malformed_policy_number_is_capped() -> None:
    claim = _claim(
        policy_number=FieldProvenance(value="not-a-policy", confidence=0.9, source_file="form.pdf")
    )
    adjusted = adjust_claim_confidences(claim)
    assert adjusted.policy_number.confidence == 0.3


def test_future_date_of_loss_is_capped() -> None:
    claim = _claim(
        date_of_loss=FieldProvenance(
            value=date.today() + timedelta(days=1), confidence=0.9, source_file="form.pdf"
        )
    )
    adjusted = adjust_claim_confidences(claim)
    assert adjusted.date_of_loss.confidence == 0.3


def test_non_positive_damage_amount_is_capped() -> None:
    claim = _claim(
        estimated_damage_amount=FieldProvenance(value=0.0, confidence=0.9, source_file="est.pdf")
    )
    adjusted = adjust_claim_confidences(claim)
    assert adjusted.estimated_damage_amount.confidence == 0.3


def test_empty_description_is_capped() -> None:
    claim = _claim(
        incident_description=FieldProvenance(value="   ", confidence=0.9, source_file="form.pdf")
    )
    adjusted = adjust_claim_confidences(claim)
    assert adjusted.incident_description.confidence == 0.2


def test_clean_claim_passes_through_unchanged() -> None:
    claim = _claim()
    assert adjust_claim_confidences(claim) == claim
