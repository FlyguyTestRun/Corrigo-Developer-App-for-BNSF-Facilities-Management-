"""The ingestion seam.

`ingest/` is the only part of this codebase that knows where data came from.
Everything downstream — analytics, the API, the dashboard, the MCP server —
reads the normalized store and cannot tell a CSV export from a REST API pull.

That is the whole architectural bet of this project. The site can start today
on manual Corrigo exports, which need no approval and no credentials, and move
to the REST API the day a Corrigo system administrator issues client
credentials, with a config change rather than a rewrite.

Three sources implement this protocol:

  CsvSource      Tier 1 — Corrigo UI / Business Intelligence exports.
  CorrigoApiSource   Tier 2 — the Corrigo Enterprise REST API.
  FixtureSource  synthetic campus data, for tests and the public repo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from bnsf_fm.models import (
    Asset,
    LaborEntry,
    Location,
    Manual,
    Part,
    PartUsage,
    Technician,
    WorkOrder,
)
from bnsf_fm.store import Store


@dataclass
class Batch:
    """One pull from a source. Any subset of entity types may be populated."""

    locations: list[Location] = field(default_factory=list)
    technicians: list[Technician] = field(default_factory=list)
    assets: list[Asset] = field(default_factory=list)
    manuals: list[Manual] = field(default_factory=list)
    work_orders: list[WorkOrder] = field(default_factory=list)
    labor_entries: list[LaborEntry] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    part_usage: list[PartUsage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def totals(self) -> dict[str, int]:
        return {
            "locations": len(self.locations),
            "technicians": len(self.technicians),
            "assets": len(self.assets),
            "manuals": len(self.manuals),
            "work_orders": len(self.work_orders),
            "labor_entries": len(self.labor_entries),
            "parts": len(self.parts),
            "part_usage": len(self.part_usage),
        }


@runtime_checkable
class IngestionSource(Protocol):
    """Anything that can produce a `Batch` of normalized entities."""

    name: str

    def fetch(self) -> Batch: ...


def load(source: IngestionSource, store: Store) -> dict[str, int]:
    """Pull from `source` and write into `store`, idempotently.

    Order matters: locations and technicians are written before the records
    that reference them, so foreign keys resolve on a cold database.
    """
    batch = source.fetch()
    written = {
        "locations": store.upsert_locations(batch.locations),
        "technicians": store.upsert_technicians(batch.technicians),
        "manuals": store.upsert_manuals(batch.manuals),
        "assets": store.upsert_assets(batch.assets),
        "parts": store.upsert_parts(batch.parts),
        "work_orders": store.upsert_work_orders(batch.work_orders),
        "labor_entries": store.upsert_labor(batch.labor_entries),
        "part_usage": store.upsert_part_usage(batch.part_usage),
    }
    return written
