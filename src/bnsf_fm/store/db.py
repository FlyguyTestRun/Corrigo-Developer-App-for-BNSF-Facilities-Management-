"""Store access: connection handling, idempotent upserts, typed reads.

Every write is an `INSERT ... ON CONFLICT DO UPDATE`, so re-running an
ingestion over an overlapping date window converges rather than duplicating.
That property matters because the Tier 1 workflow is "export the last 90 days
every Monday" — overlap is the normal case, not the exception.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
from bnsf_fm.store.schema import SCHEMA, SCHEMA_VERSION

DEFAULT_DB_PATH = Path("data/bnsf_fm.db")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class Store:
    """Thin typed wrapper over a SQLite database.

    Usable as a context manager. Pass `":memory:"` for tests.
    """

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path) if path != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('version', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # -- writes ------------------------------------------------------------

    def _upsert_many(
        self, table: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]
    ) -> int:
        rows = list(rows)
        if not rows:
            return 0
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != "id")
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT (id) DO UPDATE SET {updates}"
        )
        self.conn.executemany(sql, rows)
        self.conn.commit()
        return len(rows)

    def upsert_locations(self, items: Iterable[Location]) -> int:
        return self._upsert_many(
            "locations",
            ("id", "building", "floor", "room", "description"),
            [(x.id, x.building, x.floor, x.room, x.description) for x in items],
        )

    def upsert_technicians(self, items: Iterable[Technician]) -> int:
        return self._upsert_many(
            "technicians",
            ("id", "name", "trade", "active"),
            [(x.id, x.name, x.trade, int(x.active)) for x in items],
        )

    def upsert_manuals(self, items: Iterable[Manual]) -> int:
        return self._upsert_many(
            "manuals",
            ("id", "manufacturer", "model", "title", "relative_path", "page_count", "text"),
            [
                (x.id, x.manufacturer, x.model, x.title, x.relative_path, x.page_count, x.text)
                for x in items
            ],
        )

    def upsert_assets(self, items: Iterable[Asset]) -> int:
        return self._upsert_many(
            "assets",
            (
                "id", "tag", "name", "category", "location_id", "manufacturer",
                "model", "serial", "installed_on", "criticality", "expected_life_years",
            ),
            [
                (
                    x.id, x.tag, x.name, x.category, x.location_id, x.manufacturer,
                    x.model, x.serial, _iso(x.installed_on), str(x.criticality),
                    x.expected_life_years,
                )
                for x in items
            ],
        )

    def upsert_work_orders(self, items: Iterable[WorkOrder]) -> int:
        return self._upsert_many(
            "work_orders",
            (
                "id", "number", "title", "description", "status", "type", "priority",
                "asset_id", "location_id", "assigned_to", "opened_at", "closed_at",
                "resolution",
            ),
            [
                (
                    x.id, x.number, x.title, x.description, str(x.status), str(x.type),
                    str(x.priority), x.asset_id, x.location_id, x.assigned_to,
                    _iso(x.opened_at), _iso(x.closed_at), x.resolution,
                )
                for x in items
            ],
        )

    def upsert_labor(self, items: Iterable[LaborEntry]) -> int:
        return self._upsert_many(
            "labor_entries",
            ("id", "work_order_id", "technician_id", "hours", "logged_at", "note"),
            [
                (x.id, x.work_order_id, x.technician_id, x.hours, _iso(x.logged_at), x.note)
                for x in items
            ],
        )

    def upsert_parts(self, items: Iterable[Part]) -> int:
        return self._upsert_many(
            "parts",
            (
                "id", "sku", "name", "unit", "on_hand", "reorder_point",
                "reorder_quantity", "location_id", "unit_cost",
            ),
            [
                (
                    x.id, x.sku, x.name, x.unit, x.on_hand, x.reorder_point,
                    x.reorder_quantity, x.location_id, x.unit_cost,
                )
                for x in items
            ],
        )

    def upsert_part_usage(self, items: Iterable[PartUsage]) -> int:
        return self._upsert_many(
            "part_usage",
            ("id", "work_order_id", "part_id", "quantity", "used_at"),
            [(x.id, x.work_order_id, x.part_id, x.quantity, _iso(x.used_at)) for x in items],
        )

    def set_campus_edges(self, edges: Iterable[tuple[str, str, float]]) -> int:
        """Replace the walking graph. Stores both directions."""
        rows: list[tuple[str, str, float]] = []
        for a, b, minutes in edges:
            rows.append((a, b, minutes))
            rows.append((b, a, minutes))
        self.conn.execute("DELETE FROM campus_edges")
        self.conn.executemany(
            "INSERT INTO campus_edges (from_building, to_building, walk_minutes) "
            "VALUES (?, ?, ?) ON CONFLICT DO UPDATE SET walk_minutes = excluded.walk_minutes",
            rows,
        )
        self.conn.commit()
        return len(rows)

    # -- reads -------------------------------------------------------------

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params))

    def count(self, table: str) -> int:
        # Table names are internal constants, never user input.
        return int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def locations(self) -> list[Location]:
        return [Location(**dict(r)) for r in self._query("SELECT * FROM locations")]

    def technicians(self) -> list[Technician]:
        return [
            Technician(
                id=r["id"], name=r["name"], trade=r["trade"], active=bool(r["active"])
            )
            for r in self._query("SELECT * FROM technicians")
        ]

    def assets(self) -> list[Asset]:
        out = []
        for r in self._query("SELECT * FROM assets"):
            d = dict(r)
            d["installed_on"] = _dt(d["installed_on"])
            out.append(Asset(**d))
        return out

    def asset(self, asset_id: str) -> Asset | None:
        rows = self._query("SELECT * FROM assets WHERE id = ?", (asset_id,))
        if not rows:
            return None
        d = dict(rows[0])
        d["installed_on"] = _dt(d["installed_on"])
        return Asset(**d)

    def work_orders(self, *, open_only: bool = False) -> list[WorkOrder]:
        sql = "SELECT * FROM work_orders"
        if open_only:
            sql += " WHERE status NOT IN ('completed', 'cancelled')"
        out = []
        for r in self._query(sql):
            d = dict(r)
            d["opened_at"] = _dt(d["opened_at"])
            d["closed_at"] = _dt(d["closed_at"])
            out.append(WorkOrder(**d))
        return out

    def work_orders_for_asset(self, asset_id: str) -> list[WorkOrder]:
        out = []
        rows = self._query(
            "SELECT * FROM work_orders WHERE asset_id = ? ORDER BY opened_at DESC",
            (asset_id,),
        )
        for r in rows:
            d = dict(r)
            d["opened_at"] = _dt(d["opened_at"])
            d["closed_at"] = _dt(d["closed_at"])
            out.append(WorkOrder(**d))
        return out

    def labor_entries(self) -> list[LaborEntry]:
        out = []
        for r in self._query("SELECT * FROM labor_entries"):
            d = dict(r)
            d["logged_at"] = _dt(d["logged_at"])
            out.append(LaborEntry(**d))
        return out

    def labor_hours_by_work_order(self) -> dict[str, float]:
        rows = self._query(
            "SELECT work_order_id, SUM(hours) AS h FROM labor_entries GROUP BY work_order_id"
        )
        return {r["work_order_id"]: float(r["h"]) for r in rows}

    def parts(self) -> list[Part]:
        return [Part(**dict(r)) for r in self._query("SELECT * FROM parts")]

    def part_usage(self) -> list[PartUsage]:
        out = []
        for r in self._query("SELECT * FROM part_usage"):
            d = dict(r)
            d["used_at"] = _dt(d["used_at"])
            out.append(PartUsage(**d))
        return out

    def manual_for_asset(self, asset: Asset) -> Manual | None:
        if not asset.manual_key:
            return None
        rows = self._query(
            "SELECT * FROM manuals WHERE LOWER(TRIM(manufacturer)) = ? "
            "AND LOWER(TRIM(model)) = ?",
            asset.manual_key.split("|"),
        )
        return Manual(**dict(rows[0])) if rows else None

    def campus_edges(self) -> dict[tuple[str, str], float]:
        rows = self._query("SELECT * FROM campus_edges")
        return {(r["from_building"], r["to_building"]): float(r["walk_minutes"]) for r in rows}
