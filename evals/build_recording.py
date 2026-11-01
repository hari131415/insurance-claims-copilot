"""Build the recorded provider's replay file from the saved Claude run.

Reads evals/results/anthropic_raw.json (predicted values, confidences,
triage reasons) plus evals/data/labels.json and evals/data/files (file
bytes for the content-hash keys), and writes
backend/src/claims_copilot/providers/recorded_data.json.

Two pieces the eval run did not save are reconstructed, and the
recording says so in its header: per-field source files (the dataset
document that states the value) and classification confidences (the
claim's mean field confidence). Consistent-verdict notes use the
placeholder from claims_copilot.providers.recorded, since the run
saved notes only for flagged verdicts. Everything else is the saved
model output, keyed by the hash of the claim's file set so uploads
match regardless of filename.

Run from the repo root: backend/.venv/bin/python evals/build_recording.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from claims_copilot.providers.base import InputFile  # noqa: E402
from claims_copilot.providers.recorded import (  # noqa: E402
    CONSISTENT_NOTES,
    parse_flagged_notes,
    reconstruct_source_file,
    recording_key,
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

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    raw = json.loads((root / "evals" / "results" / "anthropic_raw.json").read_text())
    labels = {
        claim["claim_id"]: claim
        for claim in json.loads(
            (root / "evals" / "data" / "labels.json").read_text()
        )["claims"]
    }
    files_dir = root / "evals" / "data" / "files"

    claims = []
    seen_keys: dict[str, list[str]] = {}
    for record in raw["claims"]:
        if record["status"] != "ok":
            continue
        label = labels[record["claim_id"]]
        inputs = []
        for entry in label["files"]:
            path = files_dir / entry["filename"]
            inputs.append(
                InputFile(
                    filename=entry["filename"],
                    content_type=CONTENT_TYPES[path.suffix.lower()],
                    data=path.read_bytes(),
                )
            )
        key = recording_key(inputs)
        seen_keys.setdefault(key, []).append(record["claim_id"])

        confidences = [
            record["fields"][field_key]["confidence"] for field_key in FIELD_KEYS
        ]
        mean_confidence = sum(confidences) / len(confidences)
        predicted_type = {
            item["filename"]: item["predicted"] for item in record["classifications"]
        }
        flagged_notes = next(
            (
                notes
                for reason in record["reasons_predicted"]
                if (notes := parse_flagged_notes(reason)) is not None
            ),
            None,
        )
        claims.append(
            {
                "claim_id": record["claim_id"],
                "key": key,
                "filenames": sorted(entry["filename"] for entry in label["files"]),
                # The record's combined cost: extraction plus the
                # consistency call. The provider reports it as the
                # claim's original cost; the run did not split the two.
                "original_cost_usd": record["cost_usd"],
                "extraction": {
                    field_key: {
                        "value": record["fields"][field_key]["predicted"],
                        "confidence": record["fields"][field_key]["confidence"],
                        "source_file": reconstruct_source_file(
                            field_key, label["files"]
                        ),
                    }
                    for field_key in FIELD_KEYS
                },
                "classifications": [
                    {
                        "filename": entry["filename"],
                        "document_type": predicted_type[entry["filename"]],
                        "confidence": mean_confidence,
                    }
                    for entry in label["files"]
                ],
                "consistency": {
                    "consistent": flagged_notes is None,
                    "notes": flagged_notes
                    if flagged_notes is not None
                    else CONSISTENT_NOTES,
                },
            }
        )

    collisions = {
        key: claim_ids for key, claim_ids in seen_keys.items() if len(claim_ids) > 1
    }
    out = {
        "model": "claude-sonnet-5-5",
        "source_run": "evals/results/anthropic_raw.json",
        "collision_note": (
            "Content keys shared by more than one claim (photo-only claims "
            "with identical photo bytes); the provider disambiguates those "
            "by filename, falling back to the first entry for a renamed copy."
            if collisions
            else None
        ),
        "collisions": collisions,
        "reconstruction_note": (
            "Per-field source files and classification confidences were not "
            "saved by the eval run and are reconstructed: source_file cites "
            "the dataset document that states the value (claim form, or the "
            "repair estimate for amounts), and classification confidence is "
            "the claim's mean field confidence. Consistent-verdict notes use "
            "a placeholder; flagged verdicts replay the model's own notes."
        ),
        "claims": claims,
    }
    target = (
        root
        / "backend"
        / "src"
        / "claims_copilot"
        / "providers"
        / "recorded_data.json"
    )
    target.write_text(json.dumps(out, indent=1, sort_keys=True))
    print(f"wrote {target} ({len(claims)} claims, {target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
