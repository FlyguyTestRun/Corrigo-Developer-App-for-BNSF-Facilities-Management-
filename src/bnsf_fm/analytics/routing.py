"""Route sequencing for a day's work orders.

Deliberately simple: all-pairs shortest paths over a small building graph
(Floyd-Warshall, trivial at campus scale) followed by a greedy nearest-next
walk with a priority/SLA pull. A full VRP solver would be worse here — it needs
travel data nobody has measured, and it produces routes a technician will
ignore. The value is in "do these three while you're already in the plant",
which greedy sequencing captures.

Within a building the cost model prefers staying on the same floor, which is
where most of the real walking time goes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from bnsf_fm.models import Location, Priority, WorkOrder
from bnsf_fm.store import Store

# Minutes of "walking" cost charged for moves inside one building.
SAME_FLOOR_MINUTES = 1.0
DIFFERENT_FLOOR_MINUTES = 3.0
# Fallback when two buildings have no path in the graph — high enough to sort
# unreachable buildings last without poisoning the arithmetic with infinity.
UNCONNECTED_MINUTES = 30.0

# How many minutes of walking the router will trade away to pull an urgent job
# forward. Emergency work should not wait behind a convenient nearby job.
PRIORITY_PULL_MINUTES: dict[Priority, float] = {
    Priority.EMERGENCY: 90.0,
    Priority.HIGH: 25.0,
    Priority.MEDIUM: 5.0,
    Priority.LOW: 0.0,
}

# Extra pull for work already past SLA — it is not more urgent by policy, but
# every additional hour is a breach getting worse.
BREACH_PULL_MINUTES = 20.0


@dataclass
class Stop:
    work_order: WorkOrder
    location: Location | None
    travel_minutes: float
    cumulative_minutes: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "number": self.work_order.number,
            "title": self.work_order.title,
            "priority": str(self.work_order.priority),
            "status": str(self.work_order.status),
            "location": self.location.label if self.location else None,
            "travel_minutes": round(self.travel_minutes, 1),
            "cumulative_minutes": round(self.cumulative_minutes, 1),
            "reason": self.reason,
        }


@dataclass
class Route:
    stops: list[Stop]
    start_building: str
    total_travel_minutes: float
    unrouted: list[WorkOrder]

    def to_dict(self) -> dict[str, object]:
        return {
            "start_building": self.start_building,
            "stop_count": len(self.stops),
            "total_travel_minutes": round(self.total_travel_minutes, 1),
            "stops": [s.to_dict() for s in self.stops],
            "unrouted": [wo.number for wo in self.unrouted],
        }


def shortest_paths(edges: dict[tuple[str, str], float]) -> dict[tuple[str, str], float]:
    """All-pairs shortest walking time between buildings (Floyd-Warshall)."""
    nodes = {b for pair in edges for b in pair}
    dist: dict[tuple[str, str], float] = {}
    for a in nodes:
        for b in nodes:
            if a == b:
                dist[(a, b)] = 0.0
            else:
                dist[(a, b)] = edges.get((a, b), math.inf)
    for k in nodes:
        for i in nodes:
            ik = dist[(i, k)]
            if ik == math.inf:
                continue
            for j in nodes:
                through = ik + dist[(k, j)]
                if through < dist[(i, j)]:
                    dist[(i, j)] = through
    return dist


class Router:
    def __init__(self, store: Store) -> None:
        self.locations: dict[str, Location] = {loc.id: loc for loc in store.locations()}
        self.distances = shortest_paths(store.campus_edges())

    def travel_minutes(self, origin: Location | None, dest: Location | None) -> float:
        if origin is None or dest is None:
            return UNCONNECTED_MINUTES
        if origin.building == dest.building:
            return SAME_FLOOR_MINUTES if origin.floor == dest.floor else DIFFERENT_FLOOR_MINUTES
        cost = self.distances.get((origin.building, dest.building), math.inf)
        return UNCONNECTED_MINUTES if cost == math.inf else cost

    def _score(
        self, origin: Location | None, wo: WorkOrder, now: datetime
    ) -> tuple[float, float]:
        """(effective cost, raw travel minutes). Lower cost is chosen first."""
        travel = self.travel_minutes(origin, self.locations.get(wo.location_id or ""))
        pull = PRIORITY_PULL_MINUTES.get(wo.priority, 0.0)
        if wo.breached_sla(now=now):
            pull += BREACH_PULL_MINUTES
        return travel - pull, travel

    def plan(
        self,
        work_orders: list[WorkOrder],
        *,
        start_building: str,
        now: datetime | None = None,
        max_stops: int | None = None,
    ) -> Route:
        """Sequence `work_orders` into a walking route from `start_building`."""
        now = now or datetime.now(UTC)
        remaining = [wo for wo in work_orders if wo.status.is_open]
        unrouted = [wo for wo in remaining if not wo.location_id]
        remaining = [wo for wo in remaining if wo.location_id]

        # Synthetic origin: a location standing for "wherever you start".
        current: Location | None = Location(id="__start__", building=start_building)
        stops: list[Stop] = []
        cumulative = 0.0

        while remaining and (max_stops is None or len(stops) < max_stops):
            scored = [(self._score(current, wo, now), wo) for wo in remaining]
            (cost, travel), chosen = min(scored, key=lambda pair: (pair[0][0], pair[1].number))
            cumulative += travel
            dest = self.locations.get(chosen.location_id or "")
            stops.append(
                Stop(
                    work_order=chosen,
                    location=dest,
                    travel_minutes=travel,
                    cumulative_minutes=cumulative,
                    reason=self._reason(chosen, travel, cost, now),
                )
            )
            remaining.remove(chosen)
            current = dest or current

        unrouted.extend(remaining)
        return Route(
            stops=stops,
            start_building=start_building,
            total_travel_minutes=cumulative,
            unrouted=unrouted,
        )

    @staticmethod
    def _reason(wo: WorkOrder, travel: float, cost: float, now: datetime) -> str:
        if wo.priority is Priority.EMERGENCY:
            return "emergency — goes first regardless of distance"
        if wo.breached_sla(now=now):
            over_hours = wo.age_hours(now=now) - wo.priority.sla_hours
            over = (
                f"{over_hours:.0f}h" if over_hours < 48 else f"{over_hours / 24:.0f} days"
            )
            return f"past SLA by {over}"
        if travel <= SAME_FLOOR_MINUTES:
            return "same floor as previous stop"
        if travel <= DIFFERENT_FLOOR_MINUTES:
            return "same building, different floor"
        if cost < travel:
            return f"{wo.priority} priority pulled forward"
        return f"nearest remaining stop ({travel:.0f} min walk)"


def suggest_parts(store: Store, work_order: WorkOrder, *, limit: int = 5) -> list[dict[str, object]]:
    """Parts worth staging before walking out to this work order.

    Ranked by how often each part was consumed on prior work against the same
    asset, then against the same asset category. Purely historical — no model
    involved — which makes it explainable to a technician who asks why.
    """
    if not work_order.asset_id:
        return []
    asset = store.asset(work_order.asset_id)
    if asset is None:
        return []

    usage = store.part_usage()
    parts = {p.id: p for p in store.parts()}
    wos = {wo.id: wo for wo in store.work_orders()}
    assets = {a.id: a for a in store.assets()}

    same_asset: dict[str, int] = {}
    same_category: dict[str, int] = {}
    for use in usage:
        wo = wos.get(use.work_order_id)
        if not wo or not wo.asset_id:
            continue
        if wo.asset_id == asset.id:
            same_asset[use.part_id] = same_asset.get(use.part_id, 0) + use.quantity
        other = assets.get(wo.asset_id)
        if other and other.category == asset.category:
            same_category[use.part_id] = same_category.get(use.part_id, 0) + use.quantity

    scored: list[tuple[float, str, str]] = []
    for part_id in set(same_asset) | set(same_category):
        # This exact unit's history counts for far more than the category's.
        score = same_asset.get(part_id, 0) * 10 + same_category.get(part_id, 0)
        basis = (
            f"used {same_asset[part_id]}x on this unit"
            if part_id in same_asset
            else f"common on {asset.category} work"
        )
        scored.append((score, part_id, basis))
    scored.sort(key=lambda t: (-t[0], t[1]))

    out: list[dict[str, object]] = []
    for score, part_id, basis in scored[:limit]:
        part = parts.get(part_id)
        if not part:
            continue
        out.append(
            {
                "sku": part.sku,
                "name": part.name,
                "on_hand": part.on_hand,
                "in_stock": part.on_hand > 0,
                "score": score,
                "why": basis,
            }
        )
    return out
