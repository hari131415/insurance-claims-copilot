import hashlib
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel

from claims_copilot.schemas import DocumentType, LossType, TriageFile


class DocumentRequirement(BaseModel):
    name: str
    document_type: DocumentType


class Policy(BaseModel):
    policy_number: str
    active_from: date
    active_to: date
    deductible: float
    coverage_limit: float
    excluded_loss_types: list[LossType] = []
    required_documents: dict[str, list[DocumentRequirement]] = {}


def default_policies_path() -> Path:
    return Path(__file__).parent.parent / "policies" / "sample_policies.yaml"


def load_policies(path: Path) -> list[Policy]:
    with path.open() as handle:
        data = yaml.safe_load(handle)
    return [Policy.model_validate(policy) for policy in data["policies"]]


def hash_policies_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_policy(policies: list[Policy], policy_number: str | None) -> Policy | None:
    if policy_number is None:
        return None
    for policy in policies:
        if policy.policy_number == policy_number:
            return policy
    return None


def required_documents_for(policy: Policy, loss_type: LossType | None) -> list[DocumentRequirement]:
    required = list(policy.required_documents.get("default", []))
    if loss_type is not None:
        required.extend(policy.required_documents.get(loss_type.value, []))
    return required


def document_satisfied(requirement: DocumentRequirement, files: list[TriageFile]) -> bool:
    return any(file.document_type == requirement.document_type for file in files)
