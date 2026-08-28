"""Work order aging and the stall signal.

The central metric here is not "how many work orders are open" — Corrigo
reports that already. It is the reconciliation of **logged labor hours against
days open**, which separates a work order that is genuinely being worked from
one that is merely sitting in an open state.

A work order open 20 days with 0.5 hours logged and one open 20 days with 18
hours logged look identical on a status board. They are completely different
operational problems, and only one of them is an accountability issue.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from bnsf_fm.models import Priority, WorkOrder, WorkOrderStatus
from bnsf_fm.store import Store

# Upper bound in days for each bucket; the final bucket is open-ended.
AGING_BUCKETS: list[tuple[str, float]] = [
    ("0-3 days", 3),
    ("4-7 days", 7),
    ("8-14 days", 14),
    ("15-30 days", 30),
    ("30+ days", float("inf")),
]

# Below this many logged hours per day open, a work order is "stalled": it has
# been held long enough that someone should have touched it, and the labor
# record says nobody has. Deliberately forgiving — 0.15 h/day is roughly nine
# minutes a day, a bar any genuinely active work order clears.
STALL_HOURS_PER_DAY = 0.15

# Work orders younger than this are never flagged. New work legitimately sits
# briefly before someone picks it up, and flagging that is just noise.
STALL_MIN_AGE_DAYS = 5.0


def bucket_for(days: float) -> str:
    for label, upper in AGING_BUCKETS:
        if days <= upper:
            return label
    return AGING_BUCKETS[-1][0]


@dataclass
class StalledWorkOrder:
    """An open work order with implausibly little labor logged against it."""

    work_order: WorkOrder
    days_open: float
    hours_logged: float
    hours_per_day: float
    assigned_to: str | None

    @property
    def severity(self) -> str:
        """How hard this one should land on the review list."""
        if self.days_open >= 30 or self.work_order.priority is Priority.EMERGENCY:
            return "severe"
        if self.days_open >= 14 or self.work_order.priority is Priority.HIGH:
            return "moderate"
        return "watch"

    def to_dict(self, *, reveal_names: bool = False) -> dict[str, object]:
        return {
            "number": self.work_order.number,
            "title": self.work_order.title,
            "status": str(self.work_order.status),
            "priority": str(self.work_order.priority),
            "days_open": round(self.days_open, 1),
            "hours_logged": round(self.hours_logged, 2),
            "hours_per_day": round(self.hours_per_day, 3),
            "severity": self.severity,
            "assigned_to": self.assigned_to if reveal_names else None,
        }


@dataclass
class AgingReport:
    generated_at: datetime
    total_open: int
    buckets: dict[str, int]
    by_priority: dict[str, int]
    by_status: dict[str, int]
    sla_breached: int
    stalled: list[StalledWorkOrder]
    median_days_open: float
    oldest_days_open: float

    @property
    def stalled_hours_at_risk(self) -> float:
        """Total days-open sitting in stalled work orders.

        A blunt but honest exposure number: how much calendar time the backlog
        is holding in work orders nobody is actually touching.
        """
        return round(sum(s.days_open for s in self.stalled), 1)

    def to_dict(self, *, reveal_names: bool = False) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "total_open": self.total_open,
            "buckets": self.buckets,
            "by_priority": self.by_priority,
            "by_status": self.by_status,
            "sla_breached": self.sla_breached,
            "median_days_open": round(self.median_days_open, 1),
            "oldest_days_open": round(self.oldest_days_open, 1),
            "stalled_count": len(self.stalled),
            "stalled_days_at_risk": self.stalled_hours_at_risk,
            "stalled": [s.to_dict(reveal_names=reveal_names) for s in self.stalled],
        }


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def find_stalled(
    work_orders: list[WorkOrder],
    labor_by_wo: dict[str, float],
    *,
    now: datetime | None = None,
    threshold: float = STALL_HOURS_PER_DAY,
    min_age_days: float = STALL_MIN_AGE_DAYS,
) -> list[StalledWorkOrder]:
    """Open work orders whose logged labor cannot account for their age.

    Returned worst-first: longest open at the top, because that is the order a
    supervisor works the list in.
    """
    now = now or datetime.now(UTC)
    out: list[StalledWorkOrder] = []
    for wo in work_orders:
        if not wo.status.is_open:
            continue
        days = wo.age_days(now=now)
        if days < min_age_days:
            continue
        hours = labor_by_wo.get(wo.id, 0.0)
        rate = hours / days if days else 0.0
        if rate < threshold:
            out.append(
                StalledWorkOrder(
                    work_order=wo,
                    days_open=days,
                    hours_logged=hours,
                    hours_per_day=rate,
                    assigned_to=wo.assigned_to,
                )
            )
    out.sort(key=lambda s: s.days_open, reverse=True)
    return out


def build_report(store: Store, *, now: datetime | None = None) -> AgingReport:
    now = now or datetime.now(UTC)
    open_wos = [wo for wo in store.work_orders(open_only=True)]
    labor = store.labor_hours_by_work_order()

    ages = [wo.age_days(now=now) for wo in open_wos]
    buckets = Counter(bucket_for(d) for d in ages)

    return AgingReport(
        generated_at=now,
        total_open=len(open_wos),
        # Preserve declared bucket order rather than Counter's insertion order.
        buckets={label: buckets.get(label, 0) for label, _ in AGING_BUCKETS},
        by_priority=dict(Counter(str(wo.priority) for wo in open_wos)),
        by_status=dict(Counter(str(wo.status) for wo in open_wos)),
        sla_breached=sum(1 for wo in open_wos if wo.breached_sla(now=now)),
        stalled=find_stalled(open_wos, labor, now=now),
        median_days_open=_median(ages),
        oldest_days_open=max(ages, default=0.0),
    )


def on_hold_abuse(store: Store, *, now: datetime | None = None, days: float = 21) -> list[WorkOrder]:
    """Work orders parked in ON_HOLD past the point of plausibility.

    ON_HOLD is legitimate — waiting on parts, waiting on a vendor, waiting on a
    shutdown window. It becomes an accountability problem when nothing ever
    takes it off hold, which is what this surfaces.
    """
    now = now or datetime.now(UTC)
    return sorted(
        (
            wo
            for wo in store.work_orders(open_only=True)
            if wo.status is WorkOrderStatus.ON_HOLD and wo.age_days(now=now) > days
        ),
        key=lambda wo: wo.age_days(now=now),
        reverse=True,
    )
