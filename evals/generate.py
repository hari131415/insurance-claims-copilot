"""Generate a synthetic eval dataset: real files plus ground-truth labels.

Each claim gets rendered documents (claim-form PDFs, repair estimates,
police reports, damage photos) and one entry in labels.json carrying the
field values the documents state, each file's document type, and the
expected triage route with reasons. Filenames are neutral (doc_<hex>)
so nothing can be inferred from a name.

The expected route is computed from the labels by mirroring the triage
rules in claims_copilot/agents/triage.py: hard failures (inactive
policy, excluded loss type, amount at or below the deductible) route to
likely_not_covered, soft issues (missing field, missing required
document, unknown policy, conflicting values) route to needs_more_info,
and everything else is ready_for_adjuster. Two deliberate differences
from the app's runtime behavior: low model confidence is not in the
labels, because confidence is a model output, and conflicting values
count as a soft issue, because an adjuster must resolve them. Whether
the consistency node actually flags a conflict is exactly what the eval
measures.

Reproducibility: one seed drives every random choice, reportlab runs
with invariant=1 (no timestamps in the PDFs), and Pillow output is
deterministic. Same seed, same claims, values, traits, filenames, and
file bytes.

Dataset versions (--dataset-version, default 1): version 1 is frozen
and regenerates the eval dataset byte-identically; its code paths must
not gain or lose random draws. Version 2 adds the post-eval fixes:
filled signature/date lines, vehicle-break-in theft narratives,
narrative-consistent details, and realistic tax lines. The eval
results in evals/results/ describe version 1.

No script font is available on a stock system, so the handwriting trait
is slanted text with per-word jitter rather than a true script face.
Jitter moves whole words only: glyphs inside a word keep exact font
spacing, so characters cannot collide or turn into ambiguous shapes.
A legibility check runs on every generation: text PDFs must yield
every rendered line through pypdf extraction, and scanned handwriting
word boxes must not overlap and must stay inside the page.

Damage photos come from a pool of public-domain US government disaster
imagery (FEMA and similar) vendored in evals/assets/photos/, with
source and license recorded per image in evals/assets/
photo_sources.json (copied into the dataset as photo_sources.json).
Scanned pages get realistic artifacts at a sampled strength: slight
skew, gray uneven background, blur, JPEG compression, and speckle.

Run from the repo root with the backend venv, which provides the
installed claims_copilot package and reportlab:
    backend/.venv/bin/python evals/generate.py [--count N] [--seed S]
"""

import argparse
import json
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import TypedDict

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from claims_copilot.schemas import LossType
from claims_copilot.services.policies import (
    Policy,
    default_policies_path,
    load_policies,
    required_documents_for,
)

FIRST_NAMES = [
    "Alex",
    "Maria",
    "James",
    "Priya",
    "David",
    "Sarah",
    "Michael",
    "Emma",
    "Carlos",
    "Aisha",
    "Robert",
    "Li",
    "Fatima",
    "John",
    "Sofia",
    "Daniel",
    "Grace",
    "Omar",
    "Nina",
    "Victor",
]
LAST_NAMES = [
    "Rivera",
    "Garcia",
    "Smith",
    "Patel",
    "Johnson",
    "Lee",
    "Kim",
    "Brown",
    "Davis",
    "Wilson",
    "Martinez",
    "Anderson",
    "Thomas",
    "Hernandez",
    "Moore",
    "Jackson",
    "White",
    "Harris",
    "Clark",
    "Lewis",
]
STREETS = [
    "Main St",
    "Oak Ave",
    "Maple Dr",
    "Cedar Ln",
    "Washington Ave",
    "Park Blvd",
    "Elm St",
    "River Rd",
    "Highland Ave",
    "Sunset Blvd",
]
CITIES = [
    "Newark",
    "Jersey City",
    "Cherry Hill",
    "Trenton",
    "Camden",
    "Atlantic City",
    "Elizabeth",
    "Paterson",
    "Princeton",
    "Hoboken",
]
VEHICLES = [
    "2019 Toyota Camry",
    "2021 Honda Civic",
    "2018 Ford F-150",
    "2022 Tesla Model 3",
    "2020 Hyundai Elantra",
    "2017 Jeep Wrangler",
    "2023 Subaru Outback",
    "2016 Nissan Altima",
]
PROPERTIES = [
    "3-bedroom single-family home",
    "2-bedroom apartment",
    "4-bedroom colonial house",
    "townhouse",
    "single-story ranch home",
]
DESCRIPTIONS = {
    "auto": [
        "Rear-end collision at a traffic light. The other driver failed to stop.",
        "Side impact in a parking lot while reversing out of a space.",
        "Front-end collision after sliding on wet pavement into a guardrail.",
    ],
    "property": [
        "Windstorm tore shingles from the roof and rain leaked into the attic.",
        "A fallen tree branch broke two windows and damaged the siding.",
        "Hailstorm dented the roof and cracked a skylight.",
    ],
    # Version 2 theft narratives are vehicle break-ins only: the photo
    # pool holds a single theft image (a car with a damaged door and
    # taped window), so shed and garage thefts paired with it read as
    # photo contradictions.
    "theft": [
        "Vehicle broken into overnight; electronics and a bag were taken.",
        "Car window smashed overnight; a bag and electronics were taken from the back seat.",
        "Thieves forced the car door open overnight and took tools from the trunk.",
    ],
    "water damage": [
        "Burst pipe in the upstairs bathroom flooded two rooms below.",
        "Water heater failed and leaked across the basement floor.",
        "Roof leak during heavy rain soaked the living room ceiling.",
    ],
}
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
FIELD_LABELS = {
    "policy_number": "Policy number",
    "claimant_name": "Claimant name",
    "date_of_loss": "Date of loss",
    "loss_type": "Loss type",
    "incident_location": "Incident location",
    "incident_description": "Incident description",
    "estimated_damage_amount": "Estimated damage amount",
    "vehicle_or_property_details": "Vehicle or property details",
}
RENDERING_TRAITS = ["rotated", "low_resolution", "noise", "scanned", "handwriting"]
DEJAVU_SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
DEJAVU_SERIF_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
ASSETS_DIR = Path(__file__).parent / "assets"
# Varied wording so labels never simply match schema field names.
LABEL_ALIASES = {
    "policy_number": ["Policy number", "Policy #", "Policy No."],
    "claimant_name": ["Claimant name", "Insured", "Name of insured"],
    "date_of_loss": ["Date of loss", "DOL", "Loss date"],
    "loss_type": ["Loss type", "Type of loss"],
    "incident_location": ["Incident location", "Location of loss", "Where did it happen?"],
    "incident_description": ["Incident description", "Description of loss", "What happened?"],
    "estimated_damage_amount": ["Estimated damage amount", "Amount claimed", "Estimated loss"],
    "vehicle_or_property_details": [
        "Vehicle or property details",
        "Vehicle / property",
        "Property / vehicle info",
    ],
}
DATE_STYLES = ["iso", "us", "long"]
FORM_LAYOUTS = ["lines", "table", "boxed"]


class ScanParams(TypedDict):
    skew: float
    gray: int
    blur: float
    jpeg: int
    speckle: int


SCAN_STRENGTHS: dict[str, ScanParams] = {
    "light": {"skew": 0.5, "gray": 245, "blur": 0.3, "jpeg": 85, "speckle": 800},
    "medium": {"skew": 1.0, "gray": 232, "blur": 0.7, "jpeg": 70, "speckle": 400},
    "heavy": {"skew": 2.0, "gray": 215, "blur": 1.2, "jpeg": 55, "speckle": 200},
}
ESTIMATE_ITEMS = {
    "auto": ["Front bumper replacement", "Labor", "Paint and materials", "Tax"],
    "property": ["Roof repair", "Drywall replacement", "Labor", "Debris removal"],
    "theft": ["Door and lock replacement", "Glass replacement", "Labor"],
    "water damage": ["Drywall replacement", "Flooring", "Labor", "Mold remediation"],
}


def _format_date(value: date, style: str) -> str:
    if style == "us":
        return value.strftime("%m/%d/%Y")
    if style == "long":
        return value.strftime("%B %d, %Y").replace(" 0", " ")
    return value.isoformat()


def _estimate_lines(
    rng: random.Random,
    policy_number: str,
    loss_type: str,
    estimate_amount: float,
    version: int,
) -> list[str]:
    items = ESTIMATE_ITEMS[loss_type]
    if version == 1 or "Tax" not in items:
        shares = [rng.uniform(0.15, 0.40) for _ in items]
        total_share = sum(shares)
        line_amounts = [round(estimate_amount * share / total_share, 2) for share in shares]
        line_amounts[-1] = round(estimate_amount - sum(line_amounts[:-1]), 2)
        return [
            f"Policy number: {policy_number}",
            *[
                f"{name}: {_fmt_amount(value)}"
                for name, value in zip(items, line_amounts, strict=True)
            ],
            f"Total estimate: {_fmt_amount(estimate_amount)}",
        ]
    # Version 2: tax is a small percentage of the pre-tax subtotal, not
    # a random share of the total. Version 1 gave tax its own share,
    # which produced lines like $26,136.94 of tax on an $87,952.47
    # estimate. The total still matches exactly.
    tax_rate = rng.uniform(0.06, 0.09)
    subtotal = round(estimate_amount / (1 + tax_rate), 2)
    tax = round(estimate_amount - subtotal, 2)
    non_tax = [name for name in items if name != "Tax"]
    shares = [rng.uniform(0.15, 0.40) for _ in non_tax]
    total_share = sum(shares)
    split = [round(subtotal * share / total_share, 2) for share in shares]
    split[-1] = round(subtotal - sum(split[:-1]), 2)
    amounts = dict(zip(non_tax, split, strict=True))
    amounts["Tax"] = tax
    return [
        f"Policy number: {policy_number}",
        *[f"{name}: {_fmt_amount(amounts[name])}" for name in items],
        f"Total estimate: {_fmt_amount(estimate_amount)}",
    ]


def _fmt_amount(value: float) -> str:
    return f"${value:,.2f}"


@dataclass
class DocPlan:
    document_type: str
    # Pre-formatted "Label: value" display lines, exactly as rendered.
    lines: list[str]
    # Raw field values this document states, for the labels union.
    stated: dict[str, object]
    # Claim-form layout: lines, table, or boxed. Other docs use lines.
    layout: str = "lines"
    # Non-field lines (instructions, agent, signature, footer) drawn
    # before the field block (first) and after it (rest).
    extra_lines: list[str] = field(default_factory=list)


@dataclass
class ClaimSpec:
    claim_id: str
    loss_type: str
    fields: dict[str, object | None]
    docs: list[DocPlan]
    traits: list[str]
    expected_route: str
    expected_reasons: list[str]
    # Values accepted as correct for a field, when documents disagree.
    accepted: dict[str, list[object]] = field(default_factory=dict)
    # Policy, for post-sampling conversions that recompute the route.
    # Not serialized; the labels carry the policy number in fields.
    policy: Policy | None = None
    # Loss-type group the damage photo is drawn from, when it differs
    # from the claim's own loss type: a deliberate photo mismatch.
    mismatch_photo: str | None = None


def _sample_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randrange((end - start).days + 1))


def _sample_details(
    rng: random.Random, loss_type: str, description: str, version: int
) -> str:
    if version == 1:
        return rng.choice(VEHICLES) if loss_type in ("auto", "theft") else rng.choice(PROPERTIES)
    # Version 2: details follow the narrative instead of being sampled
    # independently. A single-story home has no upstairs bathroom, and
    # an apartment has no basement, so those combinations read as
    # contradictions.
    if loss_type in ("auto", "theft"):
        return rng.choice(VEHICLES)
    candidates = PROPERTIES
    if "upstairs bathroom" in description:
        candidates = [p for p in candidates if p != "single-story ranch home"]
    if "basement floor" in description:
        candidates = [p for p in candidates if p != "2-bedroom apartment"]
    return rng.choice(candidates)


# Version 1 theft narratives, frozen: version 1 must regenerate the eval
# dataset byte-identically, so this list keeps the original order and
# wording even though the shed and garage narratives pair badly with
# the single theft photo.
THEFT_DESCRIPTIONS_V1 = [
    "Vehicle broken into overnight; electronics and a bag were taken.",
    "Tools stolen from a locked garage after the side door was forced.",
    "Bicycle and power tools taken from the backyard shed.",
]


def _sample_claim(
    rng: random.Random, index: int, policies: list[Policy], version: int
) -> ClaimSpec:
    claim_id = f"claim-{index:04d}"
    loss_type = rng.choices(
        ["auto", "property", "theft", "water damage"],
        weights=[0.35, 0.30, 0.15, 0.20],
    )[0]
    route_roll = rng.random()
    if route_roll < 0.50:
        intent: str = "ready"
    elif route_roll < 0.80:
        intent = rng.choices(
            ["missing_doc", "missing_field", "conflict", "unknown_policy"],
            weights=[0.40, 0.30, 0.20, 0.10],
        )[0]
    else:
        intent = rng.choices(
            ["inactive", "excluded", "below_deductible"], weights=[0.40, 0.30, 0.30]
        )[0]

    if intent == "unknown_policy":
        policy_number = f"POL-900{rng.randrange(100):03d}"
        policy = None
        loss_date = _sample_date(rng, date(2025, 1, 1), date(2026, 12, 31))
    elif intent == "inactive":
        policy = rng.choice(policies)
        policy_number = policy.policy_number
        # A date outside this policy's own window, within 2023-2027.
        # Window-edge policies only have one valid side: a 2023-only
        # policy can only go after, a 2027-only one only before.
        before_possible = policy.active_from > date(2023, 1, 1)
        after_possible = policy.active_to < date(2027, 12, 31)
        if before_possible and (not after_possible or rng.random() < 0.5):
            loss_date = _sample_date(
                rng,
                max(date(2023, 1, 1), policy.active_from - timedelta(days=365)),
                policy.active_from - timedelta(days=1),
            )
        else:
            loss_date = _sample_date(
                rng,
                policy.active_to + timedelta(days=1),
                min(date(2027, 12, 31), policy.active_to + timedelta(days=365)),
            )
    elif intent == "excluded":
        excludable = [
            loss
            for loss in ["auto", "property", "theft", "water damage"]
            if any(LossType(loss) in p.excluded_loss_types for p in policies)
        ]
        loss_type = rng.choice(excludable)
        candidates = [p for p in policies if LossType(loss_type) in p.excluded_loss_types]
        policy = rng.choice(candidates)
        policy_number = policy.policy_number
        loss_date = _sample_date(rng, policy.active_from, policy.active_to)
    else:
        policy = rng.choice(policies)
        policy_number = policy.policy_number
        loss_date = _sample_date(rng, policy.active_from, policy.active_to)

    # A conflict claim needs an estimate document to disagree with the
    # form. Without one, the labels would claim a conflict that is not
    # in the documents; those intents become missing-field instead.
    required = required_documents_for(policy, LossType(loss_type)) if policy is not None else []
    required_types = {req.document_type for req in required}
    if (
        intent == "conflict"
        and loss_type not in ("auto", "water damage")
        and "repair_estimate" not in required_types
    ):
        intent = "missing_field"

    if policy is not None and intent == "below_deductible":
        amount = round(rng.uniform(100, policy.deductible), 2)
    elif policy is not None and intent == "ready" and rng.random() < 0.10:
        amount = round(rng.uniform(policy.coverage_limit + 5000, policy.coverage_limit + 40000), 2)
    elif policy is not None:
        amount = round(rng.uniform(policy.deductible + 500, policy.coverage_limit - 5000), 2)
    else:
        amount = round(rng.uniform(1000, 20000), 2)

    claimant = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
    location = f"{rng.randrange(1, 9999)} {rng.choice(STREETS)}, {rng.choice(CITIES)}, NJ"
    if loss_type == "theft" and version == 1:
        description = rng.choice(THEFT_DESCRIPTIONS_V1)
    else:
        description = rng.choice(DESCRIPTIONS[loss_type])
    details = _sample_details(rng, loss_type, description, version)
    latent: dict[str, object] = {
        "policy_number": policy_number,
        "claimant_name": claimant,
        "date_of_loss": loss_date.isoformat(),
        "loss_type": loss_type,
        "incident_location": location,
        "incident_description": description,
        "estimated_damage_amount": amount,
        "vehicle_or_property_details": details,
    }

    omitted_fields: set[str] = set()
    if intent == "missing_field":
        omitted_fields = set(rng.sample(FIELD_KEYS, rng.choice([1, 1, 2])))

    conflict = intent == "conflict"
    estimate_amount = amount
    if conflict:
        estimate_amount = round(amount * (1 + rng.uniform(0.15, 0.40)), 2)

    omitted_doc: str | None = None
    if intent == "missing_doc" and required_types:
        omitted_doc = rng.choice(sorted(required_types))

    docs: list[DocPlan] = []
    if omitted_doc != "claim_form":
        form_stated = {key: None if key in omitted_fields else latent[key] for key in FIELD_KEYS}
        # Varied wording per form, so labels never match schema names.
        aliases = {key: rng.choice(LABEL_ALIASES[key]) for key in FIELD_KEYS}
        form_date = _format_date(loss_date, rng.choice(DATE_STYLES))
        form_values: dict[str, str] = {
            "policy_number": policy_number,
            "claimant_name": claimant,
            "date_of_loss": form_date,
            "loss_type": loss_type,
            "incident_location": location,
            "incident_description": description,
            "estimated_damage_amount": _fmt_amount(amount),
            "vehicle_or_property_details": details,
        }
        field_lines = []
        use_checkboxes = rng.random() < 0.40
        for key in FIELD_KEYS:
            if key in omitted_fields:
                continue
            if key == "loss_type" and use_checkboxes:
                boxes = []
                for option in ["auto", "property", "theft", "water damage"]:
                    mark = "X" if option == loss_type else " "
                    boxes.append(f"[{mark}] {option}")
                field_lines.append(f"{aliases[key]}: {' '.join(boxes)}")
            else:
                field_lines.append(f"{aliases[key]}: {form_values[key]}")
        agent = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        agent_phone = f"(800) 555-{rng.randrange(1000, 9999):04d}"
        docs.append(
            DocPlan(
                document_type="claim_form",
                lines=field_lines,
                stated=form_stated,
                layout=rng.choice(FORM_LAYOUTS),
                extra_lines=[
                    "Please complete all sections. Attach supporting documents.",
                    f"Agent: {agent}  Phone: {agent_phone}",
                    # Version 1 keeps the blank signature line so the
                    # eval dataset regenerates byte-identically.
                    # Version 2 signs and dates the form: a generated
                    # form with blank signature lines reads as
                    # unsigned, which is a dataset artifact, not a
                    # real claim.
                    "Signature: ________________________  Date: __________"
                    if version == 1
                    else f"Signature: {claimant}  Date: {form_date}",
                    "Form CL-104  |  Page 1 of 1  |  Questions? Call (800) 555-0142",
                ],
            )
        )
    # Documents rendered are the loss type's usual set plus anything
    # this policy requires, so a required document is only missing when
    # the intent says so.
    if (
        loss_type in ("auto", "water damage") or "repair_estimate" in required_types
    ) and omitted_doc != "repair_estimate":
        docs.append(
            DocPlan(
                document_type="repair_estimate",
                lines=_estimate_lines(rng, policy_number, loss_type, estimate_amount, version),
                stated={
                    "policy_number": policy_number,
                    "estimated_damage_amount": estimate_amount,
                },
            )
        )
    if loss_type == "theft" and omitted_doc != "police_report":
        docs.append(
            DocPlan(
                document_type="police_report",
                lines=[
                    # Report number prefix matches the incident year; an
                    # earlier version hardcoded "26", which looked like a
                    # conflict on 2024 incidents and was a bug, not data.
                    f"Report number: {str(loss_date.year)[2:]}-{rng.randrange(100000, 999999)}",
                    f"Date of incident: {_format_date(loss_date, rng.choice(DATE_STYLES))}",
                    f"Location: {location}",
                    f"Narrative: {description}",
                ],
                stated={
                    "date_of_loss": loss_date.isoformat(),
                    "incident_location": location,
                },
            )
        )
    photo_typical = loss_type in ("auto", "property", "water damage")
    photo_required = "damage_photo" in required_types
    photo_random = loss_type == "theft" and rng.random() < 0.5
    if (photo_typical or photo_required or photo_random) and omitted_doc != "damage_photo":
        docs.append(DocPlan(document_type="damage_photo", lines=[], stated={}))

    fields: dict[str, object | None] = dict.fromkeys(FIELD_KEYS)
    for doc in docs:
        for key, value in doc.stated.items():
            if fields[key] is None:
                fields[key] = value
    accepted: dict[str, list[object]] = {}
    if conflict:
        fields["estimated_damage_amount"] = amount
        accepted["estimated_damage_amount"] = [amount, estimate_amount]

    expected_route, expected_reasons = _expected_route(
        fields, [doc.document_type for doc in docs], policy, conflict
    )
    traits: list[str] = []
    if intent == "missing_field":
        traits.append("missing_fields")
    elif intent == "conflict":
        traits.append("conflicting_values")
    return ClaimSpec(
        claim_id=claim_id,
        loss_type=loss_type,
        fields=fields,
        docs=docs,
        traits=traits,
        expected_route=expected_route,
        expected_reasons=expected_reasons,
        accepted=accepted,
        policy=policy,
    )


def _expected_route(
    fields: dict[str, object | None],
    doc_types: list[str],
    policy: Policy | None,
    conflict: bool,
    mismatch: bool = False,
) -> tuple[str, list[str]]:
    hard: list[str] = []
    soft: list[str] = []
    notes: list[str] = []
    if policy is None:
        number = fields["policy_number"] or "unknown"
        soft.append(f"Policy {number} was not found in the system; verify the policy number.")
    else:
        date_of_loss = fields["date_of_loss"]
        if date_of_loss is None:
            soft.append("Date of loss is missing, so policy coverage dates could not be checked.")
        elif not (policy.active_from <= date.fromisoformat(str(date_of_loss)) <= policy.active_to):
            hard.append(f"Policy was not active on the date of loss ({date_of_loss}).")
        loss_type = fields["loss_type"]
        if loss_type is not None and LossType(str(loss_type)) in policy.excluded_loss_types:
            hard.append(f"Loss type {loss_type} is excluded under this policy.")
        amount = fields["estimated_damage_amount"]
        if isinstance(amount, (int, float)):
            amount_value = float(amount)
            if amount_value <= policy.deductible:
                hard.append(
                    f"Estimated damage ({_fmt_amount(amount_value)}) is at or below "
                    f"the deductible ({_fmt_amount(policy.deductible)}), "
                    "so no payout would be due."
                )
            if amount_value > policy.coverage_limit:
                notes.append(
                    f"Estimated damage ({_fmt_amount(amount_value)}) exceeds the "
                    f"coverage limit ({_fmt_amount(policy.coverage_limit)}); "
                    "payout would be capped at the limit."
                )
    if policy is not None:
        loss_type = fields["loss_type"]
        enum_loss = LossType(str(loss_type)) if loss_type is not None else None
        for requirement in required_documents_for(policy, enum_loss):
            if requirement.document_type not in doc_types:
                soft.append(f"Missing required document: {requirement.name}.")
    for key in FIELD_KEYS:
        if fields[key] is None:
            soft.append(f"{FIELD_LABELS[key]} was not extracted from the documents.")
    if conflict:
        soft.append(
            "Conflicting values across documents: the repair estimate amount "
            "differs from the claim form amount."
        )
    if mismatch:
        soft.append("Photo does not match the incident description.")
    if hard:
        return "likely_not_covered", [*hard, *soft, *notes]
    if soft:
        return "needs_more_info", [*soft, *notes]
    return "ready_for_adjuster", ["All required checks passed.", *notes]


def _wrap(text: str, measure: Callable[[str], float], max_width: float) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines = []
    current = words[0]
    for word in words[1:]:
        if measure(f"{current} {word}") <= max_width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_word_jittered_pdf(
    page: canvas.Canvas,
    x: float,
    y: float,
    segment: str,
    font: str,
    size: int,
    rng: random.Random,
) -> None:
    # Whole words move; glyphs inside a word keep exact font spacing,
    # so jitter can never overlap glyphs or change a character's shape.
    cursor = x
    space = pdfmetrics.stringWidth(" ", font, size)
    for word in segment.split(" "):
        page.drawString(cursor + rng.uniform(-0.5, 0.5), y + rng.uniform(-1.5, 1.5), word)
        cursor += pdfmetrics.stringWidth(word, font, size) + space


def _draw_word_jittered_pil(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    segment: str,
    font: ImageFont.FreeTypeFont,
    rng: random.Random,
    scale: float,
) -> list[tuple[int, int, int, int]]:
    boxes = []
    x, y = xy
    space = draw.textlength(" ", font=font)
    for word in segment.split(" "):
        jitter_x = int(rng.uniform(-1, 1) * scale)
        jitter_y = int(rng.uniform(-3, 3) * scale)
        draw.text((x + jitter_x, y + jitter_y), word, font=font, fill="black")
        width = int(draw.textlength(word, font=font))
        boxes.append(
            (x + jitter_x, y + jitter_y, x + jitter_x + width, y + jitter_y + int(font.size))
        )
        x += width + int(space)
    return boxes


def _load_photo_pool() -> list[dict]:
    sources_path = ASSETS_DIR / "photo_sources.json"
    records: list[dict] = json.loads(sources_path.read_text())
    if not records:
        raise SystemExit("photo pool is empty; see evals/assets/photo_sources.json")
    for record in records:
        if not (ASSETS_DIR / "photos" / record["file"]).is_file():
            raise SystemExit(f"photo pool file missing: {record['file']}")
    return records


def _render_photo(
    rng: random.Random,
    loss_type: str,
    traits: list[str],
    path: Path,
    pool: list[dict],
    group: str | None = None,
) -> str:
    # Strict grouping: a photo is only drawn from its own loss-type
    # group. No fallback to the whole pool: an implausible photo is a
    # generator bug, and failing loudly beats shipping it.
    wanted = group or loss_type
    matching = [record for record in pool if wanted in record["loss_types"]]
    if not matching:
        raise SystemExit(f"photo pool has no image for loss type {wanted!r}")
    record = rng.choice(matching)
    image = Image.open(ASSETS_DIR / "photos" / record["file"]).convert("RGB")
    if "low_resolution" in traits:
        image = image.resize((320, int(image.height * 320 / image.width)))
    if "noise" in traits:
        pixels = image.load()
        assert pixels is not None
        width, height = image.size
        for _ in range(width * height // 50):
            x, y = rng.randrange(width), rng.randrange(height)
            pixels[x, y] = (0, 0, 0) if rng.random() < 0.5 else (255, 255, 255)
    if "rotated" in traits:
        image = image.rotate(90, expand=True)
    image.save(path, "JPEG", quality=40 if "low_resolution" in traits else 85)
    return str(record["file"])


def _parse_checkbox_line(line: str) -> tuple[str, list[tuple[bool, str]]] | None:
    if "[X]" not in line and "[ ]" not in line:
        return None
    label, _, rest = line.partition(": ")
    options: list[tuple[bool, str]] = []
    parts = rest.split("[")
    for part in parts[1:]:
        mark, _, text = part.partition("] ")
        options.append((mark == "X", text.strip()))
    if not options:
        return None
    return label, options


def _render_scanned_page(
    rng: random.Random,
    title: str,
    doc: DocPlan,
    traits: list[str],
    path: Path,
) -> tuple[str, list[tuple[int, int, tuple[int, int, int, int]]], list[str]]:
    scale = 0.5 if "low_resolution" in traits else 1.0
    width, height = int(1275 * scale), int(1650 * scale)
    strength = rng.choice(list(SCAN_STRENGTHS))
    params = SCAN_STRENGTHS[strength]
    base = params["gray"]
    # Uneven background: a small random gray field, blurred up to page
    # size, blended over the base gray like uneven scanner lighting.
    blotch = Image.new("L", (64, 82))
    blotch_pixels = blotch.load()
    assert blotch_pixels is not None
    for bx in range(64):
        for by in range(82):
            blotch_pixels[bx, by] = rng.randrange(200, 256)
    blotch = blotch.resize((width, height), Image.Resampling.BICUBIC).convert("RGB")
    image = Image.blend(Image.new("RGB", (width, height), (base, base, base)), blotch, 0.25)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(DEJAVU_SERIF, int(28 * scale))
    bold_path = DEJAVU_SERIF_BOLD if Path(DEJAVU_SERIF_BOLD).is_file() else DEJAVU_SERIF
    bold = ImageFont.truetype(bold_path, int(32 * scale))
    small = ImageFont.truetype(DEJAVU_SERIF, int(20 * scale))
    y = int(80 * scale)
    left = int(80 * scale)
    right = width - int(80 * scale)
    # (line index, row baseline, word box). The row matters: a wrapped
    # line has several rows sharing one line index, and sorting boxes
    # across rows would report false collisions.
    word_boxes: list[tuple[int, int, tuple[int, int, int, int]]] = []
    rendered_lines = [title]

    def draw_text_line(line: str, line_index: int, x: int, max_w: int) -> None:
        nonlocal y
        for segment in _wrap(line, lambda text: draw.textlength(text, font=font), max_w):
            if "handwriting" in traits:
                row_y = y
                boxes = _draw_word_jittered_pil(draw, (x, y), segment, font, rng, scale)
                for box in boxes:
                    word_boxes.append((line_index, row_y, box))
            else:
                draw.text((x, y), segment, font=font, fill="black")
            y += int(56 * scale)

    draw.text((left, y), title, font=bold, fill="black")
    y += int(70 * scale)
    ordered_lines = ([doc.extra_lines[0]] if doc.extra_lines else []) + doc.lines
    line_index = 0
    for line in ordered_lines:
        rendered_lines.append(line)
        checkbox = _parse_checkbox_line(line)
        if checkbox is not None:
            label, options = checkbox
            draw.text((left, y), f"{label}:", font=font, fill="black")
            cursor = left + int(draw.textlength(f"{label}: ", font=font))
            box_size = int(20 * scale)
            for selected, option in options:
                draw.rectangle(
                    [cursor, y + 2, cursor + box_size, y + 2 + box_size], outline="black"
                )
                if selected:
                    draw.line([cursor, y + 2, cursor + box_size, y + 2 + box_size], fill="black")
                    draw.line([cursor + box_size, y + 2, cursor, y + 2 + box_size], fill="black")
                cursor += box_size + int(8 * scale)
                draw.text((cursor, y), option, font=font, fill="black")
                cursor += int(draw.textlength(option, font=font)) + int(24 * scale)
            y += int(56 * scale)
            line_index += 1
            continue
        if doc.layout == "table" and ": " in line and line in doc.lines:
            label, _, value = line.partition(": ")
            draw.text((left, y), f"{label}:", font=font, fill="black")
            value_x = int(480 * scale)
            for segment in _wrap(
                value, lambda text: draw.textlength(text, font=font), right - value_x
            ):
                if "handwriting" in traits:
                    row_y = y
                    boxes = _draw_word_jittered_pil(draw, (value_x, y), segment, font, rng, scale)
                    for box in boxes:
                        word_boxes.append((line_index, row_y, box))
                else:
                    draw.text((value_x, y), segment, font=font, fill="black")
                y += int(56 * scale)
            draw.line([left, y + 4, right, y + 4], fill=(150, 150, 150))
            y += int(16 * scale)
            line_index += 1
            continue
        if doc.layout == "boxed" and ": " in line and line in doc.lines:
            label, _, value = line.partition(": ")
            draw.text((left, y), label, font=small, fill="black")
            y += int(30 * scale)
            segments = _wrap(
                value, lambda text: draw.textlength(text, font=font), right - left - 24
            )
            box_height = int(56 * scale) * len(segments) + int(16 * scale)
            draw.rectangle([left, y, right, y + box_height], outline="black")
            box_y = y + int(8 * scale)
            for segment in segments:
                if "handwriting" in traits:
                    row_y = box_y
                    boxes = _draw_word_jittered_pil(
                        draw, (left + 12, box_y), segment, font, rng, scale
                    )
                    for box in boxes:
                        word_boxes.append((line_index, row_y, box))
                else:
                    draw.text((left + 12, box_y), segment, font=font, fill="black")
                box_y += int(56 * scale)
            y += box_height + int(24 * scale)
            line_index += 1
            continue
        draw_text_line(line, line_index, left, right - left)
        line_index += 1
    for line in doc.extra_lines[1:]:
        rendered_lines.append(line)
        draw_text_line(line, line_index, left, right - left)
        line_index += 1

    image = image.filter(ImageFilter.GaussianBlur(params["blur"]))
    skew = rng.uniform(-params["skew"], params["skew"])
    image = image.rotate(skew, expand=False, fillcolor=(base, base, base))
    pixels = image.load()
    assert pixels is not None
    for _ in range(width * height // params["speckle"]):
        x, y = rng.randrange(width), rng.randrange(height)
        pixels[x, y] = (120, 120, 120)
    if "rotated" in traits:
        image = image.rotate(90, expand=True)
    image.save(path, "JPEG", quality=params["jpeg"])
    return strength, word_boxes, rendered_lines


def _render_pdf(
    rng: random.Random,
    title: str,
    doc: DocPlan,
    traits: list[str],
    path: Path,
) -> list[str]:
    # Scanned pages are rendered and embedded by _render_doc, which needs
    # the word boxes for the legibility check; this function is text only.
    page = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    if "rotated" in traits:
        page.translate(letter[1], 0)
        page.rotate(90)
    y = 700
    page.setFont("Times-Bold", 16)
    page.drawString(72, y, title)
    y -= 40
    font = "Times-Italic" if "handwriting" in traits else "Times-Roman"
    page.setFont(font, 12)
    max_width = letter[0] - 144
    rendered_lines = [title]

    def draw_line(text: str, x: float, max_w: float) -> None:
        nonlocal y
        for segment in _wrap(text, lambda s: pdfmetrics.stringWidth(s, font, 12), max_w):
            if "handwriting" in traits:
                _draw_word_jittered_pdf(page, x, y, segment, font, 12, rng)
            else:
                page.drawString(x, y, segment)
            y -= 22

    ordered_lines = ([doc.extra_lines[0]] if doc.extra_lines else []) + doc.lines
    for line in ordered_lines:
        rendered_lines.append(line)
        if doc.layout == "table" and ": " in line and line in doc.lines:
            label, _, value = line.partition(": ")
            page.drawString(72, y, f"{label}:")
            for segment in _wrap(value, lambda s: pdfmetrics.stringWidth(s, font, 12), 260):
                if "handwriting" in traits:
                    _draw_word_jittered_pdf(page, 280, y, segment, font, 12, rng)
                else:
                    page.drawString(280, y, segment)
                y -= 22
            page.setStrokeGray(0.6)
            page.line(72, y + 4, 540, y + 4)
            y -= 10
            continue
        if doc.layout == "boxed" and ": " in line and line in doc.lines:
            label, _, value = line.partition(": ")
            page.setFont("Times-Bold", 10)
            page.drawString(72, y, label)
            page.setFont(font, 12)
            y -= 14
            segments = _wrap(value, lambda s: pdfmetrics.stringWidth(s, font, 12), 452)
            box_height = 22 * len(segments) + 8
            page.rect(72, y - box_height, 468, box_height)
            box_y = y - 18
            for segment in segments:
                if "handwriting" in traits:
                    _draw_word_jittered_pdf(page, 84, box_y, segment, font, 12, rng)
                else:
                    page.drawString(84, box_y, segment)
                box_y -= 22
            y -= box_height + 12
            continue
        draw_line(line, 72, max_width)
    for line in doc.extra_lines[1:]:
        rendered_lines.append(line)
        draw_line(line, 72, max_width)
    if "noise" in traits:
        for _ in range(60):
            page.setFillGray(rng.uniform(0.4, 0.8))
            page.circle(rng.uniform(40, 570), rng.uniform(40, 750), 1, stroke=0, fill=1)
    page.showPage()
    page.save()
    return rendered_lines


def _render_doc(
    rng: random.Random,
    spec: ClaimSpec,
    doc: DocPlan,
    traits: list[str],
    path: Path,
    pool: list[dict],
) -> tuple[dict, dict | None]:
    if doc.document_type == "damage_photo":
        depicts = spec.mismatch_photo or spec.loss_type
        source = _render_photo(rng, spec.loss_type, traits, path, pool, spec.mismatch_photo)
        return {
            "filename": path.name,
            "document_type": "damage_photo",
            "scanned": False,
            "pages": None,
            "source": source,
            "depicts": depicts,
        }, None
    titles = {
        "claim_form": "Insurance Claim Form",
        "repair_estimate": "Repair Estimate",
        "police_report": "Police Report",
    }
    if "scanned" in traits:
        tmp = path.with_suffix(".jpg")
        strength, boxes, _rendered_lines = _render_scanned_page(
            rng, titles[doc.document_type], doc, traits, tmp
        )
        page = canvas.Canvas(str(path), pagesize=letter, invariant=1)
        page.drawImage(ImageReader(str(tmp)), 0, 0, width=letter[0], height=letter[1])
        page.showPage()
        page.save()
        tmp.unlink()
        scale = 0.5 if "low_resolution" in traits else 1.0
        canvas_size = (int(1275 * scale), int(1650 * scale))
        return {
            "filename": path.name,
            "document_type": doc.document_type,
            "scanned": True,
            "pages": 1,
            "scan_strength": strength,
            "layout": doc.layout,
            "checkbox_loss_type": any("[X]" in line for line in doc.lines),
        }, {
            "claim_id": spec.claim_id,
            "filename": path.name,
            "scanned": True,
            "word_boxes": boxes,
            "canvas": canvas_size,
        }
    rendered_lines = _render_pdf(rng, titles[doc.document_type], doc, traits, path)
    return {
        "filename": path.name,
        "document_type": doc.document_type,
        "scanned": False,
        "pages": 1,
        "layout": doc.layout,
        "checkbox_loss_type": any("[X]" in line for line in doc.lines),
    }, {
        "claim_id": spec.claim_id,
        "filename": path.name,
        "scanned": False,
        "rendered_lines": rendered_lines,
        "path": path,
    }


def _normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def check_legibility(checks: list[dict]) -> None:
    from pypdf import PdfReader

    checked = 0
    for check in checks:
        if check["scanned"]:
            width, height = check["canvas"]
            by_line: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}
            for line_index, row_y, box in check["word_boxes"]:
                by_line.setdefault((line_index, row_y), []).append(box)
            for (line_index, _row_y), boxes in by_line.items():
                ordered = sorted(boxes, key=lambda box: box[0])
                for box in ordered:
                    if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
                        raise SystemExit(
                            f"legibility failed: {check['claim_id']} {check['filename']} "
                            f"word box {box} outside canvas {width}x{height}"
                        )
                for prev, nxt in zip(ordered, ordered[1:], strict=False):
                    if nxt[0] - prev[2] < 1:
                        raise SystemExit(
                            f"legibility failed: {check['claim_id']} {check['filename']} "
                            f"line {line_index} words collide (gap {nxt[0] - prev[2]}px)"
                        )
            checked += 1
            continue
        reader = PdfReader(str(check["path"]))
        extracted = _normalize_text(" ".join(page.extract_text() or "" for page in reader.pages))
        for line in check["rendered_lines"]:
            normalized = _normalize_text(line)
            if not normalized:
                continue
            if normalized not in extracted:
                raise SystemExit(
                    f"legibility failed: {check['claim_id']} {check['filename']} "
                    f"line not recovered by text extraction: {line!r}"
                )
        checked += 1
    print(f"legibility: {checked} documents checked, 0 failures")


LOSS_GROUPS = ["auto", "property", "theft", "water damage"]
CONFLICT_TARGET = 30
MISMATCH_TARGET = 18


def _flip_to_conflict(rng: random.Random, spec: ClaimSpec, version: int) -> None:
    estimate = next(doc for doc in spec.docs if doc.document_type == "repair_estimate")
    form_amount = spec.fields["estimated_damage_amount"]
    assert isinstance(form_amount, (int, float))
    new_amount = round(float(form_amount) * (1 + rng.uniform(0.15, 0.40)), 2)
    policy_number = spec.fields["policy_number"]
    assert isinstance(policy_number, str)
    estimate.lines = _estimate_lines(rng, policy_number, spec.loss_type, new_amount, version)
    estimate.stated["estimated_damage_amount"] = new_amount
    spec.accepted = {"estimated_damage_amount": [form_amount, new_amount]}
    spec.expected_route, spec.expected_reasons = _expected_route(
        spec.fields,
        [doc.document_type for doc in spec.docs],
        spec.policy,
        conflict=True,
    )
    if "conflicting_values" not in spec.traits:
        spec.traits.append("conflicting_values")


def _flip_to_mismatch(rng: random.Random, spec: ClaimSpec) -> None:
    others = [group for group in LOSS_GROUPS if group != spec.loss_type]
    spec.mismatch_photo = rng.choice(others)
    spec.expected_route, spec.expected_reasons = _expected_route(
        spec.fields,
        [doc.document_type for doc in spec.docs],
        spec.policy,
        conflict=False,
        mismatch=True,
    )


def generate(count: int, seed: int, out: Path, version: int = 1) -> dict:
    rng = random.Random(seed)
    policies = load_policies(default_policies_path())
    pool = _load_photo_pool()
    specs = [_sample_claim(rng, index + 1, policies, version) for index in range(count)]
    # Exact targets, converted from ready claims: conflict needs an
    # estimate document to disagree with the form, mismatch needs a
    # photo document to swap. Converted conflict claims gain the
    # messy trait; mismatch claims stay clean renders.
    conflicts = sum(1 for spec in specs if spec.accepted)
    for spec in specs:
        if conflicts >= CONFLICT_TARGET:
            break
        if spec.expected_route != "ready_for_adjuster":
            continue
        if not any(doc.document_type == "repair_estimate" for doc in spec.docs):
            continue
        _flip_to_conflict(rng, spec, version)
        conflicts += 1
    mismatches = 0
    for spec in specs:
        if mismatches >= MISMATCH_TARGET:
            break
        if spec.expected_route != "ready_for_adjuster":
            continue
        if not any(doc.document_type == "damage_photo" for doc in spec.docs):
            continue
        _flip_to_mismatch(rng, spec)
        mismatches += 1
    messy_target = round(count * 0.30)
    messy = [spec for spec in specs if spec.traits]
    rest = [spec for spec in specs if not spec.traits]
    rng.shuffle(rest)
    messy.extend(rest[: max(0, messy_target - len(messy))])
    # Every messy claim also gets rendering difficulty, so the scanned
    # slice is big enough to break down. Scanned is weighted double in
    # the pool because the scanned-vs-text split is a first-class cut.
    weighted_traits = RENDERING_TRAITS + ["scanned"]
    for spec in messy:
        for trait in dict.fromkeys(rng.sample(weighted_traits, rng.choice([1, 1, 2]))):
            if trait not in spec.traits:
                spec.traits.append(trait)

    files_dir = out / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    # Regeneration must not leave stale files behind: filenames come from
    # the rng stream, so a changed trait rule renames files. Clear only
    # regular files directly inside the dataset's own files directory.
    for stale in files_dir.iterdir():
        if stale.is_file():
            stale.unlink()
    claims = []
    checks: list[dict] = []
    for spec in specs:
        entries = []
        for doc in spec.docs:
            suffix = ".jpg" if doc.document_type == "damage_photo" else ".pdf"
            filename = f"doc_{rng.getrandbits(32):08x}{suffix}"
            entry, check = _render_doc(rng, spec, doc, spec.traits, files_dir / filename, pool)
            entries.append(entry)
            if check is not None:
                checks.append(check)
        claim_entry: dict = {
            "claim_id": spec.claim_id,
            "loss_type": spec.loss_type,
            "messy": bool(spec.traits),
            "traits": spec.traits,
            "files": entries,
            "fields": spec.fields,
            "expected_route": spec.expected_route,
            "expected_reasons": spec.expected_reasons,
        }
        if spec.accepted:
            claim_entry["accepted_values"] = spec.accepted
        if spec.mismatch_photo is not None:
            claim_entry["photo_mismatch"] = True
            claim_entry["mismatch_photo_depicts"] = spec.mismatch_photo
        claims.append(claim_entry)
    check_legibility(checks)
    labels: dict = {"seed": seed, "count": count, "claims": claims}
    if version != 1:
        # Version 1 labels stay byte-identical to the eval dataset, so
        # the version marker only appears on newer datasets.
        labels["dataset_version"] = version
    (out / "labels.json").write_text(json.dumps(labels, indent=2, default=str))
    (out / "photo_sources.json").write_text((ASSETS_DIR / "photo_sources.json").read_text())
    return _breakdown(labels)


def _breakdown(labels: dict) -> dict:
    claims = labels["claims"]
    by_loss: dict[str, int] = {}
    by_route: dict[str, int] = {}
    messy = sum(1 for claim in claims if claim["messy"])
    scanned_claims = sum(1 for claim in claims if any(f["scanned"] for f in claim["files"]))
    files = [f for claim in claims for f in claim["files"]]
    scanned_files = sum(1 for f in files if f["scanned"])
    for claim in claims:
        by_loss[claim["loss_type"]] = by_loss.get(claim["loss_type"], 0) + 1
        by_route[claim["expected_route"]] = by_route.get(claim["expected_route"], 0) + 1
    by_policy: dict[str, int] = {}
    by_scan_strength: dict[str, int] = {}
    for claim in claims:
        number = claim["fields"]["policy_number"]
        if isinstance(number, str):
            by_policy[number] = by_policy.get(number, 0) + 1
    for file in files:
        strength = file.get("scan_strength")
        if strength is not None:
            by_scan_strength[strength] = by_scan_strength.get(strength, 0) + 1
    photos_by_claim: dict[str, int] = {}
    photos_by_depicts: dict[str, int] = {}
    photo_sources: dict[str, set[str]] = {}
    for claim in claims:
        for file in claim["files"]:
            if file["document_type"] != "damage_photo":
                continue
            photos_by_claim[claim["loss_type"]] = photos_by_claim.get(claim["loss_type"], 0) + 1
            depicts = file.get("depicts", claim["loss_type"])
            photos_by_depicts[depicts] = photos_by_depicts.get(depicts, 0) + 1
            photo_sources.setdefault(depicts, set()).add(file.get("source", ""))
    return {
        "claims": len(claims),
        "files": len(files),
        "by_loss_type": by_loss,
        "by_route": by_route,
        "messy": messy,
        "clean": len(claims) - messy,
        "scanned_claims": scanned_claims,
        "text_claims": len(claims) - scanned_claims,
        "scanned_files": scanned_files,
        "text_pdfs": sum(1 for f in files if not f["scanned"] and f["pages"] is not None),
        "photos": sum(1 for f in files if f["pages"] is None),
        "by_policy": by_policy,
        "by_scan_strength": by_scan_strength,
        "conflict_claims": sum(1 for claim in claims if "accepted_values" in claim),
        "photo_mismatch_claims": sum(1 for claim in claims if claim.get("photo_mismatch")),
        "photos_by_claim_loss_type": photos_by_claim,
        "photos_by_depicts": photos_by_depicts,
        "photo_pool_files_by_depicts": {
            group: sorted(sources) for group, sources in photo_sources.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the eval dataset.")
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("evals/data"))
    parser.add_argument(
        "--dataset-version",
        type=int,
        choices=[1, 2],
        default=1,
        help="1 reproduces the eval dataset; 2 includes the post-eval generator fixes",
    )
    args = parser.parse_args()
    summary = generate(args.count, args.seed, args.out, args.dataset_version)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
