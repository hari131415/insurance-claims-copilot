import json
from datetime import datetime
from typing import Any

from claims_copilot.schemas import ExtractedClaim, FieldOverride, FieldProvenance


def json_value(field: FieldProvenance) -> Any:
    return field.model_dump(mode="json")["value"]


def compute_overrides(
    original: ExtractedClaim, patched: ExtractedClaim, now: datetime
) -> dict[str, FieldOverride]:
    overrides: dict[str, FieldOverride] = {}
    for name in ExtractedClaim.model_fields:
        patched_value = json_value(getattr(patched, name))
        original_value = json_value(getattr(original, name))
        if patched_value != original_value:
            overrides[name] = FieldOverride(
                value=patched_value, model_value=original_value, edited_at=now
            )
    return overrides


def effective_extraction(
    original: ExtractedClaim, overrides: dict[str, FieldOverride]
) -> ExtractedClaim:
    data = original.model_dump(mode="json")
    for name, override in overrides.items():
        data[name] = {"value": override.value, "confidence": 1.0, "source_file": "adjuster"}
    return ExtractedClaim.model_validate(data)


def parse_overrides(raw: str | None) -> dict[str, FieldOverride]:
    if not raw:
        return {}
    data = json.loads(raw)
    return {name: FieldOverride.model_validate(item) for name, item in data.items()}


def serialize_overrides(overrides: dict[str, FieldOverride]) -> str:
    return json.dumps(
        {name: override.model_dump(mode="json") for name, override in overrides.items()}
    )


def override_conflicts(original: ExtractedClaim, overrides: dict[str, FieldOverride]) -> list[str]:
    conflicts: list[str] = []
    for name, override in overrides.items():
        current = json_value(getattr(original, name))
        if current != override.model_value and current != override.value:
            conflicts.append(name)
    return conflicts
