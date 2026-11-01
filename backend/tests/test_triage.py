from datetime import UTC, date, datetime

from claims_copilot.agents.triage import run_triage
from claims_copilot.providers.base import InputFile
from claims_copilot.providers.mock import MockProvider
from claims_copilot.schemas import (
    ConsistencyResult,
    DocumentType,
    ExtractedClaim,
    FieldProvenance,
    LossType,
    TriageFile,
    TriageRoute,
)
from claims_copilot.services.policies import default_policies_path, load_policies


def _claim(**overrides) -> ExtractedClaim:
    base = ExtractedClaim(
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
            value=4500.00, confidence=0.9, source_file="est.pdf"
        ),
        vehicle_or_property_details=FieldProvenance(
            value="2019 sedan", confidence=0.9, source_file="photo.jpg"
        ),
    )
    return base.model_copy(update=overrides)


def _inputs(*names_and_types: tuple[str, str]) -> list[InputFile]:
    return [
        InputFile(filename=name, content_type=ctype, data=b"") for name, ctype in names_and_types
    ]


def _meta(*names_and_types: tuple[str, str, DocumentType]) -> list[TriageFile]:
    return [
        TriageFile(filename=name, content_type=ctype, document_type=dtype)
        for name, ctype, dtype in names_and_types
    ]


def _auto_inputs() -> list[InputFile]:
    return _inputs(
        ("claim-form.pdf", "application/pdf"),
        ("repair-estimate.pdf", "application/pdf"),
        ("damage-photo.jpg", "image/jpeg"),
    )


def _auto_meta() -> list[TriageFile]:
    return _meta(
        ("claim-form.pdf", "application/pdf", DocumentType.claim_form),
        ("repair-estimate.pdf", "application/pdf", DocumentType.repair_estimate),
        ("damage-photo.jpg", "image/jpeg", DocumentType.damage_photo),
    )


def _form_only_meta() -> list[TriageFile]:
    return _meta(("claim-form.pdf", "application/pdf", DocumentType.claim_form))


def _policies():
    return load_policies(default_policies_path())


class ContradictingProvider(MockProvider):
    async def check_consistency(
        self, description: str, claim: ExtractedClaim, files: list[InputFile]
    ) -> ConsistencyResult:
        return ConsistencyResult(
            consistent=False,
            notes="Description says theft but photos show collision damage.",
        )


class CountingProvider(MockProvider):
    def __init__(self) -> None:
        self.consistency_calls = 0

    async def check_consistency(
        self, description: str, claim: ExtractedClaim, files: list[InputFile]
    ) -> ConsistencyResult:
        self.consistency_calls += 1
        return await super().check_consistency(description, claim, files)


async def test_happy_path_is_ready_for_adjuster() -> None:
    result = await run_triage(
        _claim(), _auto_meta(), _auto_inputs(), MockProvider(), _policies(), "v1"
    )
    assert result.route == TriageRoute.ready_for_adjuster
    assert result.reasons == ["All required checks passed."]
    assert all(check.passed for check in result.checks)


async def test_missing_police_report_needs_more_info() -> None:
    claim = _claim(
        loss_type=FieldProvenance(value=LossType.theft, confidence=0.9, source_file="form.pdf")
    )
    result = await run_triage(
        claim,
        _form_only_meta(),
        _inputs(("claim-form.pdf", "application/pdf")),
        MockProvider(),
        _policies(),
        "v1",
    )
    assert result.route == TriageRoute.needs_more_info
    assert any("police report" in reason for reason in result.reasons)


async def test_inactive_policy_is_likely_not_covered() -> None:
    claim = _claim(
        policy_number=FieldProvenance(value="POL-200002", confidence=0.9, source_file="form.pdf")
    )
    result = await run_triage(
        claim,
        _form_only_meta(),
        _inputs(("claim-form.pdf", "application/pdf")),
        MockProvider(),
        _policies(),
        "v1",
    )
    assert result.route == TriageRoute.likely_not_covered
    assert any("not active" in reason for reason in result.reasons)


async def test_excluded_loss_type_is_likely_not_covered() -> None:
    claim = _claim(
        policy_number=FieldProvenance(value="POL-200002", confidence=0.9, source_file="form.pdf"),
        date_of_loss=FieldProvenance(
            value=date(2024, 6, 1), confidence=0.9, source_file="form.pdf"
        ),
        loss_type=FieldProvenance(
            value=LossType.water_damage, confidence=0.9, source_file="form.pdf"
        ),
    )
    result = await run_triage(
        claim,
        _form_only_meta(),
        _inputs(("claim-form.pdf", "application/pdf")),
        MockProvider(),
        _policies(),
        "v1",
    )
    assert result.route == TriageRoute.likely_not_covered
    assert any("excluded" in reason for reason in result.reasons)


async def test_damage_below_deductible_is_likely_not_covered() -> None:
    claim = _claim(
        estimated_damage_amount=FieldProvenance(value=100.00, confidence=0.9, source_file="est.pdf")
    )
    result = await run_triage(
        claim, _auto_meta(), _auto_inputs(), MockProvider(), _policies(), "v1"
    )
    assert result.route == TriageRoute.likely_not_covered
    assert any("deductible" in reason for reason in result.reasons)


async def test_damage_exactly_at_deductible_is_likely_not_covered() -> None:
    claim = _claim(
        estimated_damage_amount=FieldProvenance(value=500.00, confidence=0.9, source_file="est.pdf")
    )
    result = await run_triage(
        claim, _auto_meta(), _auto_inputs(), MockProvider(), _policies(), "v1"
    )
    assert result.route == TriageRoute.likely_not_covered
    assert any("deductible" in reason for reason in result.reasons)


async def test_damage_just_above_deductible_proceeds() -> None:
    claim = _claim(
        estimated_damage_amount=FieldProvenance(value=501.00, confidence=0.9, source_file="est.pdf")
    )
    result = await run_triage(
        claim, _auto_meta(), _auto_inputs(), MockProvider(), _policies(), "v1"
    )
    assert result.route == TriageRoute.ready_for_adjuster
    assert not any("deductible" in reason for reason in result.reasons)


async def test_damage_above_limit_stays_ready_with_cap_note() -> None:
    claim = _claim(
        estimated_damage_amount=FieldProvenance(
            value=60000.00, confidence=0.9, source_file="est.pdf"
        )
    )
    result = await run_triage(
        claim, _auto_meta(), _auto_inputs(), MockProvider(), _policies(), "v1"
    )
    assert result.route == TriageRoute.ready_for_adjuster
    assert any("capped" in reason for reason in result.reasons)


async def test_low_confidence_field_needs_more_info() -> None:
    claim = _claim(
        loss_type=FieldProvenance(value=LossType.auto, confidence=0.5, source_file="photo.jpg")
    )
    result = await run_triage(
        claim, _auto_meta(), _auto_inputs(), MockProvider(), _policies(), "v1"
    )
    assert result.route == TriageRoute.needs_more_info
    assert any("low confidence" in reason for reason in result.reasons)


async def test_reasons_list_hard_then_soft_issues() -> None:
    claim = _claim(
        estimated_damage_amount=FieldProvenance(
            value=100.00, confidence=0.9, source_file="est.pdf"
        ),
        loss_type=FieldProvenance(value=LossType.auto, confidence=0.5, source_file="photo.jpg"),
    )
    result = await run_triage(
        claim, _auto_meta(), _auto_inputs(), MockProvider(), _policies(), "v1"
    )
    assert result.route == TriageRoute.likely_not_covered
    assert len(result.reasons) == 2
    assert "deductible" in result.reasons[0]
    assert "low confidence" in result.reasons[1]


async def test_reasons_list_soft_issues_then_limit_note() -> None:
    claim = _claim(
        estimated_damage_amount=FieldProvenance(
            value=60000.00, confidence=0.9, source_file="est.pdf"
        ),
        loss_type=FieldProvenance(value=LossType.auto, confidence=0.5, source_file="photo.jpg"),
    )
    result = await run_triage(
        claim, _auto_meta(), _auto_inputs(), MockProvider(), _policies(), "v1"
    )
    assert result.route == TriageRoute.needs_more_info
    assert len(result.reasons) == 2
    assert "low confidence" in result.reasons[0]
    assert "capped" in result.reasons[1]


async def test_missing_field_value_needs_more_info() -> None:
    claim = _claim(incident_location=FieldProvenance(value=None, confidence=0.0, source_file=None))
    result = await run_triage(
        claim, _auto_meta(), _auto_inputs(), MockProvider(), _policies(), "v1"
    )
    assert result.route == TriageRoute.needs_more_info
    assert any("Incident location" in reason for reason in result.reasons)


async def test_unknown_policy_needs_more_info() -> None:
    claim = _claim(
        policy_number=FieldProvenance(value="POL-999999", confidence=0.9, source_file="form.pdf")
    )
    result = await run_triage(
        claim,
        _form_only_meta(),
        _inputs(("claim-form.pdf", "application/pdf")),
        MockProvider(),
        _policies(),
        "v1",
    )
    assert result.route == TriageRoute.needs_more_info
    assert any("not found" in reason for reason in result.reasons)


async def test_consistency_contradiction_needs_more_info() -> None:
    result = await run_triage(
        _claim(), _auto_meta(), _auto_inputs(), ContradictingProvider(), _policies(), "v1"
    )
    assert result.route == TriageRoute.needs_more_info
    assert result.consistency is not None
    assert result.consistency.consistent is False
    assert any("Consistency" in reason for reason in result.reasons)


class MinorContradictionProvider(MockProvider):
    async def check_consistency(
        self, description: str, claim: ExtractedClaim, files: list[InputFile]
    ) -> ConsistencyResult:
        return ConsistencyResult(
            consistent=False,
            severity="minor",
            notes="Form says sedan; estimate says car.",
        )


class MajorContradictionProvider(MockProvider):
    async def check_consistency(
        self, description: str, claim: ExtractedClaim, files: list[InputFile]
    ) -> ConsistencyResult:
        return ConsistencyResult(
            consistent=False,
            severity="major",
            notes="Form states $12,561; estimate states $15,569.",
        )


async def test_minor_contradiction_stays_ready() -> None:
    result = await run_triage(
        _claim(), _auto_meta(), _auto_inputs(), MinorContradictionProvider(), _policies(), "v1"
    )
    assert result.route == TriageRoute.ready_for_adjuster
    assert result.consistency is not None
    assert result.consistency.severity == "minor"
    assert not any("Consistency" in reason for reason in result.reasons)


async def test_major_contradiction_needs_more_info() -> None:
    result = await run_triage(
        _claim(), _auto_meta(), _auto_inputs(), MajorContradictionProvider(), _policies(), "v1"
    )
    assert result.route == TriageRoute.needs_more_info
    assert result.consistency is not None
    assert result.consistency.severity == "major"
    assert any("Consistency" in reason for reason in result.reasons)


async def test_consistency_skipped_when_policy_unknown() -> None:
    provider = CountingProvider()
    claim = _claim(
        policy_number=FieldProvenance(value="POL-999999", confidence=0.9, source_file="form.pdf")
    )
    result = await run_triage(
        claim,
        _form_only_meta(),
        _inputs(("claim-form.pdf", "application/pdf")),
        provider,
        _policies(),
        "v1",
    )
    assert provider.consistency_calls == 0
    assert result.consistency is None
    assert result.route == TriageRoute.needs_more_info
    assert any("skipped" in reason for reason in result.reasons)
    assert any(check.name.startswith("coverage:") for check in result.checks)


async def test_consistency_skipped_when_policy_inactive_but_documents_still_checked() -> None:
    provider = CountingProvider()
    claim = _claim(
        policy_number=FieldProvenance(value="POL-200002", confidence=0.9, source_file="form.pdf")
    )
    result = await run_triage(
        claim,
        _form_only_meta(),
        _inputs(("claim-form.pdf", "application/pdf")),
        provider,
        _policies(),
        "v1",
    )
    assert provider.consistency_calls == 0
    assert result.consistency is None
    assert result.route == TriageRoute.likely_not_covered
    assert any("skipped" in reason for reason in result.reasons)
    assert any(check.name.startswith("documents:") for check in result.checks)


async def test_consistency_runs_when_policy_active() -> None:
    provider = CountingProvider()
    result = await run_triage(_claim(), _auto_meta(), _auto_inputs(), provider, _policies(), "v1")
    assert provider.consistency_calls == 1
    assert result.consistency is not None
    assert not any("skipped" in reason for reason in result.reasons)


async def test_consistency_skipped_without_description() -> None:
    claim = _claim(
        incident_description=FieldProvenance(value=None, confidence=0.0, source_file=None)
    )
    result = await run_triage(
        claim, _auto_meta(), _auto_inputs(), MockProvider(), _policies(), "v1"
    )
    assert result.consistency is None
    assert result.route == TriageRoute.needs_more_info


async def test_triage_result_carries_timestamp_and_policy_version() -> None:
    before = datetime.now(UTC)
    result = await run_triage(
        _claim(), _auto_meta(), _auto_inputs(), MockProvider(), _policies(), "abc123"
    )
    assert result.policy_version == "abc123"
    assert result.triaged_at is not None
    assert before <= result.triaged_at <= datetime.now(UTC)
