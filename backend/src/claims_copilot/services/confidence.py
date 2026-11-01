import re
from datetime import date

from claims_copilot.schemas import ExtractedClaim, FieldProvenance

_POLICY_PATTERN = re.compile(r"^POL-\d+$")

_LOW_CAP = 0.3
_UNKNOWN_POLICY_CAP = 0.4
_EMPTY_VALUE_CAP = 0.2
_KNOWN_POLICY_FLOOR = 0.9


def _cap[T](field: FieldProvenance[T], limit: float) -> FieldProvenance[T]:
    return field.model_copy(update={"confidence": min(field.confidence, limit)})


def _boost[T](field: FieldProvenance[T], floor: float) -> FieldProvenance[T]:
    return field.model_copy(update={"confidence": min(1.0, max(field.confidence, floor))})


def adjust_claim_confidences(
    claim: ExtractedClaim, known_policy_numbers: frozenset[str] = frozenset()
) -> ExtractedClaim:
    policy_number = claim.policy_number
    if policy_number.value is not None:
        if not _POLICY_PATTERN.match(policy_number.value):
            policy_number = _cap(policy_number, _LOW_CAP)
        elif known_policy_numbers:
            if policy_number.value in known_policy_numbers:
                policy_number = _boost(policy_number, _KNOWN_POLICY_FLOOR)
            else:
                policy_number = _cap(policy_number, _UNKNOWN_POLICY_CAP)

    date_of_loss = claim.date_of_loss
    if date_of_loss.value is not None and date_of_loss.value > date.today():
        date_of_loss = _cap(date_of_loss, _LOW_CAP)

    amount = claim.estimated_damage_amount
    if amount.value is not None and amount.value <= 0:
        amount = _cap(amount, _LOW_CAP)

    adjusted = {
        "policy_number": policy_number,
        "date_of_loss": date_of_loss,
        "estimated_damage_amount": amount,
    }
    for name in (
        "claimant_name",
        "incident_location",
        "incident_description",
        "vehicle_or_property_details",
    ):
        field = getattr(claim, name)
        if field.value is not None and not field.value.strip():
            field = _cap(field, _EMPTY_VALUE_CAP)
        adjusted[name] = field
    return claim.model_copy(update=adjusted)
