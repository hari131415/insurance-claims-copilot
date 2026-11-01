"""Tests for the eval runner's budget cap, resume, and scan strength.

The runner lives in evals/, outside the backend package, so it is
loaded by path. Runs use the mock provider (no API calls) against the
real dataset, which keeps them fast.
"""

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_eval_module(name: str, filename: str):
    path = REPO_ROOT / "evals" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = _load_eval_module("eval_run", "run.py")
report = _load_eval_module("eval_report", "report.py")
DATA_DIR = REPO_ROOT / "evals" / "data"


def _raw(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "mock_raw.json").read_text())


def test_over_budget() -> None:
    assert run._over_budget(0.0, 0.02, 8.75) is False
    assert run._over_budget(8.74, 0.02, 8.75) is True
    assert run._over_budget(8.75, 0.0, 8.75) is False
    assert run._over_budget(100.0, 100.0, None) is False


def test_resume_skips_completed(tmp_path: Path) -> None:
    run.main(
        ["--provider", "mock", "--data", str(DATA_DIR), "--out", str(tmp_path), "--limit", "3"]
    )
    first = _raw(tmp_path)
    assert len(first["claims"]) == 3
    assert all(claim["status"] == "ok" for claim in first["claims"])

    run.main(
        [
            "--provider",
            "mock",
            "--data",
            str(DATA_DIR),
            "--out",
            str(tmp_path),
            "--resume",
            "--limit",
            "2",
        ]
    )
    resumed = _raw(tmp_path)
    assert resumed["skipped_completed"] == 3
    assert len(resumed["claims"]) == 5
    labels = json.loads((DATA_DIR / "labels.json").read_text())
    expected_ids = [claim["claim_id"] for claim in labels["claims"][:5]]
    assert [claim["claim_id"] for claim in resumed["claims"]] == expected_ids


def test_budget_stops_resume_when_already_over(tmp_path: Path) -> None:
    run.main(
        ["--provider", "mock", "--data", str(DATA_DIR), "--out", str(tmp_path), "--limit", "2"]
    )
    raw = _raw(tmp_path)
    assert len(raw["claims"]) == 2
    # The mock costs nothing, so simulate prior spend by hand: the
    # budget check must count cumulative spend, not per-invocation.
    for claim in raw["claims"]:
        claim["cost_usd"] = 5.0
    (tmp_path / "mock_raw.json").write_text(json.dumps(raw))

    run.main(
        [
            "--provider",
            "mock",
            "--data",
            str(DATA_DIR),
            "--out",
            str(tmp_path),
            "--resume",
            "--budget",
            "5.0",
        ]
    )
    stopped = _raw(tmp_path)
    assert len(stopped["claims"]) == 2
    assert stopped["stopped"]["reason"] == "budget"
    labels = json.loads((DATA_DIR / "labels.json").read_text())
    assert stopped["stopped"]["next_claim_id"] == labels["claims"][2]["claim_id"]


def test_scan_strength_recorded(tmp_path: Path) -> None:
    run.main(
        ["--provider", "mock", "--data", str(DATA_DIR), "--out", str(tmp_path), "--limit", "5"]
    )
    for claim in _raw(tmp_path)["claims"]:
        assert "scan_strength" in claim
        assert claim["scan_strength"] in (None, "light", "medium", "heavy")


def _synthetic_claim(claim_id: str, loss_type: str, scanned: bool, strength: str | None) -> dict:
    return {
        "claim_id": claim_id,
        "loss_type": loss_type,
        "messy": False,
        "scanned": scanned,
        "scan_strength": strength,
        "status": "ok",
        "error": None,
        "fields": {
            key: {"expected": "x", "predicted": "x", "confidence": 0.9, "correct": True}
            for key in report.FIELD_KEYS
        },
        "classifications": [],
        "route_expected": "needs_more_info",
        "route_predicted": "needs_more_info",
        "route_correct": True,
        "reasons_predicted": [],
        "conflict_detected": None,
        "mismatch_detected": None,
        "latency_total_s": 1.0,
        "latency_extract_s": 0.5,
        "latency_triage_s": 0.5,
        "cost_usd": 0.0,
    }


def test_report_breakdowns_by_loss_type_and_scan_strength() -> None:
    raw = {
        "provider": "mock",
        "model": None,
        "dataset_seed": 42,
        "dataset_count": 4,
        "claims": [
            _synthetic_claim("claim-0001", "auto", True, "light"),
            _synthetic_claim("claim-0002", "auto", False, None),
            _synthetic_claim("claim-0003", "theft", True, "heavy"),
            _synthetic_claim("claim-0004", "water damage", True, "medium"),
        ],
    }
    metrics = report.compute_metrics(raw)
    assert metrics["by_loss_type"]["auto"]["claims"] == 2
    assert metrics["by_loss_type"]["theft"]["claims"] == 1
    assert metrics["by_loss_type"]["water damage"]["claims"] == 1
    assert metrics["by_scan_strength"]["light"]["claims"] == 1
    assert metrics["by_scan_strength"]["heavy"]["claims"] == 1
    assert metrics["by_scan_strength"]["medium"]["claims"] == 1
    assert metrics["by_scan_strength"]["text"]["claims"] == 1
    rendered = report.render_report(metrics, None)
    assert "## Breakdown by loss type" in rendered
    assert "## Breakdown by scan strength" in rendered
