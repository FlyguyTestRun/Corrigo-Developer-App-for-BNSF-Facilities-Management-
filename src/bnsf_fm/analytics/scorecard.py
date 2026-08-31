"""You versus the department.

Answers the question a promotion case actually needs: what share of the
department's completed work did you do, and how fast, measured against the
group rather than against nothing.

Builds on `kpi.build_report` rather than recomputing — the per-technician
completions, median cycle time, SLA rate and hours are already there. This adds
the comparison: shares, ranks, percentiles, and the even-split baseline that
makes a share number mean something.

**An honesty rail is built into the output, not left to the reader.** Raw
volume partly reflects how work is assigned, which a mechanic does not control.
Cycle time and SLA-met rate are the defensible numbers, and `caveats` says so
on the report itself. Handing a manager a volume number with no such note is
the version of this that invites the one objection that sinks it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from bnsf_fm.analytics import kpi
from bnsf_fm.analytics.kpi import TeamKpi, TechnicianKpi
from bnsf_fm.store import Store


def _percentile_rank(values: list[float], value: float, *, higher_is_better: bool) -> float:
    """Share of peers this value beats, 0-1.

    Uses the standard "less than plus half equal" definition, so ties do not
    hand you credit for beating someone you matched.
    """
    if not values:
        return 0.0
    if higher_is_better:
        below = sum(1 for v in values if v < value)
        equal = sum(1 for v in values if v == value)
    else:
        below = sum(1 for v in values if v > value)
        equal = sum(1 for v in values if v == value)
    return (below + 0.5 * equal) / len(values)


@dataclass
class Comparison:
    """One metric, you against the group."""

    metric: str
    mine: float
    department_median: float
    rank: int
    of: int
    percentile: float
    higher_is_better: bool
    unit: str = ""

    @property
    def delta(self) -> float:
        return self.mine - self.department_median

    @property
    def favorable(self) -> bool:
        return self.delta >= 0 if self.higher_is_better else self.delta <= 0

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "mine": round(self.mine, 2),
            "department_median": round(self.department_median, 2),
            "delta": round(self.delta, 2),
            "rank": self.rank,
            "of": self.of,
            "percentile": round(self.percentile, 3),
            "favorable": self.favorable,
            "higher_is_better": self.higher_is_better,
            "unit": self.unit,
        }


@dataclass
class Scorecard:
    generated_at: datetime
    window_days: int
    me: str
    department_size: int
    my_completed: int
    department_completed: int
    my_share: float
    even_split_share: float
    my_open_now: int
    my_hours: float
    comparisons: list[Comparison]
    caveats: list[str] = field(default_factory=list)
    team: TeamKpi | None = None

    @property
    def share_vs_even(self) -> float:
        """How many times an equal share you completed. 1.0 is exactly par."""
        return self.my_share / self.even_split_share if self.even_split_share else 0.0

    def comparison(self, metric: str) -> Comparison | None:
        return next((c for c in self.comparisons if c.metric == metric), None)

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "window_days": self.window_days,
            "me": self.me,
            "department_size": self.department_size,
            "my_completed": self.my_completed,
            "department_completed": self.department_completed,
            "my_share": round(self.my_share, 4),
            "even_split_share": round(self.even_split_share, 4),
            "share_vs_even": round(self.share_vs_even, 2),
            "my_open_now": self.my_open_now,
            "my_hours": round(self.my_hours, 1),
            "comparisons": [c.to_dict() for c in self.comparisons],
            "caveats": self.caveats,
        }


class NoSelfIdentified(RuntimeError):
    """No technician in the store is marked as the person running the tool."""


def build(
    store: Store,
    *,
    window_days: int = 365,
    now: datetime | None = None,
) -> Scorecard:
    """Compare the current user against the rest of the department.

    Raises `NoSelfIdentified` when no load has identified you — that is a
    configuration problem (`--me` was never passed, or its spelling did not
    match the export), and a scorecard with no "you" in it would be a confusing
    way to discover it.
    """
    now = now or datetime.now(UTC)
    team = kpi.build_report(store, window_days=window_days, now=now)

    mine: TechnicianKpi | None = next((t for t in team.technicians if t.is_self), None)
    if mine is None:
        raise NoSelfIdentified(
            "No technician is marked as you. Load with "
            '`--me "<your name or employee id as it appears in the export>"`, '
            "then re-run. See docs/exporting-from-corrigo.md."
        )

    others = [t for t in team.technicians if not t.is_self]
    # Only count people who actually completed work in the window as the
    # department. Including dormant accounts would inflate your share by
    # shrinking the denominator, which is exactly the kind of flattering
    # arithmetic a reviewer would catch.
    active = [t for t in team.technicians if t.completed > 0]
    department_size = max(len(active), 1)

    department_completed = sum(t.completed for t in team.technicians)
    my_share = (mine.completed / department_completed) if department_completed else 0.0
    even_split = 1.0 / department_size

    def compare(
        metric: str,
        value: float,
        peer_values: list[float],
        *,
        higher_is_better: bool,
        unit: str = "",
    ) -> Comparison:
        population = [*peer_values, value]
        ordered = sorted(population, reverse=higher_is_better)
        return Comparison(
            metric=metric,
            mine=value,
            department_median=_median(peer_values),
            rank=ordered.index(value) + 1,
            of=len(population),
            percentile=_percentile_rank(peer_values, value, higher_is_better=higher_is_better),
            higher_is_better=higher_is_better,
            unit=unit,
        )

    # Cycle time is only meaningful for people who closed something.
    peers_with_work = [t for t in others if t.completed > 0]

    comparisons = [
        compare(
            "Completed work orders",
            float(mine.completed),
            [float(t.completed) for t in others],
            higher_is_better=True,
        ),
        compare(
            "Median days to completion",
            mine.median_cycle_days,
            [t.median_cycle_days for t in peers_with_work],
            higher_is_better=False,
            unit="days",
        ),
        compare(
            "SLA met rate",
            mine.sla_met_rate,
            [t.sla_met_rate for t in peers_with_work],
            higher_is_better=True,
            unit="%",
        ),
        compare(
            "Open work orders now",
            float(mine.open_now),
            [float(t.open_now) for t in others],
            higher_is_better=False,
        ),
        compare(
            "Stalled open work orders",
            float(mine.stalled_open),
            [float(t.stalled_open) for t in others],
            higher_is_better=False,
        ),
    ]

    caveats = [
        "Completed volume partly reflects how work is assigned, which a technician "
        "does not control. Median days to completion and SLA-met rate are the "
        "defensible comparisons.",
        f"Department here means the {department_size} technicians who completed at "
        f"least one work order in the last {window_days} days, as seen in this export.",
    ]
    if not peers_with_work:
        caveats.append(
            "No other technician appears in this data, so every comparison below is "
            "against an empty group. The export likely covered only your own work "
            "orders — see docs/exporting-from-corrigo.md on checking the Assigned To "
            "column."
        )
    if mine.hours_logged == 0:
        caveats.append(
            "No labor hours were loaded, so hours-per-job and stall detection are "
            "unavailable. A labor or timesheet export would add them."
        )

    return Scorecard(
        generated_at=now,
        window_days=window_days,
        me=mine.label,
        department_size=department_size,
        my_completed=mine.completed,
        department_completed=department_completed,
        my_share=my_share,
        even_split_share=even_split,
        my_open_now=mine.open_now,
        my_hours=mine.hours_logged,
        comparisons=comparisons,
        caveats=caveats,
        team=team,
    )


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2
