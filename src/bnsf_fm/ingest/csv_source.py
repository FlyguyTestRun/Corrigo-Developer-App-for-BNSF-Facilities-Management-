"""Tier 1 ingestion: Corrigo UI and Business Intelligence exports.

This is the path that works *today*. It needs no API credentials, no system
administrator, and no approval — a user exports the reports they can already
see in Corrigo, drops the files in `data/raw/`, and runs the loader. Nothing
automates against the Corrigo session; the export is a human action taken under
that person's existing permissions.

Corrigo's export headers vary by report, tenant configuration, and version, so
column names are *configuration*, not code. `HeaderMap` declares the candidate
header spellings for each field; adding a site's variant is a one-line change
in `DEFAULT_MAPPINGS` (or a JSON file passed at runtime), never a code edit.

Unmapped required columns produce a clear error naming the headers actually
present in the file, because the first run against a real export is always a
mapping problem and the failure needs to say so.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bnsf_fm.ingest.base import Batch
from bnsf_fm.ingest.vocab import (
    PRIORITY_ALIASES,
    STATUS_ALIASES,
    TYPE_ALIASES,
    alias,
    norm,
    parse_datetime,
    to_number,
)
from bnsf_fm.models import (
    Asset,
    AssetCriticality,
    LaborEntry,
    Location,
    Part,
    Priority,
    Technician,
    WorkOrder,
    WorkOrderStatus,
    WorkOrderType,
)


class MappingError(ValueError):
    """A required column could not be located in an export."""


@dataclass
class HeaderMap:
    """Candidate spellings for each logical field.

    `required` names the logical fields without which a row is meaningless.
    """

    fields: dict[str, list[str]]
    required: list[str] = field(default_factory=list)

    def resolve(self, headers: Sequence[str]) -> dict[str, str]:
        """Map logical field name -> actual header present in the file."""
        available = {norm(h): h for h in headers}
        resolved: dict[str, str] = {}
        for logical, candidates in self.fields.items():
            for candidate in candidates:
                actual = available.get(norm(candidate))
                if actual is not None:
                    resolved[logical] = actual
                    break
        missing = [f for f in self.required if f not in resolved]
        if missing:
            raise MappingError(
                f"Could not map required column(s) {missing}. "
                f"Headers present in the export: {list(headers)}. "
                f"Add the site's spelling to the header mapping."
            )
        return resolved


# Candidate headers seen across Corrigo Enterprise exports and Corrigo BI
# extracts. This list is meant to grow — append, do not replace.
DEFAULT_MAPPINGS: dict[str, HeaderMap] = {
    "work_orders": HeaderMap(
        required=["id", "opened_at", "status"],
        fields={
            "id": ["WorkOrderId", "WO Id", "Work Order ID", "Id"],
            "number": ["WorkOrderNumber", "WO Number", "Work Order #", "Number"],
            "title": ["Summary", "Subject", "Title", "Task"],
            "description": ["Description", "Details", "Problem Description"],
            "status": ["Status", "WO Status", "WorkOrderStatus"],
            "type": ["Type", "WO Type", "Work Type"],
            "priority": ["Priority", "WO Priority"],
            "asset_id": ["AssetId", "Asset Id", "Equipment Id", "Asset"],
            "location_id": ["LocationId", "Location Id", "Space Id"],
            "assigned_to": ["AssignedTo", "Assigned To", "Technician", "Employee"],
            "opened_at": ["CreatedDate", "Created Date", "Date Created", "Opened", "OpenedOn"],
            "closed_at": ["CompletedDate", "Completed Date", "Closed", "ClosedOn", "Date Completed"],
            "resolution": ["Resolution", "Completion Notes", "Repair Notes"],
        },
    ),
    "assets": HeaderMap(
        required=["id", "name"],
        fields={
            "id": ["AssetId", "Asset Id", "Equipment Id", "Id"],
            "tag": ["AssetTag", "Tag", "Barcode", "Asset Number"],
            "name": ["AssetName", "Name", "Description", "Equipment Name"],
            "category": ["Category", "Asset Type", "Type", "Class"],
            "location_id": ["LocationId", "Location Id", "Space Id", "Location"],
            "manufacturer": ["Manufacturer", "Make", "Mfr"],
            "model": ["Model", "Model Number", "ModelNo"],
            "serial": ["Serial", "Serial Number", "SerialNo"],
            "installed_on": ["InstallDate", "Install Date", "Installed On", "In Service Date"],
            "criticality": ["Criticality", "Priority", "Asset Criticality"],
        },
    ),
    "locations": HeaderMap(
        required=["id", "building"],
        fields={
            "id": ["LocationId", "Location Id", "Space Id", "Id"],
            "building": ["Building", "Property", "Site", "Facility"],
            "floor": ["Floor", "Level"],
            "room": ["Room", "Space", "Area", "Suite"],
            "description": ["Description", "Location Description"],
        },
    ),
    "technicians": HeaderMap(
        required=["id"],
        fields={
            "id": ["EmployeeId", "Employee Id", "TechnicianId", "Id", "UserId"],
            "name": ["EmployeeName", "Name", "Full Name", "Technician"],
            "trade": ["Trade", "Skill", "Craft", "Specialty"],
            "active": ["Active", "IsActive", "Status"],
        },
    ),
    "labor_entries": HeaderMap(
        required=["work_order_id", "technician_id", "hours"],
        fields={
            "id": ["LaborId", "Labor Id", "TimeEntryId", "Id"],
            "work_order_id": ["WorkOrderId", "WO Id", "Work Order ID"],
            "technician_id": ["EmployeeId", "Employee Id", "TechnicianId", "Technician"],
            "hours": ["Hours", "Labor Hours", "Duration", "TimeSpent"],
            "logged_at": ["Date", "LoggedDate", "Work Date", "Entry Date"],
            "note": ["Note", "Comment", "Notes"],
        },
    ),
    "parts": HeaderMap(
        required=["id", "name"],
        fields={
            "id": ["PartId", "Part Id", "ItemId", "Id"],
            "sku": ["SKU", "Part Number", "PartNo", "Item Number"],
            "name": ["Name", "Description", "Part Name", "Item"],
            "unit": ["Unit", "UOM", "Unit of Measure"],
            "on_hand": ["OnHand", "On Hand", "Quantity", "Qty"],
            "reorder_point": ["ReorderPoint", "Reorder Point", "Min", "Minimum"],
            "reorder_quantity": ["ReorderQuantity", "Reorder Qty", "Order Qty"],
            "location_id": ["LocationId", "Location Id", "Storeroom"],
            "unit_cost": ["UnitCost", "Unit Cost", "Cost", "Price"],
        },
    ),
}

def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV, tolerating a UTF-8 BOM (Excel writes one)."""
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = list(reader.fieldnames or [])
        rows = [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]
    return headers, rows


class CsvSource:
    """Loads a directory of Corrigo exports.

    Files are matched by name: any file whose stem contains "workorder"/"wo"
    feeds work orders, "asset"/"equipment" feeds assets, and so on. Explicit
    per-entity paths override discovery.
    """

    name = "csv"

    _PATTERNS: dict[str, tuple[str, ...]] = {
        "locations": ("location", "space", "property"),
        "technicians": ("employee", "technician", "labor_roster", "people"),
        "assets": ("asset", "equipment"),
        "parts": ("part", "inventory", "material"),
        "work_orders": ("workorder", "work_order", "wo"),
        "labor_entries": ("labor", "time", "timesheet"),
    }

    def __init__(
        self,
        directory: Path | str = Path("data/raw"),
        *,
        files: dict[str, Path] | None = None,
        mappings: dict[str, HeaderMap] | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.files = files or {}
        self.mappings = {**DEFAULT_MAPPINGS, **(mappings or {})}
        self.warnings: list[str] = []

    @classmethod
    def with_mapping_file(cls, directory: Path | str, mapping_path: Path | str) -> CsvSource:
        """Build a source using site-specific header spellings from JSON.

        The JSON is `{entity: {field: [candidate headers...]}}` and is merged
        over the defaults, so a site only declares what differs.
        """
        raw = json.loads(Path(mapping_path).read_text(encoding="utf-8"))
        merged: dict[str, HeaderMap] = {}
        for entity, fields in raw.items():
            base = DEFAULT_MAPPINGS.get(entity)
            combined = dict(base.fields) if base else {}
            for logical, candidates in fields.items():
                combined[logical] = list(candidates) + combined.get(logical, [])
            merged[entity] = HeaderMap(
                fields=combined, required=base.required if base else []
            )
        return cls(directory, mappings=merged)

    def _discover(self, entity: str) -> list[Path]:
        if entity in self.files:
            return [self.files[entity]]
        if not self.directory.is_dir():
            return []
        patterns = self._PATTERNS[entity]
        hits = []
        for path in sorted(self.directory.glob("*.csv")):
            stem = norm(path.stem)
            if any(p.replace("_", "") in stem for p in patterns):
                # "wo" is a short token; require it to stand apart from words
                # like "workflow" that would otherwise match.
                if patterns == self._PATTERNS["work_orders"] and "wo" not in stem[:4]:
                    if not any(p in stem for p in ("workorder", "work_order")):
                        continue
                hits.append(path)
        return hits

    def _rows(self, entity: str) -> Iterator[tuple[dict[str, str], dict[str, str]]]:
        """Yield (row, resolved header map) for every file feeding `entity`."""
        for path in self._discover(entity):
            headers, rows = read_rows(path)
            if not rows:
                self.warnings.append(f"{path.name}: no data rows")
                continue
            try:
                resolved = self.mappings[entity].resolve(headers)
            except MappingError as exc:
                self.warnings.append(f"{path.name}: {exc}")
                continue
            for row in rows:
                yield row, resolved

    @staticmethod
    def _get(row: dict[str, str], resolved: dict[str, str], field_name: str) -> str | None:
        header = resolved.get(field_name)
        if header is None:
            return None
        value = row.get(header, "").strip()
        return value or None

    def fetch(self) -> Batch:
        batch = Batch()
        get = self._get

        for row, res in self._rows("locations"):
            batch.locations.append(
                Location(
                    id=get(row, res, "id") or "",
                    building=get(row, res, "building") or "Unknown",
                    floor=get(row, res, "floor"),
                    room=get(row, res, "room"),
                    description=get(row, res, "description"),
                )
            )

        for row, res in self._rows("technicians"):
            active_raw = get(row, res, "active")
            batch.technicians.append(
                Technician(
                    id=get(row, res, "id") or "",
                    name=get(row, res, "name"),
                    trade=get(row, res, "trade"),
                    active=norm(active_raw or "true") not in {"false", "0", "no", "inactive"},
                )
            )

        for row, res in self._rows("assets"):
            asset_id = get(row, res, "id") or ""
            batch.assets.append(
                Asset(
                    id=asset_id,
                    tag=get(row, res, "tag") or asset_id,
                    name=get(row, res, "name") or asset_id,
                    category=get(row, res, "category") or "Uncategorized",
                    location_id=get(row, res, "location_id") or "",
                    manufacturer=get(row, res, "manufacturer"),
                    model=get(row, res, "model"),
                    serial=get(row, res, "serial"),
                    installed_on=parse_datetime(get(row, res, "installed_on")),
                    criticality=alias(
                        get(row, res, "criticality"),
                        {
                            "critical": AssetCriticality.CRITICAL,
                            "high": AssetCriticality.CRITICAL,
                            "important": AssetCriticality.IMPORTANT,
                            "medium": AssetCriticality.IMPORTANT,
                        },
                        AssetCriticality.STANDARD,
                    ),
                )
            )

        for row, res in self._rows("parts"):
            part_id = get(row, res, "id") or ""
            batch.parts.append(
                Part(
                    id=part_id,
                    sku=get(row, res, "sku") or part_id,
                    name=get(row, res, "name") or part_id,
                    unit=get(row, res, "unit") or "each",
                    on_hand=int(to_number(get(row, res, "on_hand"))),
                    reorder_point=int(to_number(get(row, res, "reorder_point"))),
                    reorder_quantity=int(to_number(get(row, res, "reorder_quantity"))),
                    location_id=get(row, res, "location_id"),
                    unit_cost=to_number(get(row, res, "unit_cost")) or None,
                )
            )

        for row, res in self._rows("work_orders"):
            opened = parse_datetime(get(row, res, "opened_at"))
            wo_id = get(row, res, "id")
            if opened is None or not wo_id:
                # A work order with no open timestamp cannot be aged. Dropping
                # it is correct; defaulting the date would silently skew every
                # aging and cycle-time number downstream.
                self.warnings.append(
                    f"work order {wo_id or '<no id>'}: unparseable or missing open date — skipped"
                )
                continue
            batch.work_orders.append(
                WorkOrder(
                    id=wo_id,
                    number=get(row, res, "number") or wo_id,
                    title=get(row, res, "title") or "(no summary)",
                    description=get(row, res, "description"),
                    status=alias(get(row, res, "status"), STATUS_ALIASES, WorkOrderStatus.ASSIGNED),
                    type=alias(get(row, res, "type"), TYPE_ALIASES, WorkOrderType.REACTIVE),
                    priority=alias(get(row, res, "priority"), PRIORITY_ALIASES, Priority.MEDIUM),
                    asset_id=get(row, res, "asset_id"),
                    location_id=get(row, res, "location_id"),
                    assigned_to=get(row, res, "assigned_to"),
                    opened_at=opened,
                    closed_at=parse_datetime(get(row, res, "closed_at")),
                    resolution=get(row, res, "resolution"),
                )
            )

        for i, (row, res) in enumerate(self._rows("labor_entries")):
            wo_id = get(row, res, "work_order_id")
            tech_id = get(row, res, "technician_id")
            if not (wo_id and tech_id):
                continue
            logged = parse_datetime(get(row, res, "logged_at"))
            batch.labor_entries.append(
                LaborEntry(
                    id=get(row, res, "id") or f"{wo_id}-{tech_id}-{i}",
                    work_order_id=wo_id,
                    technician_id=tech_id,
                    hours=max(to_number(get(row, res, "hours")), 0.0),
                    logged_at=logged or datetime.now(UTC),
                    note=get(row, res, "note"),
                )
            )

        batch.warnings.extend(self.warnings)
        return batch
