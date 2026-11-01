"""Run extraction and triage over the eval dataset for one provider.

Drives the real API through a TestClient (temp SQLite database, temp
upload directory), so the run exercises the same code path as the app:
upload validation, storage, extraction, and the triage graph. Each
claim is recorded independently; a failure is saved as a failed record
and the run continues.

Before any API call, the runner prints a cost estimate derived from the
dataset (PDF text length, image sizes, pricing.yaml). Image tokens use
the documented OpenAI high-detail formula, 85 + 170 per 512px tile:
scanned pages are charged at the app's scale-2 letter render
(1224x1584, 3x4 tiles, 2125 tokens) and photos at their actual pixel
size. Gemini and Anthropic count images differently, so the estimate
is provider-approximate for them, but the same order of magnitude.
--limit N runs only the first N claims and reports the actual average
cost per claim alongside the projected full-run cost.

--budget USD stops the run before starting a claim whose projected
cost would push total spend over the cap, and stops immediately after
any single claim overshoots it. The stop point is printed and recorded
in the raw file, so the run can be resumed later.

--resume loads an existing raw file for the provider, skips claims
already completed (status ok), and continues with the rest. Failed
claims are retried. The budget counts cumulative spend across the
original run and the resume, not per invocation.

Reported costs cover extraction plus the triage consistency call, both
as reported by the API. The estimate likewise counts both calls: the
consistency call re-sends the same files, so its input is the file
tokens again plus a small prompt allowance.

Run from the repo root with the backend venv:
    backend/.venv/bin/python evals/run.py --provider mock
    backend/.venv/bin/python evals/run.py --provider azure_openai --limit 10
    backend/.venv/bin/python evals/run.py --provider openai --estimate-only

Real providers read their keys from the environment (.env is not loaded
by this script; export the keys first).
"""

import argparse
import json
import math
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

# The Gemini and Anthropic SDKs build httpx clients that misparse
# bracketed IPv6 entries (e.g. [::1]) in no_proxy. Localhost requests
# must bypass the proxy anyway, so pin the working value before any
# provider client is constructed. See decisions.md.
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

from unittest.mock import patch  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from pypdf import PdfReader  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import claims_copilot.api.claims as claims_api  # noqa: E402
from claims_copilot.config import Settings, load_pricing  # noqa: E402
from claims_copilot.db import get_session  # noqa: E402
from claims_copilot.main import create_app  # noqa: E402

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
# OpenAI high-detail image pricing: 85 base + 170 per 512px tile.
# Scanned pages are rendered by the app at scale 2 on letter
# (1224x1584, 3x4 tiles), hence the fixed 2125 below.
SCANNED_PAGE_TOKENS = 85 + 170 * 3 * 4
EXTRACTION_OUTPUT_TOKENS = 600
CONSISTENCY_INPUT_EXTRA_TOKENS = 300
CONSISTENCY_OUTPUT_TOKENS = 200


def _image_tokens(width: int, height: int) -> int:
    return 85 + 170 * math.ceil(width / 512) * math.ceil(height / 512)


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value).lower()).split())


def _field_correct(
    key: str, expected: object, predicted: object, accepted: list | None = None
) -> bool:
    if expected is None:
        return predicted is None
    if predicted is None:
        return False
    # Conflict claims accept either document's value.
    candidates = accepted if accepted else [expected]
    if key == "estimated_damage_amount":
        if not isinstance(predicted, (int, float)):
            return False
        return any(
            isinstance(candidate, (int, float)) and abs(float(candidate) - float(predicted)) <= 0.01
            for candidate in candidates
        )
    if key in ("policy_number", "date_of_loss", "loss_type"):
        return any(str(candidate) == str(predicted) for candidate in candidates)
    return any(_normalize_text(candidate) == _normalize_text(predicted) for candidate in candidates)


def _resolve_model(provider: str, settings: Settings) -> str | None:
    if provider == "mock":
        return None
    if provider == "azure_openai":
        if settings.azure_openai_deployment:
            return settings.azure_openai_deployment
        priced = load_pricing().get("azure_openai", {})
        return next(iter(priced), None)
    if provider == "gemini":
        return settings.gemini_model
    if provider == "anthropic":
        return settings.anthropic_model
    if provider == "openai":
        return settings.openai_model
    return None


def estimate_cost(labels: dict, files_dir: Path, provider: str, model: str | None) -> dict:
    if provider == "mock" or model is None:
        return {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    pricing = load_pricing().get(provider, {}).get(model)
    if pricing is None:
        raise ValueError(f"no pricing for {provider}/{model}; re-check pricing.yaml")
    file_tokens = 0
    claims = labels["claims"]
    for claim in claims:
        for entry in claim["files"]:
            path = files_dir / entry["filename"]
            if entry["pages"] is not None and not entry["scanned"]:
                text = "".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
                file_tokens += max(1, len(text) // 4)
            elif entry["pages"] is not None:
                file_tokens += entry["pages"] * SCANNED_PAGE_TOKENS
            else:
                with Image.open(path) as photo:
                    file_tokens += _image_tokens(photo.width, photo.height)
    # Extraction sends the files once; the consistency call re-sends
    # them with a short prompt and returns a short verdict.
    input_tokens = 2 * file_tokens + CONSISTENCY_INPUT_EXTRA_TOKENS * len(claims)
    output_tokens = (EXTRACTION_OUTPUT_TOKENS + CONSISTENCY_OUTPUT_TOKENS) * len(claims)
    cost = (
        input_tokens * pricing["input_per_million"] / 1_000_000
        + output_tokens * pricing["output_per_million"] / 1_000_000
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost,
        "file_tokens": file_tokens,
    }


SCAN_STRENGTH_ORDER = {"light": 0, "medium": 1, "heavy": 2}


def _claim_scan_strength(claim: dict) -> str | None:
    strengths: list[str] = [
        entry["scan_strength"]
        for entry in claim["files"]
        if entry.get("scan_strength") in SCAN_STRENGTH_ORDER
    ]
    if not strengths:
        return None
    return max(strengths, key=lambda strength: SCAN_STRENGTH_ORDER[strength])


def _over_budget(total_spent: float, projected_next: float, budget: float | None) -> bool:
    return budget is not None and total_spent + projected_next > budget


def _content_type(filename: str) -> str:
    return "application/pdf" if filename.endswith(".pdf") else "image/jpeg"


def _run_claim(client: TestClient, claim: dict, files_dir: Path, timeout: int) -> dict:
    started = time.monotonic()
    record: dict = {
        "claim_id": claim["claim_id"],
        "loss_type": claim["loss_type"],
        "messy": claim["messy"],
        "scanned": any(f["scanned"] for f in claim["files"]),
        "scan_strength": _claim_scan_strength(claim),
        "status": "failed",
        "error": None,
        "fields": {},
        "classifications": [],
        "route_expected": claim["expected_route"],
        "route_predicted": None,
        "route_correct": False,
        "reasons_predicted": [],
        "conflict_detected": None,
        "mismatch_detected": None,
        "latency_total_s": 0.0,
        "latency_extract_s": 0.0,
        "latency_triage_s": 0.0,
        "cost_usd": 0.0,
    }
    try:
        response = client.post("/claims")
        response.raise_for_status()
        claim_id = response.json()["id"]

        uploads = [
            (
                "files",
                (
                    entry["filename"],
                    (files_dir / entry["filename"]).read_bytes(),
                    _content_type(entry["filename"]),
                ),
            )
            for entry in claim["files"]
        ]
        response = client.post(f"/claims/{claim_id}/files", files=uploads)
        response.raise_for_status()

        extract_started = time.monotonic()
        response = client.post(f"/claims/{claim_id}/extract", timeout=600)
        response.raise_for_status()
        deadline = time.monotonic() + timeout
        while True:
            state = client.get(f"/claims/{claim_id}").json()
            if state["extraction_status"] in ("done", "failed"):
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"extraction did not finish in {timeout}s")
            time.sleep(1)
        record["latency_extract_s"] = round(time.monotonic() - extract_started, 3)
        if state["extraction_status"] == "failed":
            record["error"] = state.get("extraction_error") or "extraction failed"
            return record
        extraction = state["extraction"] or {}
        predicted_by_file = {
            item["filename"]: item["document_type"] for item in state.get("classifications", [])
        }
        accepted_values = claim.get("accepted_values", {})
        for key in FIELD_KEYS:
            predicted = extraction.get(key, {})
            value = predicted.get("value") if isinstance(predicted, dict) else None
            record["fields"][key] = {
                "expected": claim["fields"][key],
                "predicted": value,
                "confidence": predicted.get("confidence") if isinstance(predicted, dict) else None,
                "correct": _field_correct(
                    key, claim["fields"][key], value, accepted_values.get(key)
                ),
            }
        for entry in claim["files"]:
            predicted_type = predicted_by_file.get(entry["filename"])
            record["classifications"].append(
                {
                    "filename": entry["filename"],
                    "expected": entry["document_type"],
                    "predicted": predicted_type,
                    "correct": predicted_type == entry["document_type"],
                }
            )
        record["cost_usd"] = (state.get("usage") or {}).get("cost_usd", 0.0)

        triage_started = time.monotonic()
        response = client.post(f"/claims/{claim_id}/triage", timeout=600)
        response.raise_for_status()
        record["latency_triage_s"] = round(time.monotonic() - triage_started, 3)
        triage = client.get(f"/claims/{claim_id}").json().get("triage") or {}
        record["route_predicted"] = triage.get("route")
        record["reasons_predicted"] = triage.get("reasons", [])
        record["route_correct"] = record["route_predicted"] == claim["expected_route"]
        consistency_usage = (triage.get("consistency") or {}).get("usage") or {}
        record["cost_usd"] += consistency_usage.get("cost_usd", 0.0)
        flagged = any(
            "Consistency check flagged" in reason for reason in record["reasons_predicted"]
        )
        if accepted_values:
            record["conflict_detected"] = flagged
        if claim.get("photo_mismatch"):
            record["mismatch_detected"] = flagged
        record["status"] = "ok"
    except Exception as exc:  # per-claim failure must not stop the run
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        record["latency_total_s"] = round(time.monotonic() - started, 3)
    return record


def _write_raw(raw_path: Path, payload: dict) -> None:
    # Write through a temp file so a run killed mid-write never leaves
    # a torn raw file behind for --resume to choke on.
    tmp_path = raw_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2))
    tmp_path.replace(raw_path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the eval dataset for one provider.")
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--data", type=Path, default=Path("evals/data"))
    parser.add_argument("--out", type=Path, default=Path("evals/results"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--estimate-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help="stop before total spend would exceed this many USD",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip claims already completed in the existing raw file",
    )
    parser.add_argument(
        "--claim-ids",
        type=Path,
        default=None,
        help="file with one claim id per line; run only those claims",
    )
    args = parser.parse_args(argv)

    labels_path = args.data / "labels.json"
    if not labels_path.exists():
        parser.error(f"no dataset at {labels_path}; run evals/generate.py first")
    labels = json.loads(labels_path.read_text())
    claims = labels["claims"]
    if args.claim_ids is not None:
        wanted = [line.strip() for line in args.claim_ids.read_text().splitlines() if line.strip()]
        by_id = {claim["claim_id"]: claim for claim in claims}
        missing = [cid for cid in wanted if cid not in by_id]
        if missing:
            parser.error(f"claim ids not in dataset: {missing[:5]}")
        claims = [by_id[cid] for cid in wanted]

    args.out.mkdir(parents=True, exist_ok=True)
    raw_path = args.out / f"{args.provider}_raw.json"
    records: dict[str, dict] = {}
    skipped = 0
    if args.resume and raw_path.exists():
        try:
            previous = json.loads(raw_path.read_text())
        except json.JSONDecodeError as exc:
            parser.error(f"existing raw file {raw_path} is not valid JSON: {exc}")
        if previous.get("provider") != args.provider:
            parser.error(
                f"existing raw file is for provider {previous.get('provider')!r}, "
                f"not {args.provider!r}; refusing to mix providers"
            )
        for record in previous.get("claims", []):
            records[record["claim_id"]] = record
        skipped = sum(1 for record in records.values() if record["status"] == "ok")
        print(f"resumed: skipped {skipped} completed claims from {raw_path}")

    completed_ids = {claim_id for claim_id, record in records.items() if record["status"] == "ok"}
    remaining = [claim for claim in claims if claim["claim_id"] not in completed_ids]
    scoped = remaining[: args.limit] if args.limit else remaining

    tmp = Path(tempfile.mkdtemp(prefix="eval-run-"))
    settings = Settings(
        llm_provider=args.provider,
        upload_dir=tmp / "uploads",
        storage_backend="local",
    )
    model = _resolve_model(args.provider, settings)
    estimate = estimate_cost({"claims": scoped}, args.data / "files", args.provider, model)
    print(
        f"estimate for {len(scoped)} claims"
        + (f" (limit {args.limit} of {len(claims)})" if args.limit else "")
        + f": {estimate['input_tokens']} in / {estimate['output_tokens']} out tokens,"
        f" ${estimate['cost_usd']:.4f}"
    )
    if args.estimate_only:
        return

    engine = create_engine(f"sqlite:///{tmp}/eval.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def override_session():  # type: ignore[no-untyped-def]
        with Session(engine) as session:
            yield session

    app = create_app(init_db_on_startup=False)
    app.dependency_overrides[get_session] = override_session
    stopped: dict | None = None

    def payload() -> dict:
        ordered = [records[claim["claim_id"]] for claim in claims if claim["claim_id"] in records]
        actual_cost = sum(record["cost_usd"] for record in ordered)
        avg_cost = actual_cost / len(ordered) if ordered else 0.0
        return {
            "provider": args.provider,
            "model": model,
            "dataset_seed": labels["seed"],
            "dataset_count": len(claims),
            "limit": args.limit,
            "budget_usd": args.budget,
            "resumed": args.resume,
            "skipped_completed": skipped,
            "stopped": stopped,
            "generated_at": datetime.now(UTC).isoformat(),
            "estimate": estimate,
            "actual_cost_usd": actual_cost,
            "projected_full_cost_usd": avg_cost * len(claims),
            "claims": ordered,
        }

    # Same override the tests use, scoped to the run: the API module's
    # get_settings is an lru_cache wrapper, so direct assignment fails
    # type checking; patch.object replaces it for the duration instead.
    with (
        patch.object(claims_api, "get_settings", return_value=settings),
        TestClient(app) as client,
    ):
        for index, claim in enumerate(scoped, start=1):
            ok_records = [r for r in records.values() if r["status"] == "ok"]
            total_spent = sum(r["cost_usd"] for r in records.values())
            if ok_records:
                projected_next = sum(r["cost_usd"] for r in ok_records) / len(ok_records)
            elif scoped:
                projected_next = estimate["cost_usd"] / len(scoped)
            else:
                projected_next = 0.0
            if _over_budget(total_spent, projected_next, args.budget):
                stopped = {
                    "reason": "budget",
                    "claims_recorded": len(records),
                    "claims_ok": len(ok_records),
                    "total_spent_usd": round(total_spent, 4),
                    "next_claim_id": claim["claim_id"],
                }
                print(
                    f"stopped: budget cap before {claim['claim_id']} "
                    f"({len(ok_records)} ok, total ${total_spent:.4f})"
                )
                break
            record = _run_claim(client, claim, args.data / "files", args.timeout)
            records[claim["claim_id"]] = record
            total_spent = sum(r["cost_usd"] for r in records.values())
            print(
                f"[{index}/{len(scoped)}] {claim['claim_id']}: {record['status']}"
                + (
                    f" route={record['route_predicted']}"
                    if record["status"] == "ok"
                    else f" {record['error']}"
                )
            )
            _write_raw(raw_path, payload())
            if args.budget is not None and total_spent > args.budget:
                stopped = {
                    "reason": "budget_exceeded",
                    "claims_recorded": len(records),
                    "claims_ok": sum(1 for r in records.values() if r["status"] == "ok"),
                    "total_spent_usd": round(total_spent, 4),
                    "next_claim_id": (scoped[index]["claim_id"] if index < len(scoped) else None),
                }
                print(
                    f"stopped: budget exceeded after {claim['claim_id']} "
                    f"(total ${total_spent:.4f} over cap ${args.budget:.2f})"
                )
                break

    final = payload()
    ordered = final["claims"]
    ok = [record for record in ordered if record["status"] == "ok"]
    actual_cost = final["actual_cost_usd"]
    avg_cost = actual_cost / len(ordered) if ordered else 0.0
    print(
        f"done: {len(ok)}/{len(ordered)} ok, actual cost ${actual_cost:.4f}, "
        f"avg ${avg_cost:.6f}/claim"
        + (
            f", projected full run ${final['projected_full_cost_usd']:.4f}"
            if args.limit or stopped
            else ""
        )
    )
    if stopped is not None:
        final["stopped"] = stopped
    _write_raw(raw_path, final)
    print(f"raw results: {raw_path}")


if __name__ == "__main__":
    main()
