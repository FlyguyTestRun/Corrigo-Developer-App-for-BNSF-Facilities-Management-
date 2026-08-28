"""Pre-job briefing: what this unit has done before, and what to bring.

Everything here is derived from recorded history — no model, no inference the
technician cannot check. That is the point. A suggestion a mechanic cannot
trace back to "this happened three times before" is a suggestion they will
ignore, correctly.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bnsf_fm.analytics.registry import dossier
from bnsf_fm.analytics.routing import suggest_parts
from bnsf_fm.models import WorkOrder, WorkOrderStatus, WorkOrderType
from bnsf_fm.store import Store

# A fault seen this many times on one unit stops being an incident and starts
# being a pattern worth naming in the briefing.
RECURRENCE_THRESHOLD = 2

# Window for "this keeps coming back", in days.
RECURRENCE_WINDOW_DAYS = 365


@dataclass
class Briefing:
    work_order: WorkOrder
    asset_label: str
    location: str | None
    manual_title: str | None
    manual_path: str | None
    prior_similar: list[dict[str, object]]
    recurring: list[tuple[str, int]]
    likely_causes: list[str]
    parts_to_stage: list[dict[str, object]]
    cautions: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "work_order": self.work_order.number,
            "title": self.work_order.title,
            "asset": self.asset_label,
            "location": self.location,
            "manual": (
                {"title": self.manual_title, "path": self.manual_path}
                if self.manual_title
                else None
            ),
            "prior_similar": self.prior_similar,
            "recurring_faults": self.recurring,
            "likely_causes": self.likely_causes,
            "parts_to_stage": self.parts_to_stage,
            "cautions": self.cautions,
        }

    def render(self) -> str:
        lines = [
            f"WO {self.work_order.number} — {self.work_order.title}",
            f"{self.asset_label}" + (f"  ({self.location})" if self.location else ""),
            "",
        ]
        if self.likely_causes:
            lines.append("Likely cause, from this unit's history:")
            lines.extend(f"  - {c}" for c in self.likely_causes)
            lines.append("")
        if self.parts_to_stage:
            lines.append("Stage before you go:")
            for p in self.parts_to_stage:
                stock = "" if p["in_stock"] else "   [OUT OF STOCK]"
                lines.append(f"  - {p['sku']} {p['name']} — {p['why']}{stock}")
            lines.append("")
        if self.prior_similar:
            lines.append("Last time this happened:")
            for prior in self.prior_similar[:3]:
                lines.append(f"  - {prior['closed']}: {prior['resolution']}")
            lines.append("")
        if self.manual_title:
            lines.append(f"Manual: {self.manual_title}  ({self.manual_path})")
        if self.cautions:
            lines.append("")
            lines.extend(f"! {c}" for c in self.cautions)
        return "\n".join(lines)


def brief(store: Store, work_order_number: str, *, now: datetime | None = None) -> Briefing:
    """Build a pre-job briefing for a work order."""
    now = now or datetime.now(UTC)
    work_orders = {wo.number: wo for wo in store.work_orders()}
    wo = work_orders.get(work_order_number)
    if wo is None:
        raise KeyError(f"No work order numbered {work_order_number!r}")

    cautions: list[str] = []
    if not wo.asset_id:
        return Briefing(
            work_order=wo,
            asset_label="(no asset linked)",
            location=None,
            manual_title=None,
            manual_path=None,
            prior_similar=[],
            recurring=[],
            likely_causes=[],
            parts_to_stage=[],
            cautions=[
                "No asset is linked to this work order — history and manual lookup "
                "are unavailable. Linking the asset is the highest-value data fix here."
            ],
        )

    d = dossier(store, wo.asset_id)
    if d is None:
        return Briefing(
            work_order=wo, asset_label=wo.asset_id, location=None, manual_title=None,
            manual_path=None, prior_similar=[], recurring=[], likely_causes=[],
            parts_to_stage=[],
            cautions=[f"Asset {wo.asset_id} is referenced but not in the registry."],
        )

    cutoff = now - timedelta(days=RECURRENCE_WINDOW_DAYS)
    history = [
        h
        for h in d.work_orders
        if h.id != wo.id and h.status is WorkOrderStatus.COMPLETED and h.closed_at
    ]

    # Same reported fault, most recent first — the strongest signal available.
    same_fault = [h for h in history if h.title == wo.title]
    prior_similar = [
        {
            "number": h.number,
            "closed": h.closed_at.date().isoformat() if h.closed_at else None,
            "resolution": h.resolution or "(no resolution recorded)",
        }
        for h in sorted(same_fault, key=lambda h: h.closed_at, reverse=True)  # type: ignore[arg-type,return-value]
    ]

    # Resolutions that recurred are the likely causes worth checking first.
    resolutions = Counter(
        h.resolution for h in same_fault if h.resolution and h.closed_at and h.closed_at >= cutoff
    )
    likely = [text for text, count in resolutions.most_common(3) if count >= 1]

    recent_same = [h for h in same_fault if h.closed_at and h.closed_at >= cutoff]
    if len(recent_same) >= RECURRENCE_THRESHOLD:
        cautions.append(
            f"This exact fault has been closed {len(recent_same)} times on this unit in the "
            f"last {RECURRENCE_WINDOW_DAYS} days — treat a like-for-like repair as suspect "
            "and look for the underlying cause."
        )

    signal = d.replacement_signal(now=now)
    if signal != "ok":
        cautions.append(f"Asset flagged: {signal}.")
    if d.asset.criticality.value == "critical":
        cautions.append("Critical asset — confirm shutdown authorization before servicing.")
    if not d.manual:
        cautions.append(
            "No manual on file for this make/model — adding one would help everyone here."
        )

    reactive_recent = sum(
        1
        for h in d.work_orders
        if h.type is WorkOrderType.REACTIVE and h.opened_at >= cutoff
    )
    if reactive_recent >= 5:
        cautions.append(
            f"{reactive_recent} reactive work orders on this unit in the last year — "
            "candidate for a PM review."
        )

    return Briefing(
        work_order=wo,
        asset_label=f"{d.asset.tag} — {d.asset.name}",
        location=d.location.label if d.location else None,
        manual_title=d.manual.title if d.manual else None,
        manual_path=d.manual.relative_path if d.manual else None,
        prior_similar=prior_similar,
        recurring=d.recurring_faults,
        likely_causes=likely,
        parts_to_stage=suggest_parts(store, wo),
        cautions=cautions,
    )
