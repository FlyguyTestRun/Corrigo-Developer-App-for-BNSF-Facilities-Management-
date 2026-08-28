"""Asset registry: one asset, everything known about it.

This is the view a technician actually wants standing in front of a unit —
what it is, where it is, what the manual says, what has broken before, and what
was done about it last time. Everything else in this project reads off the
registry.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from bnsf_fm.models import Asset, Location, Manual, WorkOrder, WorkOrderStatus, WorkOrderType
from bnsf_fm.store import Store


@dataclass
class AssetDossier:
    asset: Asset
    location: Location | None
    manual: Manual | None
    work_orders: list[WorkOrder]
    total_labor_hours: float

    @property
    def open_work_orders(self) -> list[WorkOrder]:
        return [wo for wo in self.work_orders if wo.status.is_open]

    @property
    def reactive_count(self) -> int:
        return sum(1 for wo in self.work_orders if wo.type is WorkOrderType.REACTIVE)

    @property
    def recurring_faults(self) -> list[tuple[str, int]]:
        """Fault titles seen more than once on this unit, most frequent first."""
        counts = Counter(
            wo.title for wo in self.work_orders if wo.type is WorkOrderType.REACTIVE
        )
        return [(title, n) for title, n in counts.most_common() if n > 1]

    def age_years(self, *, now: datetime | None = None) -> float | None:
        if not self.asset.installed_on:
            return None
        now = now or datetime.now(UTC)
        return (now - self.asset.installed_on).days / 365.25

    def replacement_signal(self, *, now: datetime | None = None) -> str:
        """A plain repair-versus-replace read.

        Not a financial model — a triage flag. The real repair-vs-replace
        calculation needs replacement cost and labor rates the pilot does not
        have yet; this says which units are worth running that calculation on.
        """
        age = self.age_years(now=now)
        life = self.asset.expected_life_years
        past_life = age is not None and life is not None and age > life
        heavy_repair = self.reactive_count >= 6
        if past_life and heavy_repair:
            return "replace — past expected life and repair-heavy"
        if past_life:
            return "review — past expected life"
        if heavy_repair:
            return "review — repair frequency elevated"
        return "ok"

    def to_dict(self, *, now: datetime | None = None) -> dict[str, object]:
        now = now or datetime.now(UTC)
        age = self.age_years(now=now)
        closed = [wo for wo in self.work_orders if wo.status is WorkOrderStatus.COMPLETED]
        return {
            "tag": self.asset.tag,
            "name": self.asset.name,
            "category": self.asset.category,
            "criticality": str(self.asset.criticality),
            "location": self.location.label if self.location else None,
            "manufacturer": self.asset.manufacturer,
            "model": self.asset.model,
            "serial": self.asset.serial,
            "age_years": round(age, 1) if age is not None else None,
            "expected_life_years": self.asset.expected_life_years,
            "manual": (
                {"title": self.manual.title, "path": self.manual.relative_path}
                if self.manual
                else None
            ),
            "work_order_count": len(self.work_orders),
            "open_work_orders": len(self.open_work_orders),
            "reactive_count": self.reactive_count,
            "total_labor_hours": round(self.total_labor_hours, 1),
            "recurring_faults": self.recurring_faults,
            "replacement_signal": self.replacement_signal(now=now),
            "last_service": (
                max(wo.closed_at for wo in closed if wo.closed_at).isoformat()
                if any(wo.closed_at for wo in closed)
                else None
            ),
        }


def dossier(store: Store, asset_id: str) -> AssetDossier | None:
    asset = store.asset(asset_id)
    if asset is None:
        return None
    locations = {loc.id: loc for loc in store.locations()}
    work_orders = store.work_orders_for_asset(asset_id)
    labor = store.labor_hours_by_work_order()
    return AssetDossier(
        asset=asset,
        location=locations.get(asset.location_id),
        manual=store.manual_for_asset(asset),
        work_orders=work_orders,
        total_labor_hours=sum(labor.get(wo.id, 0.0) for wo in work_orders),
    )


def find_assets(
    store: Store,
    *,
    query: str | None = None,
    building: str | None = None,
    category: str | None = None,
) -> list[Asset]:
    """Search the registry by free text, building, or category."""
    locations = {loc.id: loc for loc in store.locations()}
    needle = query.lower().strip() if query else None
    out = []
    for asset in store.assets():
        loc = locations.get(asset.location_id)
        if building and (not loc or loc.building != building):
            continue
        if category and asset.category != category:
            continue
        if needle:
            haystack = " ".join(
                filter(
                    None,
                    [asset.tag, asset.name, asset.category, asset.manufacturer,
                     asset.model, asset.serial, loc.label if loc else None],
                )
            ).lower()
            if needle not in haystack:
                continue
        out.append(asset)
    return out


def replacement_candidates(store: Store, *, now: datetime | None = None) -> list[AssetDossier]:
    """Assets whose age and repair history warrant a repair-vs-replace review."""
    out = []
    for asset in store.assets():
        d = dossier(store, asset.id)
        if d and d.replacement_signal(now=now) != "ok":
            out.append(d)
    out.sort(key=lambda d: (d.replacement_signal().startswith("review"), -d.reactive_count))
    return out
