from datetime import date

import pytest
from pydantic import ValidationError

from claims_copilot.schemas import (
    ExtractedClaim,
    FieldProvenance,
    LossType,
)


def _claim() -> ExtractedClaim:
    return ExtractedClaim(
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
            value=4500.00, confidence=0.9, source_file="estimate.pdf"
        ),
        vehicle_or_property_details=FieldProvenance(
            value="2019 sedan", confidence=0.9, source_file="photo.jpg"
        ),
    )


def test_confidence_above_one_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FieldProvenance(value="x", confidence=1.5)


def test_confidence_below_zero_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FieldProvenance(value="x", confidence=-0.1)


def test_field_value_may_be_unknown() -> None:
    field = FieldProvenance[str](value=None, confidence=0.0, source_file=None)
    assert field.value is None


def test_extracted_claim_round_trips_through_json() -> None:
    claim = _claim()
    restored = ExtractedClaim.model_validate_json(claim.model_dump_json())
    assert restored == claim
    assert restored.loss_type.value == LossType.auto
    assert restored.date_of_loss.value == date(2026, 10, 20)
