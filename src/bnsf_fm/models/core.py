"""Core entities, modelled on the Corrigo Enterprise entity set.

Field names are ours, not Corrigo's — the ingestion layer maps Corrigo's
`WorkOrder`, `Asset`, `Location`, `Employee` shapes onto these. Keeping our own
vocabulary means a Corrigo schema change is a one-file mapping fix rather than
a refactor across analytics.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field


class WorkOrderStatus(StrEnum):
    """Lifecycle states, collapsed from Corrigo's richer status list.

    Corrigo distinguishes many sub-states; for accountability analytics only
    the open/active/closed distinction matters, plus ON_HOLD, which is the
    state most often abused to park a work order indefinitely.
    """

    NEW = "new"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    @property
    def is_open(self) -> bool:
        return self not in (WorkOrderStatus.COMPLETED, WorkOrderStatus.CANCELLED)


class WorkOrderType(StrEnum):
    REACTIVE = "reactive"
    PREVENTIVE = "preventive"
    INSPECTION = "inspection"
    PROJECT = "project"


class Priority(StrEnum):
    EMERGENCY = "emergency"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def sla_hours(self) -> int:
        """Target hours to completion. Site-configurable; these are defaults."""
        return _SLA_HOURS[self]


_SLA_HOURS: dict[Priority, int] = {
    Priority.EMERGENCY: 4,
    Priority.HIGH: 24,
    Priority.MEDIUM: 72,
    Priority.LOW: 336,  # 14 days
}


class AssetCriticality(StrEnum):
    CRITICAL = "critical"
    IMPORTANT = "important"
    STANDARD = "standard"


class Technician(BaseModel):
    """A person who logs labor against work orders.

    `display_name` is what any shared view shows. It defaults to a stable
    pseudonym derived from the technician id, so team-level dashboards can be
    built and shared without exposing co-workers by name. Real names are opt-in
    per-viewer, never a property of the stored record.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    trade: str | None = None
    active: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pseudonym(self) -> str:
        digest = hashlib.sha256(self.id.encode()).hexdigest()
        return f"TECH-{int(digest[:8], 16) % 100:02d}"

    def display_name(self, *, reveal: bool = False) -> str:
        if reveal and self.name:
            return self.name
        return self.pseudonym


class Location(BaseModel):
    """A place on campus: building, floor, room.

    Corrigo's `Location` entity carries no modification timestamp, so it cannot
    be extracted incrementally — the ingestion layer always pulls locations in
    full and caches them.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    building: str
    floor: str | None = None
    room: str | None = None
    description: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def label(self) -> str:
        return " / ".join(p for p in (self.building, self.floor, self.room) if p)


class Manual(BaseModel):
    """An O&M document attached to an asset model.

    Manuals are keyed by manufacturer + model rather than by individual asset,
    because one PDF covers every unit of that model on campus. `text` holds
    extracted content for retrieval; the PDF itself lives under the gitignored
    data/ tree and is referenced by relative path.
    """

    id: str
    manufacturer: str
    model: str
    title: str
    relative_path: str | None = None
    page_count: int | None = None
    text: str | None = None


class Asset(BaseModel):
    """A piece of equipment under maintenance."""

    id: str
    tag: str
    name: str
    category: str
    location_id: str
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    installed_on: datetime | None = None
    criticality: AssetCriticality = AssetCriticality.STANDARD
    expected_life_years: int | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def manual_key(self) -> str | None:
        """Join key onto Manual. None when the asset lacks make/model data."""
        if not (self.manufacturer and self.model):
            return None
        return f"{self.manufacturer.strip().lower()}|{self.model.strip().lower()}"


class WorkOrder(BaseModel):
    """A unit of maintenance work.

    `opened_at` and `closed_at` drive every aging and cycle-time metric, so
    ingestion must never leave `opened_at` unset — a work order with no open
    timestamp is dropped at load with a warning rather than silently defaulted,
    which would corrupt the aging distribution.
    """

    id: str
    number: str
    title: str
    description: str | None = None
    status: WorkOrderStatus
    type: WorkOrderType = WorkOrderType.REACTIVE
    priority: Priority = Priority.MEDIUM
    asset_id: str | None = None
    location_id: str | None = None
    assigned_to: str | None = None
    opened_at: datetime
    closed_at: datetime | None = None
    resolution: str | None = None

    def age_hours(self, *, now: datetime | None = None) -> float:
        """Hours from open to close, or to `now` if still open."""
        end = self.closed_at or now or datetime.now(UTC)
        return max((end - self.opened_at).total_seconds() / 3600.0, 0.0)

    def age_days(self, *, now: datetime | None = None) -> float:
        return self.age_hours(now=now) / 24.0

    def breached_sla(self, *, now: datetime | None = None) -> bool:
        return self.age_hours(now=now) > self.priority.sla_hours


class LaborEntry(BaseModel):
    """Time a technician logged against a work order.

    The reason this entity matters more than its size suggests: comparing
    summed labor hours against a work order's days-open is what separates a
    work order that is genuinely in progress from one that is merely open.
    """

    id: str
    work_order_id: str
    technician_id: str
    hours: float = Field(ge=0)
    logged_at: datetime
    note: str | None = None


class Part(BaseModel):
    """A stocked consumable or spare."""

    id: str
    sku: str
    name: str
    unit: str = "each"
    on_hand: int = 0
    reorder_point: int = 0
    reorder_quantity: int = 0
    location_id: str | None = None
    unit_cost: float | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def needs_reorder(self) -> bool:
        return self.on_hand <= self.reorder_point


class PartUsage(BaseModel):
    """Consumption of a part against a work order."""

    id: str
    work_order_id: str
    part_id: str
    quantity: int
    used_at: datetime
