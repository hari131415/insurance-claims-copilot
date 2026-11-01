from claims_copilot.providers.base import InputFile
from claims_copilot.providers.mock import MockProvider
from claims_copilot.schemas import LossType


def _files(*names: str) -> list[InputFile]:
    return [InputFile(filename=name, content_type="application/pdf", data=b"") for name in names]


async def test_mock_returns_the_same_extraction_for_the_same_files() -> None:
    provider = MockProvider()
    files = _files("claim-form.pdf", "damage-photo.jpg")
    first = await provider.extract(files)
    second = await provider.extract(files)
    assert first == second


async def test_mock_infers_theft_from_a_police_report_filename() -> None:
    provider = MockProvider()
    result = await provider.extract(_files("claim-form.pdf", "police-report.pdf"))
    assert result.claim.loss_type.value == LossType.theft
    assert result.claim.loss_type.source_file == "police-report.pdf"


async def test_mock_defaults_to_property_without_a_signal_filename() -> None:
    provider = MockProvider()
    result = await provider.extract(_files("document.pdf"))
    assert result.claim.loss_type.value == LossType.property
    assert result.claim.loss_type.confidence == 0.5


async def test_mock_reports_zero_cost() -> None:
    provider = MockProvider()
    result = await provider.extract(_files("claim-form.pdf"))
    assert result.usage.cost_usd == 0.0
    assert result.provider == "mock"


async def test_mock_attributes_fields_to_the_first_uploaded_file() -> None:
    provider = MockProvider()
    result = await provider.extract(_files("claim-form.pdf", "damage-photo.jpg"))
    assert result.claim.policy_number.source_file == "claim-form.pdf"
    assert result.claim.claimant_name.source_file == "claim-form.pdf"
