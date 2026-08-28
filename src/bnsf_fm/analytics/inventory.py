"""Parts and consumables.

Consumption is derived from part usage recorded against closed work orders, so
burn rate is grounded in work actually performed rather than in a stock count
someone remembered to update. Days-of-cover falls out of that, and days-of-cover
is the number that tells you whether a reorder point is set sensibly.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bnsf_fm.models import Part
from bnsf_fm.store import Store


@dataclass
class PartStatus:
    part: Part
    consumed_in_window: int
    daily_burn: float
    days_of_cover: float | None
    needs_reorder: bool
    suggested_reorder_point: int

    @property
    def flag(self) -> str:
        if self.part.on_hand == 0:
            return "out of stock"
        if self.needs_reorder:
            return "at or below reorder point"
        if self.days_of_cover is not None and self.days_of_cover < 14:
            return "under two weeks of cover"
        return "ok"

    def to_dict(self) -> dict[str, object]:
        return {
            "sku": self.part.sku,
            "name": self.part.name,
            "on_hand": self.part.on_hand,
            "reorder_point": self.part.reorder_point,
            "suggested_reorder_point": self.suggested_reorder_point,
            "consumed_in_window": self.consumed_in_window,
            "daily_burn": round(self.daily_burn, 3),
            "days_of_cover": (
                round(self.days_of_cover, 1) if self.days_of_cover is not None else None
            ),
            "flag": self.flag,
            "reorder_cost": (
                round(self.part.reorder_quantity * self.part.unit_cost, 2)
                if self.part.unit_cost
                else None
            ),
        }


@dataclass
class InventoryReport:
    window_days: int
    parts: list[PartStatus]

    @property
    def reorder_now(self) -> list[PartStatus]:
        return [p for p in self.parts if p.needs_reorder]

    @property
    def reorder_cost(self) -> float:
        return round(
            sum(
                p.part.reorder_quantity * (p.part.unit_cost or 0.0)
                for p in self.reorder_now
            ),
            2,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "window_days": self.window_days,
            "part_count": len(self.parts),
            "reorder_count": len(self.reorder_now),
            "reorder_cost": self.reorder_cost,
            "parts": [p.to_dict() for p in self.parts],
        }


# Cover the lead time plus a safety margin. Two weeks is the working assumption
# for campus supply; a site with a different lead time changes this one number.
LEAD_TIME_DAYS = 14
SAFETY_FACTOR = 1.5


def build_report(
    store: Store, *, window_days: int = 180, now: datetime | None = None
) -> InventoryReport:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=window_days)

    consumed: dict[str, int] = defaultdict(int)
    for use in store.part_usage():
        if use.used_at >= cutoff:
            consumed[use.part_id] += use.quantity

    statuses: list[PartStatus] = []
    for part in store.parts():
        used = consumed.get(part.id, 0)
        burn = used / window_days if window_days else 0.0
        cover = (part.on_hand / burn) if burn > 0 else None
        # Round up so a slow-moving part still carries at least one unit of
        # buffer rather than a reorder point of zero.
        suggested = max(1, round(burn * LEAD_TIME_DAYS * SAFETY_FACTOR)) if burn > 0 else 0
        statuses.append(
            PartStatus(
                part=part,
                consumed_in_window=used,
                daily_burn=burn,
                days_of_cover=cover,
                needs_reorder=part.needs_reorder,
                suggested_reorder_point=suggested,
            )
        )

    # Most urgent first: out of stock, then thinnest cover.
    statuses.sort(
        key=lambda s: (
            s.part.on_hand > 0,
            s.days_of_cover if s.days_of_cover is not None else float("inf"),
        )
    )
    return InventoryReport(window_days=window_days, parts=statuses)
