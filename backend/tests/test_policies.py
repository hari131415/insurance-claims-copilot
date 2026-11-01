import hashlib
from pathlib import Path

from claims_copilot.schemas import DocumentType, LossType, TriageFile
from claims_copilot.services.policies import (
    DocumentRequirement,
    default_policies_path,
    document_satisfied,
    get_policy,
    hash_policies_file,
    load_policies,
    required_documents_for,
)


def test_sample_policies_load_with_expected_values() -> None:
    policies = load_policies(default_policies_path())
    assert len(policies) == 12
    policy = get_policy(policies, "POL-100001")
    assert policy is not None
    assert policy.deductible == 500.0
    assert policy.coverage_limit == 50000.0
    assert policy.excluded_loss_types == []
    deductibles = {policy.deductible for policy in policies}
    limits = {policy.coverage_limit for policy in policies}
    assert len(deductibles) > 1 and len(limits) > 1
    excluded = {loss_type for policy in policies for loss_type in policy.excluded_loss_types}
    assert excluded == {
        LossType.auto,
        LossType.property,
        LossType.theft,
        LossType.water_damage,
    }


def test_get_policy_returns_none_for_unknown_or_missing_number() -> None:
    policies = load_policies(default_policies_path())
    assert get_policy(policies, "POL-000000") is None
    assert get_policy(policies, None) is None


def test_required_documents_combine_default_and_loss_type() -> None:
    policies = load_policies(default_policies_path())
    policy = get_policy(policies, "POL-100001")
    assert policy is not None
    names = [req.name for req in required_documents_for(policy, LossType.theft)]
    assert names == ["claim form", "police report"]


def test_document_satisfied_by_classification_not_filename() -> None:
    requirement = DocumentRequirement(
        name="police report", document_type=DocumentType.police_report
    )
    files = [
        TriageFile(
            filename="scan001.pdf",
            content_type="application/pdf",
            document_type=DocumentType.police_report,
        )
    ]
    assert document_satisfied(requirement, files)


def test_document_not_satisfied_when_classification_differs() -> None:
    requirement = DocumentRequirement(
        name="police report", document_type=DocumentType.police_report
    )
    files = [
        TriageFile(
            filename="police-report.pdf",
            content_type="application/pdf",
            document_type=DocumentType.claim_form,
        )
    ]
    assert not document_satisfied(requirement, files)


def test_policies_file_hash_matches_content() -> None:
    path = default_policies_path()
    assert hash_policies_file(path) == hashlib.sha256(Path(path).read_bytes()).hexdigest()
