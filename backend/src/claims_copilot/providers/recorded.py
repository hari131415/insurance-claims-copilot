import hashlib
import json
from pathlib import Path
from typing import Any, cast

from claims_copilot.providers.base import BaseProvider, InputFile
from claims_copilot.schemas import (
    ConsistencyResult,
    ExtractedClaim,
    ExtractionResult,
    FieldProvenance,
    FileClassification,
    TokenUsage,
)

UNKNOWN_FILES_MESSAGE = (
    "No recorded Claude output for these files. The recorded provider only "
    "replays files from the eval dataset (see evals/results/anthropic_raw.json)."
)

# The eval run did not save notes for consistent verdicts, so replays
# use this placeholder. Flagged verdicts replay the model's own notes.
CONSISTENT_NOTES = "No contradiction found."

_RECORDING_FILENAME = "recorded_data.json"


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def recording_key(files: list[InputFile]) -> str:
    hashes = sorted(file_hash(item.data) for item in files)
    return hashlib.sha256("\n".join(hashes).encode()).hexdigest()


def parse_flagged_notes(reason: str) -> str | None:
    prefix = "Consistency check flagged a problem: "
    if reason.startswith(prefix):
        return reason[len(prefix) :]
    return None


def reconstruct_source_file(field_key: str, files: list[dict[str, str]]) -> str | None:
    # The eval run did not save per-field source files. This
    # reconstruction cites the dataset document that states the value:
    # the claim form for every field except the amount, which the
    # repair estimate states. The cited document does contain the
    # value, even though it may differ from the file the model cited.
    if not files:
        return None
    if field_key == "estimated_damage_amount":
        for entry in files:
            if entry["document_type"] == "repair_estimate":
                return entry["filename"]
    for entry in files:
        if entry["document_type"] == "claim_form":
            return entry["filename"]
    return files[0]["filename"]


def load_recording() -> dict[str, Any]:
    path = Path(__file__).parent / _RECORDING_FILENAME
    return cast(dict[str, Any], json.loads(path.read_text()))


class RecordedProvider(BaseProvider):
    name = "recorded"
    model = "claude-sonnet-5-5"

    def __init__(self, recording: dict | None = None) -> None:
        data = recording if recording is not None else load_recording()
        self._by_key: dict[str, list[dict]] = {}
        for entry in data["claims"]:
            self._by_key.setdefault(entry["key"], []).append(entry)

    def _lookup(self, files: list[InputFile]) -> dict:
        entries = self._by_key.get(recording_key(files))
        if not entries:
            raise ValueError(UNKNOWN_FILES_MESSAGE)
        if len(entries) == 1:
            return entries[0]
        # Same bytes, different claims: the dataset has photo-only
        # claims sharing one photo. Filenames break the tie; a renamed
        # copy falls back to the first entry, which is still real
        # output for the same photo.
        filenames = sorted(item.filename for item in files)
        for entry in entries:
            if entry["filenames"] == filenames:
                return entry
        return entries[0]

    async def extract(self, files: list[InputFile]) -> ExtractionResult:
        entry = self._lookup(files)
        claim = ExtractedClaim(
            **{key: FieldProvenance(**value) for key, value in entry["extraction"].items()}
        )
        return ExtractionResult(
            provider=self.name,
            model=self.model,
            claim=claim,
            # Token counts were not saved per claim; the cost is the
            # claim's full original run cost (extraction plus the
            # consistency call), which the UI reports as such.
            usage=TokenUsage(cost_usd=entry["original_cost_usd"]),
            classifications=[FileClassification(**item) for item in entry["classifications"]],
        )

    async def check_consistency(
        self, description: str, claim: ExtractedClaim, files: list[InputFile]
    ) -> ConsistencyResult:
        saved = self._lookup(files)["consistency"]
        return ConsistencyResult(
            consistent=saved["consistent"],
            notes=saved["notes"],
            severity=None,
            usage=None,
        )
