# Eval harness

Three scripts, run from the repo root with the backend venv
(`backend/.venv/bin/python`, which provides the installed
`claims_copilot` package and reportlab):

```sh
# 1. Build the dataset: 300 claims, real files, labels.json
backend/.venv/bin/python evals/generate.py

# 2. Run it for one provider (prints a cost estimate first)
backend/.venv/bin/python evals/run.py --provider mock
backend/.venv/bin/python evals/run.py --provider openai --limit 10
backend/.venv/bin/python evals/run.py --provider openai --estimate-only

# 3. Build the report from saved raw results (no API calls)
backend/.venv/bin/python evals/report.py --results evals/results/mock_raw.json
```

## Checks

Evals code passes the same bar as the backend. From the repo root:

```sh
backend/.venv/bin/ruff check --config backend/pyproject.toml evals
backend/.venv/bin/mypy --config-file backend/pyproject.toml evals
```

(The backend run is `mypy src`; evals need the config flag because they
live outside `backend/`.)

## Dataset (`evals/data/`, gitignored, reproducible)

`generate.py` renders claim-form PDFs, repair estimates, police
reports, and damage photos, and writes `labels.json` with the field
values the documents state, each file's document type, and the expected
triage route with reasons. Filenames are neutral (`doc_<hex>`) so
nothing can be inferred from a name. Same seed, same dataset:
labels and file bytes are identical across regenerations.

About 30% of claims are messy: rotated pages, low resolution, noise,
scanned image-only PDFs (no text layer), handwriting-style text
(slanted with per-character jitter; no script font is vendored),
missing fields, and conflicting values across documents. The expected
route mirrors the triage rules: hard failures (inactive policy,
excluded loss type, amount at or below the deductible) route to
likely_not_covered, soft issues (missing field, missing required
document, unknown policy, conflicting values) to needs_more_info.

## Runner (`evals/run.py`)

Drives the real API through a TestClient (temp database, temp upload
directory) and saves raw per-claim results to
`evals/results/<provider>_raw.json`, so the report regenerates without
re-calling any API. A failed claim is recorded as failed and the run
continues. Real providers read keys from the environment.

Field accuracy: exact match for IDs and dates, float tolerance for
amounts, normalized text match (lowercased, punctuation stripped) for
names and free-text fields. A model that paraphrases a description
counts as wrong on that field; that strictness is deliberate, since a
good extraction copies what the document says.

## Cost estimate, trial runs, and consistency cost

The runner prints a cost estimate before any API call. Text PDFs are
charged at ~4 chars/token; images use the documented OpenAI
high-detail formula (85 + 170 per 512px tile), so scanned pages cost
the app's scale-2 letter render (1224x1584, 2125 tokens) and photos
cost their actual pixel size. Extraction output is 600 tokens per
claim. `--limit N` runs a trial and reports the actual average cost
per claim plus the projected full-run cost.

`--budget USD` caps total spend: before each claim the runner
projects its cost from the running average (or the estimate, before
any claim has completed) and stops before starting a claim that
would push the total over the cap; a single claim that overshoots
stops the run immediately after it. The stop point (reason, claims
recorded, total spent, next unrun claim) is printed and recorded in
the raw file. `--resume` loads an existing raw file, skips claims
already completed, retries failed ones, and continues; the budget
counts cumulative spend across the original run and the resume.
Results are written after every claim (through a temp file), so a
run killed mid-flight leaves a resumable file behind. The report
adds breakdowns by loss type and by scan strength (the strongest
scan artifact level among a claim's files) alongside the
clean/messy and scanned/text splits.

Reported costs cover extraction plus the triage consistency call.
`ConsistencyResult` carries a `usage` field filled in by each adapter
after the call; it is excluded from the provider schema (via
`SkipJsonSchema`), so models are never asked to report their own
usage. The estimate counts the consistency call too: it re-sends the
same files, so its input is the file tokens again plus a small prompt
allowance, with 200 output tokens per claim.

## Refreshing saved results with live consistency calls

`evals/refresh.py` reuses a saved record's extraction and
classifications, calls the provider's consistency check with the
current prompt, and recomputes the route through the deterministic
triage nodes. Claims where the graph skips consistency need no
call. It prints a cost estimate before any API call and enforces
a `--budget` cap the same way `evals/run.py` does. This is how a
consistency-prompt change is verified without paying for
extraction again; `evals/run.py --claim-ids` covers the claims
that need a full re-run instead.

## Replaying saved results through triage

`evals/replay.py` re-runs the triage graph's deterministic nodes
over a saved raw file, with no API calls: the consistency verdict
is recovered from the saved reasons, and extraction,
classifications, and policy lookup are rebuilt from the record.
With no overrides it must reproduce the saved routes exactly
(300/300 on the Anthropic full run); that reproduction is the
harness's self-check. `--threshold` and `--exclusion-bar` override
the triage constants to measure a rule change, and
`--simulate-stated` nulls every field the labels say is absent,
modelling the extraction-prompt fix ("return null when a field
isn't stated; never infer") without a live run.

## Conflict scoring

Conflict claims carry `accepted_values` in the labels: either
document's value counts as correct for field accuracy. Separately,
the report measures conflict detection: whether triage flagged the
conflict (a reason reporting a consistency problem). The mock
provider never flags conflicts, so its detection rate is 0% by
design; that is the floor a real provider must beat. There are
exactly 30 conflict claims, converted from ready claims so every one
has an estimate document to disagree with the form; a conflict intent
on a claim without an estimate is re-rolled to missing-field, since
labels must never claim a conflict that is not in the documents.

## Photo grouping and photo mismatches

Damage photos are grouped strictly by loss type: flood images only
on water damage, storm cars only on auto, storm houses only on
property, and the damaged-door image is the only theft photo (a
search for public-domain break-in photos found nothing usable). The
generator fails loudly if a group is empty rather than falling back
to the whole pool. Each photo entry records `depicts`, the group it
was drawn from, and the breakdown reports photos by claim loss type
and by depicts group.

Separately, exactly 18 claims are deliberate photo mismatches: the
description comes from the claim's loss type but the photo is drawn
from another group's pool (a collision description with a flood
photo, for example). They are labeled `photo_mismatch` with
`mismatch_photo_depicts`, expect `needs_more_info`, and get their
own detection rate in the report, using the same consistency-flag
signal as conflict detection. Mismatch claims are clean renders,
not messy ones.

## Dataset variety

Claim forms use three layouts (plain lines, two-column tables, boxed
fields), varied label wording ("Policy #", "Insured", "DOL", "Amount
claimed"), three date formats (ISO, US, long), checkbox loss-type
selection on about 40% of forms, and extra content (instructions,
agent info, signature line, footer). Repair estimates itemize 3-5
lines summing to the total. Damage photos are real public-domain US
government disaster images from `evals/assets/photos/` (17 images,
source and license per image in `evals/assets/photo_sources.json`,
copied into the dataset as `photo_sources.json`). Scanned pages get
a sampled artifact strength (light/medium/heavy: skew, gray uneven
background, blur, JPEG compression, speckle), recorded per file as
`scan_strength`. Handwriting jitters whole words only, and every
generation runs a legibility check: text PDFs must yield every
rendered line through pypdf extraction, and scanned handwriting word
boxes must not overlap and must stay inside the page. Sample
policies number 12, with deductibles $250-$5,000, limits
$25,000-$250,000, every loss type excluded by at least one policy,
and windows spanning 2023-2027.

## Live run (after review, keys required)

The live run opens with `evals/check_models.py`: one minimal call per
provider to confirm each configured model ID exists; if any ID is
wrong, the run stops and reports it, and no substitute model is used
without explicit approval. Providers without keys in the environment
are reported as blocked. Keys are never written to any file, log,
report, or commit; `.env` is gitignored and the runner does not load
it.

Azure strict JSON Schema mode is tried first in the live run, falling
back to `json_object` only on a real schema rejection, with the
outcome recorded in `docs/decisions.md`. The adapter supports it
behind `AZURE_OPENAI_STRICT_JSON_SCHEMA` (default off, Azure only;
the standard OpenAI adapter always stays on `json_object`). The
strict schema is the plain inlined schema with every object forced to
`additionalProperties: false` and all properties required. After the
live run, the screenshot set in `docs/screenshots/` is retaken with
the realistic generated documents.

## Results

Only Claude (claude-sonnet-5-5) was evaluated live. The Azure,
Gemini, and standard OpenAI adapters are tested against stubbed
SDK responses only; their live behavior, and Azure strict JSON
Schema mode, are unverified. The threshold (0.7) and exclusion
bar (0.9) were derived from the dev split only; test claims were
never used for tuning.

### Full-run baseline: all 300 claims, original prompts

Anthropic full run, 300/300 ok, $6.1220 total ($0.020407/claim),
latency p50 7.66s / p95 21.36s per claim:

- Field accuracy 97.2%, classification accuracy 100%,
  route accuracy 75.7% (227/300)
- Conflict detection 27/30 (90%), photo-mismatch detection
  18/18 (100%)
- Consistency judge: precision 23.4%, false-alarm rate 58.3%,
  recall 93.8% (192 flagged, 48 real)
- Dangerous errors 3 (claims routed to likely_not_covered
  against their labels, all via an excluded loss type)

The route gap was triage strictness, not extraction: all 68
ready -> needs_more_info misses had every field value correct.

### Test split: 200 held-out claims, before/after the fixes

Split seed 123: 100 dev claims for tuning, 200 test claims never
used for tuning. The fixes: stated-only extraction prompt
(null when a field isn't stated, never infer), exclusion bar
0.9, threshold 0.7, and a consistency prompt that flags only
specific two-source contradictions with major/minor severity
(only major routes to needs_more_info). The after run is mixed
by design: 37 test claims with an absent field got full
re-runs, 163 fully-stated claims reused saved extractions
with a live consistency call only.

| Metric | Before | After |
| --- | --- | --- |
| Field accuracy | 97.6% (1561/1600) | 99.8% (1597/1600) |
| Route accuracy | 76.5% (153/200) | 78.5% (157/200) |
| Consistency precision | 27.7% | 31.3% |
| Consistency recall | 94.7% | 94.7% |
| False-alarm rate | 58.0% | 48.8% |
| Dangerous errors | 2 | 0 |
| Cost per claim | $0.0207 | $0.0103 (mixed-method artifact, see below) |

Read the field accuracy with its caveat: the after figure
reuses 163/200 extractions unchanged, so the improvement
concentrates in the 37 full re-runs, where the stated-only
prompt turned inferred values into correct abstentions. The
cost figure needs the same care: $0.0103 is what the mixed
re-run cost, not what the pipeline costs. Full-pipeline cost
is ~$0.02/claim (the 37 full re-runs averaged $0.020981), the
same as before. Expected-ready claims reaching ready:
10/54 before, 16/54 after; the judge still over-flags.

The dev gate that approved the test run (40 dev claims,
consistency-only): precision 34.6% -> 40.0%, false-alarm
56.7% -> 40.0%, recall 90.0% -> 80.0%, route 31/40 -> 33/40,
dangerous 0 -> 0, $0.2818 spent.

## What gets committed

Committed: the generator (`generate.py`), the photo pool with
its sources (`assets/`), the labels (`data/labels.json`), the
eval results (`results/` full run, test split before/after, and
dev refresh), and the recorded replay (`backend/src/
claims_copilot/providers/recorded_data.json`, built from the
full run by `build_recording.py`).

Not committed: the 773 generated dataset files (`data/files/`,
33 MB). They are reproducible from seed 42: the generator
defaults to dataset version 1, and a fresh v1 run was verified
byte-identical against the eval dataset (773 files,
`labels.json`, `photo_sources.json`), so committing them would
pay 33 MB for files anyone can rebuild. Version 2 is opt-in
via `--dataset-version 2` and includes the post-eval fixes
(filled signature/date lines, vehicle-break-in theft
narratives, narrative-consistent details, realistic tax lines;
see `docs/decisions.md`); it produces a different dataset, and
the eval results describe version 1. Rebuild the eval dataset
with:

    backend/.venv/bin/python evals/generate.py

Defaults are `--count 300 --seed 42 --dataset-version 1`.
Version 2 labels carry a `dataset_version` field; version 1
labels deliberately omit it so they stay byte-identical.

Intermediate refresh files (`results/anthropic_test_refresh.json`,
`results/anthropic_test_refresh2.json`, `results/test_full/`)
are also not committed; the merged `anthropic_test_raw.json`
supersedes them.
