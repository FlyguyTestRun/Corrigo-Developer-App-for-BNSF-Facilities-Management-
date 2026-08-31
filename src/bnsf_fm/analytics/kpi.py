"""Team and technician performance metrics.

Labels come straight off the stored record: your name for you, "Tech N" for
everyone else. There is no reveal switch, because under ingest-time
anonymization there is nothing to reveal — a co-worker's name was never
written to the database. See `bnsf_fm.ingest.anonymize`.

Metrics are deliberately volume-normalized. Raw counts punish whoever gets
handed the hard work; median cycle time and SLA-met rate do not.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bnsf_fm.models import Technician, WorkOrder, WorkOrderStatus, WorkOrderType
from bnsf_fm.store import Store


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


@dataclass
class TechnicianKpi:
    technician_id: str
    label: str
    is_self: bool
    trade: str | None
    completed: int
    open_now: int
    median_cycle_days: float
    hours_logged: float
    sla_met_rate: float
    stalled_open: int

    @property
    def hours_per_completed(self) -> float:
        return round(self.hours_logged / self.completed, 2) if self.completed else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "technician": self.label,
            "is_self": self.is_self,
            "trade": self.trade,
            "completed": self.completed,
            "open_now": self.open_now,
            "median_cycle_days": round(self.median_cycle_days, 2),
            "hours_logged": round(self.hours_logged, 1),
            "hours_per_completed": self.hours_per_completed,
            "sla_met_rate": round(self.sla_met_rate, 3),
            "stalled_open": self.stalled_open,
        }


@dataclass
class TeamKpi:
    window_days: int
    completed: int
    opened: int
    median_cycle_days: float
    sla_met_rate: float
    preventive_share: float
    first_time_fix_rate: float
    backlog_open: int
    backlog_growth: int
    technicians: list[TechnicianKpi]

    def to_dict(self) -> dict[str, object]:
        return {
            "window_days": self.window_days,
            "completed": self.completed,
            "opened": self.opened,
            "median_cycle_days": round(self.median_cycle_days, 2),
            "sla_met_rate": round(self.sla_met_rate, 3),
            "preventive_share": round(self.preventive_share, 3),
            "first_time_fix_rate": round(self.first_time_fix_rate, 3),
            "backlog_open": self.backlog_open,
            "backlog_growth": self.backlog_growth,
            "technicians": [t.to_dict() for t in self.technicians],
        }


def first_time_fix_rate(
    work_orders: list[WorkOrder], *, repeat_window_days: int = 30
) -> float:
    """Share of completed reactive work that did not recur on the same asset.

    A repeat is a second reactive work order on the same asset within
    `repeat_window_days` of a completion. This is the standard FM definition
    and it is the metric that distinguishes fixing a fault from resetting it.
    """
    completed = sorted(
        (
            wo
            for wo in work_orders
            if wo.status is WorkOrderStatus.COMPLETED
            and wo.type is WorkOrderType.REACTIVE
            and wo.asset_id
            and wo.closed_at
        ),
        key=lambda wo: wo.closed_at,  # type: ignore[arg-type,return-value]
    )
    if not completed:
        return 0.0

    by_asset: dict[str, list[WorkOrder]] = defaultdict(list)
    for wo in completed:
        by_asset[wo.asset_id].append(wo)  # type: ignore[index]

    window = timedelta(days=repeat_window_days)
    repeats = 0
    for asset_wos in by_asset.values():
        for earlier, later in zip(asset_wos, asset_wos[1:], strict=False):
            assert earlier.closed_at and later.closed_at
            if later.closed_at - earlier.closed_at <= window:
                repeats += 1
    return 1.0 - (repeats / len(completed))


def build_report(
    store: Store,
    *,
    window_days: int = 90,
    now: datetime | None = None,
) -> TeamKpi:
    """Team and per-technician KPIs over a trailing window."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=window_days)

    all_wos = store.work_orders()
    labor_entries = store.labor_entries()
    techs: dict[str, Technician] = {t.id: t for t in store.technicians()}

    in_window = [wo for wo in all_wos if wo.opened_at >= cutoff]
    closed_in_window = [
        wo
        for wo in all_wos
        if wo.status is WorkOrderStatus.COMPLETED and wo.closed_at and wo.closed_at >= cutoff
    ]
    open_now = [wo for wo in all_wos if wo.status.is_open]

    cycles = [wo.age_days(now=now) for wo in closed_in_window]
    sla_met = sum(1 for wo in closed_in_window if not wo.breached_sla(now=now))

    # Backlog growth: opened minus closed in the window. Positive means the
    # team is falling behind regardless of how fast individual jobs close.
    backlog_growth = len(in_window) - len(closed_in_window)

    preventive = sum(
        1 for wo in in_window if wo.type in (WorkOrderType.PREVENTIVE, WorkOrderType.INSPECTION)
    )

    # Per-technician rollups.
    hours_by_tech: dict[str, float] = defaultdict(float)
    for entry in labor_entries:
        if entry.logged_at >= cutoff:
            hours_by_tech[entry.technician_id] += entry.hours

    from bnsf_fm.analytics.aging import find_stalled  # local import: avoids cycle

    stalled_by_tech: dict[str, int] = defaultdict(int)
    for stalled in find_stalled(open_now, store.labor_hours_by_work_order(), now=now):
        if stalled.assigned_to:
            stalled_by_tech[stalled.assigned_to] += 1

    closed_by_tech: dict[str, list[WorkOrder]] = defaultdict(list)
    for wo in closed_in_window:
        if wo.assigned_to:
            closed_by_tech[wo.assigned_to].append(wo)
    open_by_tech: dict[str, int] = defaultdict(int)
    for wo in open_now:
        if wo.assigned_to:
            open_by_tech[wo.assigned_to] += 1

    tech_ids = set(closed_by_tech) | set(open_by_tech) | set(hours_by_tech)
    tech_kpis: list[TechnicianKpi] = []
    for tech_id in sorted(tech_ids):
        tech = techs.get(tech_id) or Technician(id=tech_id)
        done = closed_by_tech.get(tech_id, [])
        met = sum(1 for wo in done if not wo.breached_sla(now=now))
        tech_kpis.append(
            TechnicianKpi(
                technician_id=tech_id,
                label=tech.display_name(),
                is_self=tech.is_self,
                trade=tech.trade,
                completed=len(done),
                open_now=open_by_tech.get(tech_id, 0),
                median_cycle_days=_median([wo.age_days(now=now) for wo in done]),
                hours_logged=hours_by_tech.get(tech_id, 0.0),
                sla_met_rate=(met / len(done)) if done else 0.0,
                stalled_open=stalled_by_tech.get(tech_id, 0),
            )
        )
    tech_kpis.sort(key=lambda t: t.completed, reverse=True)

    return TeamKpi(
        window_days=window_days,
        completed=len(closed_in_window),
        opened=len(in_window),
        median_cycle_days=_median(cycles),
        sla_met_rate=(sla_met / len(closed_in_window)) if closed_in_window else 0.0,
        preventive_share=(preventive / len(in_window)) if in_window else 0.0,
        first_time_fix_rate=first_time_fix_rate(all_wos),
        backlog_open=len(open_now),
        backlog_growth=backlog_growth,
        technicians=tech_kpis,
    )
