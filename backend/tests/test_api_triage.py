from helpers import _create_claim, _upload, _wait_for_extraction

from claims_copilot.services.policies import default_policies_path, hash_policies_file


def test_triage_before_extraction_returns_400(client) -> None:
    claim_id = _create_claim(client)
    _upload(client, claim_id)
    assert client.post(f"/claims/{claim_id}/triage").status_code == 400


def test_triage_after_extraction_returns_ready_route(client) -> None:
    claim_id = _create_claim(client)
    _upload(client, claim_id, "claim-form.pdf")
    _upload(client, claim_id, "repair-estimate.pdf")
    _upload(client, claim_id, "car-damage.jpg")
    client.post(f"/claims/{claim_id}/extract")
    _wait_for_extraction(client, claim_id)
    response = client.post(f"/claims/{claim_id}/triage")
    assert response.status_code == 200
    data = response.json()
    assert data["triage"]["route"] == "ready_for_adjuster"
    assert data["triage"]["reasons"] == ["All required checks passed."]
    assert data["triage"]["triaged_at"] is not None
    assert data["triage"]["policy_version"] == hash_policies_file(default_policies_path())
    assert client.get(f"/claims/{claim_id}").json()["triage"]["route"] == "ready_for_adjuster"


def test_triage_uses_adjuster_override_for_coverage(client) -> None:
    claim_id = _create_claim(client)
    _upload(client, claim_id, "claim-form.pdf")
    client.post(f"/claims/{claim_id}/extract")
    data = _wait_for_extraction(client, claim_id)
    extraction = data["extraction"]
    extraction["estimated_damage_amount"]["value"] = 100.00
    assert client.patch(f"/claims/{claim_id}", json=extraction).status_code == 200
    response = client.post(f"/claims/{claim_id}/triage")
    assert response.status_code == 200
    assert response.json()["triage"]["route"] == "likely_not_covered"
    assert any("deductible" in reason for reason in response.json()["triage"]["reasons"])


def test_triage_unknown_policy_notes_skipped_consistency(client) -> None:
    claim_id = _create_claim(client)
    _upload(client, claim_id, "claim-form.pdf")
    client.post(f"/claims/{claim_id}/extract")
    data = _wait_for_extraction(client, claim_id)
    extraction = data["extraction"]
    extraction["policy_number"]["value"] = "POL-999999"
    assert client.patch(f"/claims/{claim_id}", json=extraction).status_code == 200
    response = client.post(f"/claims/{claim_id}/triage")
    assert response.status_code == 200
    triage = response.json()["triage"]
    assert triage["route"] == "needs_more_info"
    assert triage["consistency"] is None
    assert any("skipped" in reason for reason in triage["reasons"])


def test_triage_after_patch_to_expired_policy_is_likely_not_covered(client) -> None:
    claim_id = _create_claim(client)
    _upload(client, claim_id, "claim-form.pdf")
    client.post(f"/claims/{claim_id}/extract")
    data = _wait_for_extraction(client, claim_id)
    extraction = data["extraction"]
    extraction["policy_number"]["value"] = "POL-200002"
    # The date value change makes it an adjuster override (confidence
    # 1.0), which clears the exclusion bar; the date stays outside
    # POL-200002's 2024 window, so the denial stands.
    extraction["date_of_loss"]["value"] = "2026-11-15"
    assert client.patch(f"/claims/{claim_id}", json=extraction).status_code == 200
    response = client.post(f"/claims/{claim_id}/triage")
    assert response.status_code == 200
    assert response.json()["triage"]["route"] == "likely_not_covered"


def test_triage_expired_policy_below_bar_routes_to_review(client) -> None:
    claim_id = _create_claim(client)
    _upload(client, claim_id, "claim-form.pdf")
    client.post(f"/claims/{claim_id}/extract")
    data = _wait_for_extraction(client, claim_id)
    extraction = data["extraction"]
    extraction["policy_number"]["value"] = "POL-200002"
    assert client.patch(f"/claims/{claim_id}", json=extraction).status_code == 200
    response = client.post(f"/claims/{claim_id}/triage")
    assert response.status_code == 200
    triage = response.json()["triage"]
    # The mock date carries confidence 0.85, below the 0.9 exclusion
    # bar, so the inactivity denial is demoted to a review instead.
    assert triage["route"] == "needs_more_info"
    assert any("not applied" in reason for reason in triage["reasons"])
