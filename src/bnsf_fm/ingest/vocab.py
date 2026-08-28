"""Shared Corrigo vocabulary: status/priority/type aliases and value parsing.

Both ingestion sources need these. Corrigo's status and priority vocabularies
are tenant-configurable, so the same logical state arrives spelled differently
depending on whether it came from a CSV export, Corrigo BI, or the REST API —
and differently again between two customers. Keeping the alias tables in one
place means a site adds its spelling once and both paths pick it up.

Everything maps generously and falls back to a safe default rather than
dropping a record: an unrecognized status is not a reason to lose a work order.
The single exception is a missing open date, which the callers drop, because a
work order that cannot be aged would corrupt every metric downstream.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, TypeVar

from bnsf_fm.models import Priority, WorkOrderStatus, WorkOrderType

T = TypeVar("T")


def norm(text: str) -> str:
    """Normalize a token for matching: casefold, strip punctuation and spacing."""
    return re.sub(r"[^a-z0-9]", "", text.strip().lower())


def alias(raw: str | None, table: dict[str, T], default: T) -> T:
    """Look `raw` up in an alias table, falling back to `default`."""
    if not raw:
        return default
    return table.get(norm(raw), default)


def to_number(raw: str | None, default: float = 0.0) -> float:
    """Parse a number out of an exported cell, tolerating currency and commas."""
    if raw is None:
        return default
    cleaned = re.sub(r"[^0-9.\-]", "", raw.strip())
    if not cleaned or cleaned in {"-", ".", "-."}:
        return default
    try:
        return float(cleaned)
    except ValueError:
        return default


def as_text(value: Any) -> str | None:
    """Coerce an arbitrary API value to text, mapping empty to None."""
    if value is None or value == "":
        return None
    return str(value)


STATUS_ALIASES: dict[str, WorkOrderStatus] = {
    "new": WorkOrderStatus.NEW, "open": WorkOrderStatus.NEW,
    "created": WorkOrderStatus.NEW, "unassigned": WorkOrderStatus.NEW,
    "assigned": WorkOrderStatus.ASSIGNED, "dispatched": WorkOrderStatus.ASSIGNED,
    "accepted": WorkOrderStatus.ASSIGNED, "scheduled": WorkOrderStatus.ASSIGNED,
    "inprogress": WorkOrderStatus.IN_PROGRESS, "started": WorkOrderStatus.IN_PROGRESS,
    "onsite": WorkOrderStatus.IN_PROGRESS, "active": WorkOrderStatus.IN_PROGRESS,
    "onhold": WorkOrderStatus.ON_HOLD, "hold": WorkOrderStatus.ON_HOLD,
    "waitingonparts": WorkOrderStatus.ON_HOLD, "pending": WorkOrderStatus.ON_HOLD,
    "suspended": WorkOrderStatus.ON_HOLD, "deferred": WorkOrderStatus.ON_HOLD,
    "completed": WorkOrderStatus.COMPLETED, "complete": WorkOrderStatus.COMPLETED,
    "closed": WorkOrderStatus.COMPLETED, "done": WorkOrderStatus.COMPLETED,
    "resolved": WorkOrderStatus.COMPLETED, "finished": WorkOrderStatus.COMPLETED,
    "cancelled": WorkOrderStatus.CANCELLED, "canceled": WorkOrderStatus.CANCELLED,
    "void": WorkOrderStatus.CANCELLED, "rejected": WorkOrderStatus.CANCELLED,
}

PRIORITY_ALIASES: dict[str, Priority] = {
    "emergency": Priority.EMERGENCY, "urgent": Priority.EMERGENCY,
    "critical": Priority.EMERGENCY, "p1": Priority.EMERGENCY, "1": Priority.EMERGENCY,
    "high": Priority.HIGH, "p2": Priority.HIGH, "2": Priority.HIGH,
    "medium": Priority.MEDIUM, "normal": Priority.MEDIUM, "standard": Priority.MEDIUM,
    "p3": Priority.MEDIUM, "3": Priority.MEDIUM, "routine": Priority.MEDIUM,
    "low": Priority.LOW, "p4": Priority.LOW, "4": Priority.LOW, "deferred": Priority.LOW,
}

TYPE_ALIASES: dict[str, WorkOrderType] = {
    "reactive": WorkOrderType.REACTIVE, "corrective": WorkOrderType.REACTIVE,
    "repair": WorkOrderType.REACTIVE, "demand": WorkOrderType.REACTIVE,
    "servicerequest": WorkOrderType.REACTIVE, "oncall": WorkOrderType.REACTIVE,
    "preventive": WorkOrderType.PREVENTIVE, "preventative": WorkOrderType.PREVENTIVE,
    "pm": WorkOrderType.PREVENTIVE, "planned": WorkOrderType.PREVENTIVE,
    "scheduled": WorkOrderType.PREVENTIVE, "routinemaintenance": WorkOrderType.PREVENTIVE,
    "inspection": WorkOrderType.INSPECTION, "audit": WorkOrderType.INSPECTION,
    "survey": WorkOrderType.INSPECTION, "roundsandreadings": WorkOrderType.INSPECTION,
    "project": WorkOrderType.PROJECT, "capital": WorkOrderType.PROJECT,
    "installation": WorkOrderType.PROJECT,
}

# Formats seen in Corrigo exports, most specific first. ISO is tried separately.
DATE_FORMATS = [
    "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M", "%m/%d/%Y", "%m/%d/%y %I:%M %p", "%m/%d/%y",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%b %d, %Y %I:%M %p", "%b %d, %Y",
]

_NULL_TOKENS = {"null", "n/a", "-", "none", "(none)"}


def parse_datetime(raw: str | None) -> datetime | None:
    """Parse a Corrigo timestamp. Returns None on blank or unparseable input.

    Timestamps land as naive local time in most exports and are treated as UTC.
    For aging arithmetic measured in days this is immaterial, and inventing a
    timezone we cannot verify would be worse than being consistently naive.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text or text.lower() in _NULL_TOKENS:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None
