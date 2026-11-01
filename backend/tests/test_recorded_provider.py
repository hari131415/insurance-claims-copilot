import pytest

from claims_copilot.providers.base import InputFile
from claims_copilot.providers.recorded import (
    UNKNOWN_FILES_MESSAGE,
    RecordedProvider,
    load_recording,
    parse_flagged_notes,
    reconstruct_source_file,
    recording_key,
)


def _recording() -> dict:
    return {
        "claims": [
            {
                "key": recording_key([InputFile("a.pdf", "application/pdf", b"form")]),
                "filenames": ["a.pdf"],
                "original_cost_usd": 0.021,
                "extraction": {
                    "policy_number": {
                        "value": "POL-100001",
                        "confidence": 0.9,
                        "source_file": "a.pdf",
                    },
                    "claimant_name": {
                        "value": "Alex Rivera",
                        "confidence": 0.85,
                        "source_file": "a.pdf",
                    },
                    "date_of_loss": {
                        "value": "2026-10-20",
                        "confidence": 0.85,
                        "source_file": "a.pdf",
                    },
                    "loss_type": {
                        "value": "auto",
                        "confidence": 0.9,
                        "source_file": "a.pdf",
                    },
                    "incident_location": {
                        "value": "123 Main St",
                        "confidence": 0.85,
                        "source_file": "a.pdf",
                    },
                    "incident_description": {
                        "value": "Rear-end collision.",
                        "confidence": 0.85,
                        "source_file": "a.pdf",
                    },
                    "estimated_damage_amount": {
                        "value": 4500.0,
                        "confidence": 0.85,
                        "source_file": "a.pdf",
                    },
                    "vehicle_or_property_details": {
                        "value": "Sedan",
                        "confidence": 0.85,
                        "source_file": "a.pdf",
                    },
                },
                "classifications": [
                    {
                        "filename": "a.pdf",
                        "document_type": "claim_form",
                        "confidence": 0.9,
                    }
                ],
                "consistency": {"consistent": False, "notes": "Amounts disagree."},
            }
        ]
    }


def test_key_ignores_file_order_and_names() -> None:
    a = [InputFile("x.pdf", "application/pdf", b"one")]
    b = [
        InputFile("y.pdf", "application/pdf", b"two"),
        InputFile("x.pdf", "application/pdf", b"one"),
    ]
    c = [InputFile("x.pdf", "application/pdf", b"one")]
    assert recording_key(a) == recording_key(c)
    assert recording_key(a) != recording_key(b)
    assert recording_key(b) == recording_key(
        list(reversed(b)),
    )


async def test_extract_replays_recorded_values() -> None:
    provider = RecordedProvider(_recording())
    result = await provider.extract([InputFile("a.pdf", "application/pdf", b"form")])
    assert result.provider == "recorded"
    assert result.model == "claude-sonnet-5-5"
    assert result.claim.claimant_name.value == "Alex Rivera"
    assert result.claim.estimated_damage_amount.value == 4500.0
    assert result.usage.cost_usd == 0.021
    assert result.classifications[0].document_type.value == "claim_form"


async def test_extract_unknown_files_fails_clearly() -> None:
    provider = RecordedProvider(_recording())
    with pytest.raises(ValueError, match="No recorded Claude output"):
        await provider.extract([InputFile("other.pdf", "application/pdf", b"nope")])
    assert "eval dataset" in UNKNOWN_FILES_MESSAGE


async def test_consistency_replays_recorded_verdict() -> None:
    provider = RecordedProvider(_recording())
    claim = (await provider.extract([InputFile("a.pdf", "application/pdf", b"form")])).claim
    verdict = await provider.check_consistency(
        "Rear-end collision.", claim, [InputFile("a.pdf", "application/pdf", b"form")]
    )
    assert verdict.consistent is False
    assert verdict.notes == "Amounts disagree."
    assert verdict.severity is None


async def test_collision_falls_back_to_filename_match() -> None:
    base = _recording()["claims"][0]
    first = dict(base)
    first["key"] = recording_key([InputFile("photo.jpg", "image/jpeg", b"same-photo")])
    first["filenames"] = ["photo.jpg"]
    second = dict(base)
    second["key"] = first["key"]
    second["filenames"] = ["other.jpg"]
    second["extraction"] = {
        **first["extraction"],
        "claimant_name": {
            "value": "Sam Park",
            "confidence": 0.85,
            "source_file": "other.jpg",
        },
    }
    provider = RecordedProvider({"claims": [first, second]})
    result = await provider.extract([InputFile("other.jpg", "image/jpeg", b"same-photo")])
    assert result.claim.claimant_name.value == "Sam Park"
    renamed = await provider.extract([InputFile("renamed.jpg", "image/jpeg", b"same-photo")])
    assert renamed.claim.claimant_name.value == "Alex Rivera"


def test_parse_flagged_notes() -> None:
    assert (
        parse_flagged_notes("Consistency check flagged a problem: Photos differ.")
        == "Photos differ."
    )
    assert parse_flagged_notes("All required checks passed.") is None


def test_reconstruct_source_file() -> None:
    files = [
        {"filename": "form.pdf", "document_type": "claim_form"},
        {"filename": "est.pdf", "document_type": "repair_estimate"},
    ]
    assert reconstruct_source_file("claimant_name", files) == "form.pdf"
    assert reconstruct_source_file("estimated_damage_amount", files) == "est.pdf"
    assert reconstruct_source_file("claimant_name", []) is None


def test_real_recording_loads_with_one_documented_collision() -> None:
    data = load_recording()
    assert data["model"] == "claude-sonnet-5-5"
    assert len(data["claims"]) == 300
    keys = [entry["key"] for entry in data["claims"]]
    # Two photo-only claims share one photo's bytes; the provider
    # disambiguates them by filename.
    assert len(set(keys)) == 299
    assert sorted(data["collisions"].values()) == [["claim-0171", "claim-0288"]]
    for entry in data["claims"]:
        assert isinstance(entry["original_cost_usd"], (int, float))
        assert entry["original_cost_usd"] >= 0
        assert set(entry["extraction"]) == {
            "policy_number",
            "claimant_name",
            "date_of_loss",
            "loss_type",
            "incident_location",
            "incident_description",
            "estimated_damage_amount",
            "vehicle_or_property_details",
        }
        assert isinstance(entry["consistency"]["consistent"], bool)
        assert isinstance(entry["consistency"]["notes"], str)
