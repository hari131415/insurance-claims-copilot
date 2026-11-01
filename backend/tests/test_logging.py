from claims_copilot.logging import mask_pii


def test_masking_hides_email_policy_number_and_phone() -> None:
    masked = mask_pii(
        "call user@example.com about POL-100001 or 555-010-0000",
    )
    assert "user@example.com" not in masked
    assert "POL-100001" not in masked
    assert "555-010-0000" not in masked
    assert "[email]" in masked
    assert "[policy_number]" in masked
    assert "[phone]" in masked


def test_masking_leaves_claim_ids_and_field_names_alone() -> None:
    masked = mask_pii("claim 3f8a2b1c extraction_status failed on field policy_number")
    assert "3f8a2b1c" in masked
    assert "policy_number" in masked
