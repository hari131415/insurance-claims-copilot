"""Refresh saved eval records with a live consistency call only.

Reuses each claim's saved extraction and classifications, calls the
provider's consistency check with the current prompt, and recomputes
the route through the deterministic triage nodes. Claims where the
graph skips consistency (unknown/inactive policy, empty description)
need no call: their route is recomputed from the saved extraction
alone. No extraction call is made, so this is the cheap half of a
re-run: it verifies a consistency-prompt change without paying for
extraction again.

Run from the repo root with the backend venv:
    backend/.venv/bin/python evals/refresh.py --saved evals/results/anthropic_raw.json \
        --ids dev_ids.txt --out evals/results/anthropic_dev_refresh.json --provider anthropic

--budget USD stops before a call whose projected cost would exceed
the cap, mirroring evals/run.py, and records where it stopped.
Real providers read their keys from the environment.
"""

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

from claims_copilot.agents import triage as triage_module  # noqa: E402
from claims_copilot.config import Settings  # noqa: E402
from claims_copilot.providers import get_provider  # noqa: E402
from claims_copilot.providers.base import BaseProvider, InputFile  # noqa: E402
from claims_copilot.services.policies import (  # noqa: E402
    default_policies_path,
    get_policy,
    load_policies,
)


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_raw(raw_path: Path, payload: dict) -> None:
    tmp_path = raw_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp_path, raw_path)


def _payload(
    args: argparse.Namespace,
    ids: list[str],
    records: dict[str, dict],
    stopped: dict | None,
    total_spent: float,
) -> dict:
    return {
        "provider": args.provider,
        "model": args.model,
        "dataset_seed": args.seed,
        "budget_usd": args.budget,
        "stopped": stopped,
        "actual_cost_usd": round(total_spent, 4),
        "claims": [records[cid] for cid in ids if cid in records],
    }


async def _run(
    args: argparse.Namespace,
    replay: Any,
    run: Any,
    provider: BaseProvider,
    policies: list,
    saved_by_id: dict[str, dict],
    label_by_id: dict[str, dict],
    ids: list[str],
    files_dir: Path,
) -> tuple[dict | None, int, float]:
    records: dict[str, dict] = {}
    stopped: dict | None = None
    calls = 0
    total_spent = 0.0
    for index, claim_id in enumerate(ids, start=1):
        saved = saved_by_id[claim_id]
        label_claim = label_by_id[claim_id]
        claim, files = replay.build_claim_and_files(saved, label_claim)
        policy = get_policy(policies, claim.policy_number.value)
        description = claim.incident_description.value
        skipped = (
            triage_module._consistency_skipped(
                {"claim": claim, "policy": policy}  # type: ignore[typeddict-item]
            )
            or not description
        )
        if skipped:
            route, reasons = replay.triage_with_consistency(claim, files, policies, None)
            record = {
                **saved,
                "route_predicted": route,
                "reasons_predicted": reasons,
                "route_correct": route == saved["route_expected"],
                "cost_usd": 0.0,
                "consistency_severity": None,
                "consistency_notes": None,
                "consistency_skipped": True,
            }
            cost = 0.0
        else:
            projected = total_spent / calls if calls else args.estimate_per_call
            if run._over_budget(total_spent, projected, args.budget):
                stopped = {
                    "reason": "budget",
                    "claims_recorded": len(records),
                    "calls": calls,
                    "total_spent_usd": round(total_spent, 4),
                    "next_claim_id": claim_id,
                }
                print(f"stopped: budget cap before {claim_id} (total ${total_spent:.4f})")
                break
            input_files = [
                InputFile(
                    filename=item["filename"],
                    content_type=run._content_type(item["filename"]),
                    data=(files_dir / item["filename"]).read_bytes(),
                )
                for item in label_claim["files"]
            ]
            result = await provider.check_consistency(description, claim, input_files)
            route, reasons = replay.triage_with_consistency(claim, files, policies, result)
            cost = result.usage.cost_usd if result.usage is not None else 0.0
            record = {
                **saved,
                "route_predicted": route,
                "reasons_predicted": reasons,
                "route_correct": route == saved["route_expected"],
                "cost_usd": cost,
                "consistency_severity": result.severity,
                "consistency_notes": result.notes,
                "consistency_skipped": False,
            }
            calls += 1
            total_spent += cost
        records[claim_id] = record
        print(
            f"[{index}/{len(ids)}] {claim_id}: route={record['route_predicted']} "
            f"severity={record['consistency_severity']} cost=${record['cost_usd']:.4f}"
        )
        _write_raw(args.out, _payload(args, ids, records, stopped, total_spent))
        if args.budget is not None and total_spent > args.budget:
            stopped = {
                "reason": "budget_exceeded",
                "claims_recorded": len(records),
                "calls": calls,
                "total_spent_usd": round(total_spent, 4),
                "next_claim_id": ids[index] if index < len(ids) else None,
            }
            print(f"stopped: budget exceeded after {claim_id} (total ${total_spent:.4f})")
            _write_raw(args.out, _payload(args, ids, records, stopped, total_spent))
            break
    return stopped, calls, total_spent


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh saved records with live consistency calls."
    )
    parser.add_argument("--saved", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=Path("evals/data"))
    parser.add_argument("--ids", type=Path, required=True, help="one claim id per line")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--provider", default="anthropic")
    parser.add_argument("--budget", type=float, default=None)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    replay = _load_module("eval_replay", repo_root / "evals" / "replay.py")
    run = _load_module("eval_run", repo_root / "evals" / "run.py")

    labels = json.loads((args.data / "labels.json").read_text())
    saved_raw = json.loads(args.saved.read_text())
    saved_by_id = {record["claim_id"]: record for record in saved_raw["claims"]}
    label_by_id = {claim["claim_id"]: claim for claim in labels["claims"]}
    ids = [line.strip() for line in args.ids.read_text().splitlines() if line.strip()]
    missing = [cid for cid in ids if cid not in saved_by_id or cid not in label_by_id]
    if missing:
        parser.error(f"ids missing from saved/labels: {missing[:5]}")

    settings = Settings(llm_provider=args.provider)
    provider = get_provider(settings)
    model = run._resolve_model(args.provider, settings)
    args.model = model
    args.seed = labels["seed"]
    policies = load_policies(default_policies_path())

    # Estimate first, before any API call: file tokens for the claims
    # that will need a call (skip pattern from the saved runs), plus
    # the new prompt's allowances, scaled by the full run's
    # actual/estimate ratio. The estimate is printed, not charged.
    call_claims = []
    for cid in ids:
        claim, files = replay.build_claim_and_files(saved_by_id[cid], label_by_id[cid])
        policy = get_policy(policies, claim.policy_number.value)
        if (
            triage_module._consistency_skipped(
                {"claim": claim, "policy": policy}  # type: ignore[typeddict-item]
            )
            or not claim.incident_description.value
        ):
            continue
        call_claims.append(label_by_id[cid])
    estimate = run.estimate_cost({"claims": call_claims}, args.data / "files", args.provider, model)
    prompt_in, prompt_out = 600, 400
    basis = (
        estimate["file_tokens"] + prompt_in * len(call_claims)
    ) * 2 / 1_000_000 + prompt_out * len(call_claims) * 10 / 1_000_000
    args.estimate_per_call = basis / len(call_claims) if call_claims else 0.0
    print(
        f"estimate for {len(call_claims)} consistency calls "
        f"({len(ids) - len(call_claims)} skipped, no call): "
        f"basis ${basis:.4f}, actual-scale x1.637 = ${basis * 1.637:.4f}"
    )

    stopped, calls, total_spent = asyncio.run(
        _run(
            args,
            replay,
            run,
            provider,
            policies,
            saved_by_id,
            label_by_id,
            ids,
            args.data / "files",
        )
    )
    print(f"done: {calls} calls, ${total_spent:.4f} spent, stopped={stopped}")


if __name__ == "__main__":
    main()
