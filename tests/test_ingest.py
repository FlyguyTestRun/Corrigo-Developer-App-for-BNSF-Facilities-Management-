"""Ingestion: header mapping, value parsing, idempotency.

The CSV path is the one that will meet real Corrigo exports first, and the
failure mode that matters is silent: a column that maps to nothing, or a date
format that parses to garbage, quietly corrupting every downstream metric.
These tests exist to make those failures loud.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bnsf_fm.ingest import CsvSource, FixtureSource, HeaderMap, MappingError, load
from bnsf_fm.ingest.vocab import (
    PRIORITY_ALIASES,
    STATUS_ALIASES,
    TYPE_ALIASES,
    alias,
    parse_datetime,
    to_number,
)
from bnsf_fm.models import Priority, WorkOrderStatus, WorkOrderType
from bnsf_fm.store import Store


class TestHeaderMapping:
    def test_matching_ignores_case_spacing_and_punctuation(self):
        header_map = HeaderMap(fields={"id": ["WorkOrderId"]}, required=["id"])
        for spelling in ["WorkOrderId", "work order id", "WORK_ORDER_ID", "Work-Order-Id"]:
            assert header_map.resolve([spelling]) == {"id": spelling}

    def test_missing_required_column_names_what_was_present(self):
        header_map = HeaderMap(fields={"id": ["WorkOrderId"]}, required=["id"])
        with pytest.raises(MappingError) as exc:
            header_map.resolve(["Ticket Ref", "Summary"])
        message = str(exc.value)
        assert "id" in message
        assert "Ticket Ref" in message  # the actual headers are reported back

    def test_first_matching_candidate_wins(self):
        header_map = HeaderMap(fields={"id": ["Preferred", "Fallback"]})
        assert header_map.resolve(["Fallback", "Preferred"]) == {"id": "Preferred"}

    def test_optional_columns_may_be_absent(self):
        header_map = HeaderMap(
            fields={"id": ["Id"], "note": ["Note"]}, required=["id"]
        )
        assert header_map.resolve(["Id"]) == {"id": "Id"}


class TestValueParsing:
    @pytest.mark.parametrize(
        "raw",
        [
            "03/15/2026 02:30:00 PM", "03/15/2026 14:30", "03/15/2026",
            "2026-03-15 14:30:00", "2026-03-15", "15-Mar-2026",
            "Mar 15, 2026", "2026-03-15T14:30:00Z",
        ],
    )
    def test_corrigo_date_formats_all_parse(self, raw):
        parsed = parse_datetime(raw)
        assert parsed is not None
        assert (parsed.year, parsed.month, parsed.day) == (2026, 3, 15)
        assert parsed.tzinfo is not None  # never naive downstream

    @pytest.mark.parametrize("raw", ["", "   ", None, "NULL", "n/a", "-", "None"])
    def test_blank_and_null_tokens_yield_none(self, raw):
        assert parse_datetime(raw) is None

    def test_unparseable_date_yields_none_rather_than_guessing(self):
        assert parse_datetime("sometime last week") is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("$1,234.50", 1234.50), ("12", 12.0), ("  3.5 hrs ", 3.5),
         ("-2", -2.0), ("", 0.0), ("abc", 0.0), (None, 0.0)],
    )
    def test_number_parsing_tolerates_export_formatting(self, raw, expected):
        assert to_number(raw) == expected

    def test_status_aliases_cover_common_vocabularies(self):
        assert alias("In Progress", STATUS_ALIASES, None) is WorkOrderStatus.IN_PROGRESS
        assert alias("WAITING ON PARTS", STATUS_ALIASES, None) is WorkOrderStatus.ON_HOLD
        assert alias("Closed", STATUS_ALIASES, None) is WorkOrderStatus.COMPLETED
        assert alias("Canceled", STATUS_ALIASES, None) is WorkOrderStatus.CANCELLED

    def test_unknown_status_falls_back_rather_than_dropping(self):
        assert alias("Bespoke Tenant State", STATUS_ALIASES, WorkOrderStatus.ASSIGNED) is (
            WorkOrderStatus.ASSIGNED
        )

    def test_priority_and_type_aliases(self):
        assert alias("P1", PRIORITY_ALIASES, None) is Priority.EMERGENCY
        assert alias("Routine", PRIORITY_ALIASES, None) is Priority.MEDIUM
        assert alias("Preventative", TYPE_ALIASES, None) is WorkOrderType.PREVENTIVE
        assert alias("PM", TYPE_ALIASES, None) is WorkOrderType.PREVENTIVE


class TestCsvSource:
    def _write(self, directory, name, header, rows):
        path = directory / name
        lines = [",".join(header)]
        lines.extend(",".join(str(c) for c in row) for row in rows)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def test_loads_a_realistic_export(self, tmp_path):
        self._write(
            tmp_path, "locations.csv",
            ["Location Id", "Building", "Floor", "Room"],
            [["L1", "Headquarters West", "2", "Mechanical Room"]],
        )
        self._write(
            tmp_path, "assets.csv",
            ["Asset Id", "Asset Tag", "Asset Name", "Category", "Location Id",
             "Manufacturer", "Model", "Install Date"],
            [["A1", "AHU-1", "Air Handler 1", "Air Handling Unit", "L1",
              "Trane", "CSAA", "01/15/2010"]],
        )
        self._write(
            tmp_path, "workorders.csv",
            ["Work Order ID", "WO Number", "Summary", "Status", "Priority",
             "Asset Id", "Location Id", "Created Date", "Completed Date"],
            [["W1", "100001", "No cooling", "In Progress", "High", "A1", "L1",
              "03/15/2026 08:00:00 AM", ""]],
        )
        batch = CsvSource(tmp_path).fetch()
        assert len(batch.work_orders) == 1
        wo = batch.work_orders[0]
        assert wo.number == "100001"
        assert wo.status is WorkOrderStatus.IN_PROGRESS
        assert wo.priority is Priority.HIGH
        assert wo.closed_at is None
        assert batch.assets[0].manufacturer == "Trane"
        assert batch.locations[0].building == "Headquarters West"

    def test_work_order_without_open_date_is_dropped_with_a_warning(self, tmp_path):
        """A work order that cannot be aged must not silently get a fake date."""
        self._write(
            tmp_path, "workorders.csv",
            ["Work Order ID", "Status", "Created Date"],
            [["W1", "New", ""], ["W2", "New", "03/15/2026"]],
        )
        batch = CsvSource(tmp_path).fetch()
        assert [wo.id for wo in batch.work_orders] == ["W2"]
        assert any("W1" in w and "skipped" in w for w in batch.warnings)

    def test_unmappable_file_warns_instead_of_crashing(self, tmp_path):
        self._write(tmp_path, "workorders.csv", ["Ticket", "Notes"], [["1", "x"]])
        batch = CsvSource(tmp_path).fetch()
        assert batch.work_orders == []
        assert any("Could not map" in w for w in batch.warnings)

    def test_excel_bom_is_tolerated(self, tmp_path):
        path = tmp_path / "workorders.csv"
        path.write_text(
            "Work Order ID,Status,Created Date\nW1,New,03/15/2026\n", encoding="utf-8-sig"
        )
        batch = CsvSource(tmp_path).fetch()
        assert len(batch.work_orders) == 1

    def test_site_mapping_file_extends_defaults(self, tmp_path):
        self._write(
            tmp_path, "workorders.csv",
            ["BNSF Ticket Ref", "State", "Raised On"],
            [["W1", "Dispatched", "03/15/2026"]],
        )
        mapping = tmp_path / "mapping.json"
        mapping.write_text(
            '{"work_orders": {"id": ["BNSF Ticket Ref"], "status": ["State"],'
            ' "opened_at": ["Raised On"]}}',
            encoding="utf-8",
        )
        batch = CsvSource.with_mapping_file(tmp_path, mapping).fetch()
        assert len(batch.work_orders) == 1
        assert batch.work_orders[0].status is WorkOrderStatus.ASSIGNED

    def test_empty_directory_produces_an_empty_batch(self, tmp_path):
        batch = CsvSource(tmp_path).fetch()
        assert batch.totals() == dict.fromkeys(batch.totals(), 0)


class TestLoadIdempotency:
    def test_reloading_the_same_data_does_not_duplicate(self):
        """The Tier 1 workflow re-exports overlapping windows every week."""
        with Store(":memory:") as store:
            now = datetime(2026, 8, 28, tzinfo=UTC)
            first = load(FixtureSource(now=now), store)
            counts = {t: store.count(t) for t in
                      ("locations", "assets", "work_orders", "labor_entries", "part_usage")}
            load(FixtureSource(now=now), store)
            assert {t: store.count(t) for t in counts} == counts
            assert first["work_orders"] == counts["work_orders"]

    def test_reload_updates_changed_fields(self):
        with Store(":memory:") as store:
            now = datetime(2026, 8, 28, tzinfo=UTC)
            load(FixtureSource(now=now), store)
            wo = store.work_orders()[0]
            wo.title = "Edited upstream"
            store.upsert_work_orders([wo])
            assert next(
                w for w in store.work_orders() if w.id == wo.id
            ).title == "Edited upstream"

    def test_fixtures_are_deterministic(self):
        now = datetime(2026, 8, 28, tzinfo=UTC)
        a = FixtureSource(now=now).fetch()
        b = FixtureSource(now=now).fetch()
        assert [w.id for w in a.work_orders] == [w.id for w in b.work_orders]
        assert [w.status for w in a.work_orders] == [w.status for w in b.work_orders]

    def test_fixtures_contain_the_stall_pathology(self):
        """The synthetic data must reproduce the problem the tool exists to find."""
        from bnsf_fm.analytics import aging

        now = datetime(2026, 8, 28, tzinfo=UTC)
        with Store(":memory:") as store:
            load(FixtureSource(now=now), store)
            report = aging.build_report(store, now=now)
            assert report.stalled, "fixtures produced no stalled work orders"
            assert report.buckets["30+ days"] > 0

    def test_fault_matches_asset_category(self):
        """Fixture faults must be plausible for the asset they are raised on."""
        from bnsf_fm.ingest.fixtures import FAULTS_BY_CATEGORY

        batch = FixtureSource(now=datetime(2026, 8, 28, tzinfo=UTC)).fetch()
        assets = {a.id: a for a in batch.assets}
        for wo in batch.work_orders:
            titles = {f[0] for f in FAULTS_BY_CATEGORY[assets[wo.asset_id].category]}
            assert wo.title in titles
