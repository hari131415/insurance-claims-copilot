"""Prompt-structure guards for the extraction and consistency prompts.

These assert the requirements the evals depend on, not the full text:
extraction must refuse inference, and consistency must demand a
specific two-source contradiction with a severity.
"""

from claims_copilot.providers.base import InputFile
from claims_copilot.providers.llm import (
    EXTRACTION_INSTRUCTIONS,
    consistency_instructions,
)
from claims_copilot.schemas import ExtractedClaim, FieldProvenance


def _claim() -> ExtractedClaim:
    def field(value):
        return FieldProvenance(value=value, confidence=0.9, source_file="form.pdf")

    return ExtractedClaim(
        policy_number=field("POL-100001"),
        claimant_name=field("Alex Rivera"),
        date_of_loss=field("2026-10-20"),
        loss_type=field("auto"),
        incident_location=field("1 Main St"),
        incident_description=field("Rear-ended at a light."),
        estimated_damage_amount=field(4500.0),
        vehicle_or_property_details=field("Sedan, rear bumper."),
    )


def test_extraction_prompt_forbids_inference() -> None:
    assert "Damage visible in a photo does not state" in EXTRACTION_INSTRUCTIONS
    assert "return null with a low confidence and source_file null" in EXTRACTION_INSTRUCTIONS
    assert "Never infer, guess, or fill in a value" in EXTRACTION_INSTRUCTIONS


def test_consistency_prompt_requires_two_sources_and_severity() -> None:
    text = consistency_instructions(
        "Rear-ended at a light.",
        _claim(),
        [InputFile(filename="form.pdf", content_type="application/pdf", data=b"")],
    )
    assert "cite what each source says" in text
    assert "major" in text and "minor" in text
    assert "Vague unease is not a contradiction" in text
