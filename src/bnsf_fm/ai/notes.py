"""Field notes to a structured work order update.

The single biggest time sink in a mechanic's day is retyping what they just did
into a form, in the format the CMMS wants, hours after doing it. This turns a
line of rough shorthand — "swapped contactor on ahu-12, 1.5hr, unit running" —
into a structured draft: resolution text, labor hours, parts consumed, proposed
status.

Two hard rules, both deliberate:

1. **Drafts only.** Nothing here writes to Corrigo. `WorkOrderDraft` is
   reviewed by the person who did the work and submitted by them. An AI writing
   directly into a production CMMS work order — a record that feeds client
   billing and compliance reporting — is not a defensible design, and would
   not survive review by anyone who owns that system.

2. **Deterministic fallback.** `draft_update` works with no model available and
   no network. The rule-based extractor handles the common shapes (hours,
   part SKUs, status words) so the feature degrades to useful rather than
   broken. The model, when configured, improves the prose and catches what the
   patterns miss.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bnsf_fm.models import Part, WorkOrder, WorkOrderStatus
from bnsf_fm.store import Store

# Claude model used when the AI path is enabled. Overridable per deployment.
DEFAULT_MODEL = "claude-sonnet-5"


@dataclass
class PartLine:
    sku: str
    name: str
    quantity: int
    in_stock: bool


@dataclass
class WorkOrderDraft:
    """A proposed update. Never submitted automatically."""

    work_order_number: str
    resolution: str
    labor_hours: float | None
    parts: list[PartLine] = field(default_factory=list)
    proposed_status: WorkOrderStatus | None = None
    follow_up: str | None = None
    source_note: str = ""
    generated_by: str = "rules"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "work_order": self.work_order_number,
            "resolution": self.resolution,
            "labor_hours": self.labor_hours,
            "parts": [
                {"sku": p.sku, "name": p.name, "quantity": p.quantity, "in_stock": p.in_stock}
                for p in self.parts
            ],
            "proposed_status": str(self.proposed_status) if self.proposed_status else None,
            "follow_up": self.follow_up,
            "generated_by": self.generated_by,
            "warnings": self.warnings,
            "requires_human_submission": True,
        }

    def render(self) -> str:
        """Plain-text rendering to paste into Corrigo or read back aloud."""
        lines = [f"Work order {self.work_order_number}", "", self.resolution, ""]
        if self.labor_hours is not None:
            lines.append(f"Labor: {self.labor_hours:g} h")
        if self.parts:
            lines.append("Parts used:")
            lines.extend(
                f"  - {p.quantity} x {p.sku} {p.name}"
                + ("" if p.in_stock else "  [NOT IN STOCK — verify]")
                for p in self.parts
            )
        if self.proposed_status:
            lines.append(f"Proposed status: {self.proposed_status}")
        if self.follow_up:
            lines.append(f"Follow-up: {self.follow_up}")
        if self.warnings:
            lines.append("")
            lines.extend(f"! {w}" for w in self.warnings)
        lines.extend(["", "-- draft, review before submitting --"])
        return "\n".join(lines)


# "1.5hr", "1.5 hrs", "90 min", "2h", "an hour and a half" is not handled and
# should not be — if the note is ambiguous the field is left blank for the
# technician to fill rather than guessed.
_HOURS_PATTERNS = [
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", re.I),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)\b", re.I),
]

_STATUS_HINTS: list[tuple[re.Pattern[str], WorkOrderStatus]] = [
    (re.compile(r"\b(complete[d]?|finished|done|back (?:on)?line|running|resolved|fixed)\b", re.I),
     WorkOrderStatus.COMPLETED),
    (re.compile(r"\b(wait(?:ing)? on parts|on order|need(?:s)? parts|ordered|on hold|"
                r"awaiting|vendor|shutdown window)\b", re.I),
     WorkOrderStatus.ON_HOLD),
    (re.compile(r"\b(still working|in progress|continu|return tomorrow|came back)\b", re.I),
     WorkOrderStatus.IN_PROGRESS),
]

_FOLLOW_UP = re.compile(
    r"\b(?:need to|needs|follow[- ]?up|come back|order|recommend|should)\b[^.;\n]*", re.I
)

# A quantity immediately preceding the part reference: "12 FLT-2020", "(4) ...",
# "3x ...". The parenthesized form cannot carry a leading \b — the boundary
# between a space and "(" is not a word boundary — so it is its own alternative.
_QUANTITY_NEAR_PART = re.compile(r"(?:\((\d+)\)|\b(\d+)\s*(?:x|ea\b|each\b)?)\s*$", re.I)


def extract_hours(note: str) -> float | None:
    """Pull labor hours from free text. Minutes are converted."""
    for i, pattern in enumerate(_HOURS_PATTERNS):
        match = pattern.search(note)
        if match:
            value = float(match.group(1))
            return round(value / 60.0, 2) if i == 1 else value
    return None


def extract_status(note: str) -> WorkOrderStatus | None:
    """Infer the proposed status. On-hold wins over completed when both appear.

    "Replaced the belt, unit running, but waiting on parts for the guard" is
    not a completed work order, and reading it as one is the error that hurts.
    """
    hits = [status for pattern, status in _STATUS_HINTS if pattern.search(note)]
    if WorkOrderStatus.ON_HOLD in hits:
        return WorkOrderStatus.ON_HOLD
    return hits[0] if hits else None


def extract_parts(note: str, catalog: list[Part]) -> list[PartLine]:
    """Match parts by SKU, then by distinctive name tokens.

    Matching is conservative: a part is only claimed when its SKU or its full
    name appears. Guessing "belt" onto a specific SKU would put wrong parts on
    a billable record.
    """
    found: dict[str, int] = {}
    lowered = note.lower()
    for part in catalog:
        sku_hit = re.search(rf"\b{re.escape(part.sku.lower())}\b", lowered)
        name_hit = part.name.lower() in lowered
        if not (sku_hit or name_hit):
            continue
        index = (sku_hit.start() if sku_hit else lowered.index(part.name.lower()))
        prefix = note[:index].rstrip()
        qty_match = _QUANTITY_NEAR_PART.search(prefix)
        quantity = int(next(g for g in qty_match.groups() if g)) if qty_match else 1
        found[part.id] = max(found.get(part.id, 0), quantity)

    by_id = {p.id: p for p in catalog}
    return [
        PartLine(
            sku=by_id[pid].sku,
            name=by_id[pid].name,
            quantity=qty,
            in_stock=by_id[pid].on_hand >= qty,
        )
        for pid, qty in found.items()
    ]


def _rule_based(note: str, work_order: WorkOrder, catalog: list[Part]) -> WorkOrderDraft:
    follow = _FOLLOW_UP.search(note)
    cleaned = note.strip()
    resolution = cleaned[0].upper() + cleaned[1:] if cleaned else ""
    if resolution and not resolution.endswith((".", "!", "?")):
        resolution += "."
    return WorkOrderDraft(
        work_order_number=work_order.number,
        resolution=resolution,
        labor_hours=extract_hours(note),
        parts=extract_parts(note, catalog),
        proposed_status=extract_status(note),
        follow_up=follow.group(0).strip() if follow else None,
        source_note=note,
        generated_by="rules",
    )


_PROMPT = """\
You are helping a maintenance mechanic turn rough field notes into a work order \
update for a CMMS. Be precise and factual. Do not invent work that is not \
described in the note.

Work order {number}: {title}
Asset: {asset}
Current status: {status}

Field note (verbatim):
{note}

Rewrite the note as a professional resolution entry of two or three sentences, \
in past tense, describing what was found and what was done. Use only \
information present in the note. Do not add recommendations that the note does \
not contain. Reply with the resolution text only, no preamble or labels.\
"""


def _model_resolution(
    note: str, work_order: WorkOrder, asset_name: str, model: str
) -> tuple[str | None, str | None]:
    """Ask Claude to write the resolution prose. Returns (text, warning)."""
    try:
        import anthropic
    except ImportError:
        return None, "anthropic package not installed — used rule-based draft"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, "ANTHROPIC_API_KEY not set — used rule-based draft"
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[
                {
                    "role": "user",
                    "content": _PROMPT.format(
                        number=work_order.number,
                        title=work_order.title,
                        asset=asset_name,
                        status=work_order.status,
                        note=note,
                    ),
                }
            ],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()
        return (text or None), None
    except Exception as exc:  # noqa: BLE001 - degrade to rules on any model failure
        return None, f"model call failed ({type(exc).__name__}) — used rule-based draft"


def draft_update(
    store: Store,
    work_order_number: str,
    note: str,
    *,
    use_model: bool = True,
    model: str = DEFAULT_MODEL,
    now: datetime | None = None,
) -> WorkOrderDraft:
    """Turn a field note into a reviewable work order draft.

    Falls back cleanly to rule-based extraction when no model is configured,
    so the feature is usable on a laptop with no API key.
    """
    now = now or datetime.now(UTC)
    work_orders = {wo.number: wo for wo in store.work_orders()}
    work_order = work_orders.get(work_order_number)
    if work_order is None:
        raise KeyError(f"No work order numbered {work_order_number!r}")

    catalog = store.parts()
    draft = _rule_based(note, work_order, catalog)

    # Structured fields always come from the deterministic extractor — hours
    # and parts feed labor records and stock counts, and those must be
    # reproducible and auditable, not model-generated.
    if use_model:
        asset = store.asset(work_order.asset_id) if work_order.asset_id else None
        text, warning = _model_resolution(
            note, work_order, asset.name if asset else "(unassigned)", model
        )
        if text:
            draft.resolution = text
            draft.generated_by = f"model:{model}"
        if warning:
            draft.warnings.append(warning)

    if draft.labor_hours is None:
        draft.warnings.append("no labor time found in the note — enter hours before submitting")
    for line in draft.parts:
        if not line.in_stock:
            draft.warnings.append(f"{line.sku} not in stock at recorded quantity — verify")
    if draft.proposed_status is WorkOrderStatus.COMPLETED and draft.labor_hours is None:
        draft.warnings.append("proposed complete with no labor logged — this is what stalls audits")

    return draft
