"""What the export was missing.

Two audiences, one report.

For you: the first load against a real Corrigo export is always a mapping
problem, and the failures are silent by nature — a column nobody mapped just
does not appear, and a work order dropped for an unparseable date leaves no
trace in the totals. This names them.

For the credential conversation: every line here is a capability the data does
not currently support and a reason API access would help. "We would like the
API" is a preference; "38% of work orders have no asset linked, so we cannot
build service history" is an argument.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from bnsf_fm.models import WorkOrderStatus
from bnsf_fm.store import Store


@dataclass
class Finding:
    """One gap, with the capability it costs."""

    field: str
    missing: int
    total: int
    costs: str

    @property
    def share(self) -> float:
        return self.missing / self.total if self.total else 0.0

    @property
    def severity(self) -> str:
        if self.share >= 0.5:
            return "blocking"
        if self.share >= 0.15:
            return "degraded"
        return "minor" if self.missing else "ok"

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "missing": self.missing,
            "total": self.total,
            "share": round(self.share, 3),
            "severity": self.severity,
            "costs": self.costs,
        }


@dataclass
class QualityReport:
    generated_at: datetime
    work_orders: int
    date_range: tuple[datetime | None, datetime | None]
    technicians: int
    findings: list[Finding]
    unmapped_headers: dict[str, list[str]] = field(default_factory=dict)
    load_warnings: list[str] = field(default_factory=list)
    names_leaked: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "blocking"]

    @property
    def coverage_days(self) -> float | None:
        start, end = self.date_range
        return (end - start).days if start and end else None

    def to_dict(self) -> dict[str, object]:
        start, end = self.date_range
        return {
            "generated_at": self.generated_at.isoformat(),
            "work_orders": self.work_orders,
            "technicians": self.technicians,
            "date_range": {
                "from": start.date().isoformat() if start else None,
                "to": end.date().isoformat() if end else None,
                "days": self.coverage_days,
            },
            "findings": [f.to_dict() for f in self.findings],
            "unmapped_headers": self.unmapped_headers,
            "load_warnings": self.load_warnings,
            "anonymization_ok": not self.names_leaked,
            "names_leaked": self.names_leaked,
        }


def build(
    store: Store,
    *,
    now: datetime | None = None,
    unmapped_headers: dict[str, list[str]] | None = None,
    load_warnings: list[str] | None = None,
) -> QualityReport:
    now = now or datetime.now(UTC)
    work_orders = store.work_orders()
    total = len(work_orders)
    labor = store.labor_hours_by_work_order()
    completed = [wo for wo in work_orders if wo.status is WorkOrderStatus.COMPLETED]

    findings = [
        Finding(
            field="assigned technician",
            missing=sum(1 for wo in work_orders if not wo.assigned_to),
            total=total,
            costs="Without an assignee there is no department comparison and no "
            "per-technician KPI — the scorecard cannot be built.",
        ),
        Finding(
            field="close date on completed work",
            missing=sum(1 for wo in completed if not wo.closed_at),
            total=len(completed),
            costs="Time-to-completion is undefined for these, so they drop out of "
            "every cycle-time figure.",
        ),
        Finding(
            field="linked asset",
            missing=sum(1 for wo in work_orders if not wo.asset_id),
            total=total,
            costs="No asset means no service history, no recurring-fault detection, "
            "and no repair-versus-replace signal.",
        ),
        Finding(
            field="location",
            missing=sum(1 for wo in work_orders if not wo.location_id),
            total=total,
            costs="Route sequencing and per-building backlog need a location.",
        ),
        Finding(
            field="labor hours logged",
            missing=sum(1 for wo in work_orders if wo.id not in labor),
            total=total,
            costs="Stall detection compares logged hours against days open. Without "
            "hours it cannot distinguish a hard job from an untouched one — the "
            "single most useful metric here.",
        ),
        Finding(
            field="resolution text",
            missing=sum(1 for wo in completed if not wo.resolution),
            total=len(completed),
            costs="Resolutions are what a briefing quotes back and what a future "
            "retrieval corpus would be built from.",
        ),
    ]

    opened = [wo.opened_at for wo in work_orders]
    return QualityReport(
        generated_at=now,
        work_orders=total,
        date_range=(min(opened, default=None), max(opened, default=None)),
        technicians=len(store.technicians()),
        findings=findings,
        unmapped_headers=unmapped_headers or {},
        load_warnings=load_warnings or [],
        # The privacy guarantee, checked rather than asserted. Any name here is
        # a bug in the anonymizer, and the report should be loud about it.
        names_leaked=store.stored_names(others_only=True),
    )
