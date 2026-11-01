from helpers import _create_claim, _upload, _wait_for_extraction

from claims_copilot.providers.base import InputFile
from claims_copilot.providers.mock import MockProvider
from claims_copilot.services.extraction import ExtractionService
from claims_copilot.services.storage import LocalStorage


def _extracted_claim(client, claim_id: str) -> None:
    _upload(client, claim_id, "claim-form.pdf")
    client.post(f"/claims/{claim_id}/extract")
    _wait_for_extraction(client, claim_id)


def test_patch_keeps_original_and_records_override(client) -> None:
    claim_id = _create_claim(client)
    _extracted_claim(client, claim_id)
    before = client.get(f"/claims/{claim_id}").json()
    assert before["model_extraction"]["estimated_damage_amount"]["value"] == 4500.0
    assert before["overrides"] == []
    extraction = before["extraction"]
    extraction["estimated_damage_amount"]["value"] = 6000.00
    response = client.patch(f"/claims/{claim_id}", json=extraction)
    assert response.status_code == 200
    data = client.get(f"/claims/{claim_id}").json()
    assert data["extraction"]["estimated_damage_amount"]["value"] == 6000.0
    assert data["extraction"]["estimated_damage_amount"]["confidence"] == 1.0
    assert data["extraction"]["estimated_damage_amount"]["source_file"] == "adjuster"
    assert data["model_extraction"]["estimated_damage_amount"]["value"] == 4500.0
    assert len(data["overrides"]) == 1
    override = data["overrides"][0]
    assert override["field"] == "estimated_damage_amount"
    assert override["value"] == 6000.0
    assert override["model_value"] == 4500.0
    assert override["conflicts_with_model"] is False
    assert override["edited_at"] is not None


def test_patch_reverting_to_model_value_removes_override(client) -> None:
    claim_id = _create_claim(client)
    _extracted_claim(client, claim_id)
    data = client.get(f"/claims/{claim_id}").json()
    extraction = data["extraction"]
    extraction["estimated_damage_amount"]["value"] = 6000.00
    client.patch(f"/claims/{claim_id}", json=extraction)
    assert len(client.get(f"/claims/{claim_id}").json()["overrides"]) == 1
    extraction["estimated_damage_amount"]["value"] = 4500.00
    client.patch(f"/claims/{claim_id}", json=extraction)
    assert client.get(f"/claims/{claim_id}").json()["overrides"] == []


class SecondAmountProvider(MockProvider):
    def __init__(self, second_amount: float) -> None:
        self.calls = 0
        self.second_amount = second_amount

    async def extract(self, files: list[InputFile]):
        self.calls += 1
        result = await super().extract(files)
        if self.calls > 1:
            result.claim.estimated_damage_amount.value = self.second_amount
        return result


def _reextract_with_second_amount(client, test_app, monkeypatch, second_amount: float) -> str:
    _, engine, upload_dir = test_app
    provider = SecondAmountProvider(second_amount)
    monkeypatch.setattr(
        "claims_copilot.api.claims.build_extraction_service",
        lambda session: ExtractionService(engine, provider, LocalStorage(upload_dir)),
    )
    claim_id = _create_claim(client)
    _extracted_claim(client, claim_id)
    data = client.get(f"/claims/{claim_id}").json()
    extraction = data["extraction"]
    extraction["estimated_damage_amount"]["value"] = 6000.00
    client.patch(f"/claims/{claim_id}", json=extraction)
    client.post(f"/claims/{claim_id}/extract")
    _wait_for_extraction(client, claim_id)
    return claim_id


def test_reextraction_keeps_override_and_flags_disagreement(client, test_app, monkeypatch) -> None:
    claim_id = _reextract_with_second_amount(client, test_app, monkeypatch, 7000.00)
    data = client.get(f"/claims/{claim_id}").json()
    assert data["extraction"]["estimated_damage_amount"]["value"] == 6000.0
    assert data["model_extraction"]["estimated_damage_amount"]["value"] == 7000.0
    assert len(data["overrides"]) == 1
    assert data["overrides"][0]["conflicts_with_model"] is True


def test_reextraction_agreeing_with_override_is_not_a_conflict(
    client, test_app, monkeypatch
) -> None:
    claim_id = _reextract_with_second_amount(client, test_app, monkeypatch, 6000.00)
    data = client.get(f"/claims/{claim_id}").json()
    assert data["extraction"]["estimated_damage_amount"]["value"] == 6000.0
    assert data["model_extraction"]["estimated_damage_amount"]["value"] == 6000.0
    assert data["overrides"][0]["conflicts_with_model"] is False
