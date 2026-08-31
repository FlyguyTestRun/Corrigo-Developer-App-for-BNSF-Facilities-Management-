"""Anonymization and label allocation.

These tests exist because the failure modes here are silent and serious: a
co-worker's name surviving into the database, two people merging into one row,
or "Tech 3" quietly meaning a different person after a new hire joins.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta

import pytest

from bnsf_fm.ingest import CsvSource, Roster, normalize_identity, surrogate_id
from bnsf_fm.models import Technician, WorkOrder, WorkOrderStatus
from bnsf_fm.store import Store

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


class TestNormalization:
    @pytest.mark.parametrize(
        "spelling",
        ["Bryan Shaw", "bryan shaw", "BRYAN SHAW", "Shaw, Bryan", "Bryan  Shaw",
         " Bryan Shaw ", "Bryan Shaw (Maintenance)"],
    )
    def test_spelling_drift_folds_to_one_person(self, spelling):
        """Corrigo screens disagree on name format; splitting one person across
        several rows would corrupt every per-technician number."""
        assert normalize_identity(spelling) == normalize_identity("Bryan Shaw")
        assert surrogate_id(spelling) == surrogate_id("Bryan Shaw")

    def test_different_people_get_different_ids(self):
        assert surrogate_id("Bryan Shaw") != surrogate_id("Brian Shaw")

    def test_employee_ids_work_too(self):
        assert surrogate_id("EMP0001") == surrogate_id("emp-0001")
        assert surrogate_id("EMP0001") != surrogate_id("EMP0002")


class TestRoster:
    def test_peer_names_are_discarded(self):
        roster = Roster(me="Bryan Shaw")
        peer = roster.identify("Dana Whitfield")
        assert peer is not None
        assert peer.name is None, "a co-worker's real name must not survive"
        assert peer.is_self is False
        assert "whitfield" not in peer.id.lower()

    def test_self_keeps_a_name(self):
        roster = Roster(me="Bryan Shaw")
        me = roster.identify("Shaw, Bryan")
        assert me is not None
        assert me.is_self is True
        assert me.name == "Bryan Shaw"

    def test_no_me_still_strips_everyone(self):
        """Opting out of anonymization is not a flag you can forget to pass."""
        roster = Roster()
        identity = roster.identify("Dana Whitfield")
        assert identity is not None and identity.name is None

    def test_unmatched_me_is_reported(self):
        roster = Roster(me="Bryan Shaw")
        roster.identify("Someone Else")
        assert roster.matched_self is False

    @pytest.mark.parametrize("blank", [None, "", "   ", "-"])
    def test_blanks_yield_nothing(self, blank):
        assert Roster().identify(blank) is None

    def test_identify_is_idempotent(self):
        roster = Roster()
        first = roster.identify("Dana Whitfield")
        second = roster.identify("whitfield, dana")
        assert first is second
        assert roster.distinct_count() == 1


class TestLabels:
    def _tech(self, raw: str, roster: Roster) -> Technician:
        identity = roster.identify(raw)
        assert identity is not None
        return Technician(id=identity.id, name=identity.name, is_self=identity.is_self)

    def test_labels_are_sequential_and_unique(self, small_store):
        roster = Roster()
        small_store.upsert_technicians(
            [self._tech(f"Person {i}", roster) for i in range(1, 6)]
        )
        labels = [t.label for t in small_store.technicians()]
        assert sorted(labels) == sorted(f"Tech {i}" for i in range(1, 6))

    def test_fifty_technicians_collide_never(self, small_store):
        """The old sha256 % 100 label collided for 2 of 12 realistic employee
        ids, silently merging two real people into one row."""
        roster = Roster()
        small_store.upsert_technicians(
            [self._tech(f"EMP{i:04d}", roster) for i in range(1, 51)]
        )
        labels = [t.label for t in small_store.technicians()]
        assert len(labels) == 50
        assert len(set(labels)) == 50

    def test_labels_survive_a_reload(self, small_store):
        roster = Roster()
        people = [self._tech(f"Person {i}", roster) for i in range(1, 4)]
        small_store.upsert_technicians(people)
        before = {t.id: t.label for t in small_store.technicians()}
        small_store.upsert_technicians(people)
        assert {t.id: t.label for t in small_store.technicians()} == before

    def test_a_new_hire_does_not_renumber_anyone(self, small_store):
        """If labels were re-derived by sorting, one new hire would shift
        everyone and "Tech 3" would mean a different person than last month."""
        roster = Roster()
        # "Aaron" sorts before the others by surrogate only by chance, so use
        # several and add one afterwards.
        small_store.upsert_technicians(
            [self._tech(f"Person {i}", roster) for i in range(1, 5)]
        )
        before = {t.id: t.label for t in small_store.technicians()}
        small_store.upsert_technicians([self._tech("Brand New Hire", roster)])
        after = {t.id: t.label for t in small_store.technicians()}
        for tech_id, label in before.items():
            assert after[tech_id] == label, "an existing technician was renumbered"
        assert len(after) == len(before) + 1

    def test_self_is_named_peers_are_not(self, small_store):
        roster = Roster(me="Bryan Shaw")
        small_store.upsert_technicians(
            [self._tech("Bryan Shaw", roster), self._tech("Dana Whitfield", roster)]
        )
        me = small_store.self_technician()
        assert me is not None and me.display_name() == "Bryan Shaw"
        assert small_store.stored_names(others_only=True) == []
        peers = [t for t in small_store.technicians() if not t.is_self]
        assert all(t.display_name().startswith("Tech ") for t in peers)


class TestCsvAnonymization:
    def _write(self, directory, name, header, rows):
        """Write real CSV — a value like "Shaw, Bryan" must stay one field."""
        path = directory / name
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def test_department_is_discovered_from_the_assignee_column(self, tmp_path):
        """Most technician-level exports have no employee roster file — the
        only place a co-worker appears is Assigned To."""
        self._write(
            tmp_path, "workorders.csv",
            ["Work Order ID", "Status", "Created Date", "Assigned To"],
            [["W1", "Completed", "03/15/2026", "Bryan Shaw"],
             ["W2", "Completed", "03/16/2026", "Dana Whitfield"],
             ["W3", "New", "03/17/2026", "Shaw, Bryan"]],
        )
        roster = Roster(me="Bryan Shaw")
        batch = CsvSource(tmp_path, roster=roster).fetch()
        assert len(batch.technicians) == 2
        assert roster.matched_self is True
        named = [t for t in batch.technicians if t.name]
        assert [t.name for t in named] == ["Bryan Shaw"]
        # Both of Bryan's spellings resolved to one technician.
        assert len({wo.assigned_to for wo in batch.work_orders}) == 2

    def test_names_never_reach_the_store(self, tmp_path):
        self._write(
            tmp_path, "workorders.csv",
            ["Work Order ID", "Status", "Created Date", "Assigned To"],
            [["W1", "Completed", "03/15/2026", "Dana Whitfield"]],
        )
        batch = CsvSource(tmp_path, roster=Roster(me="Bryan Shaw")).fetch()
        with Store(":memory:") as store:
            store.upsert_technicians(batch.technicians)
            store.upsert_work_orders(batch.work_orders)
            assert store.stored_names(others_only=True) == []
            rows = store.conn.execute("SELECT id, name FROM technicians").fetchall()
            assert all("whitfield" not in str(r["id"]).lower() for r in rows)

    def test_unmapped_headers_are_reported(self, tmp_path):
        self._write(
            tmp_path, "workorders.csv",
            ["Work Order ID", "Status", "Created Date", "Cost Centre", "Vendor Ref"],
            [["W1", "New", "03/15/2026", "CC-1", "V-9"]],
        )
        source = CsvSource(tmp_path)
        source.fetch()
        leftover = source.unmapped_headers["workorders.csv"]
        assert "Cost Centre" in leftover
        assert "Vendor Ref" in leftover

    def test_unmatched_me_warns_on_the_batch(self, tmp_path):
        self._write(
            tmp_path, "workorders.csv",
            ["Work Order ID", "Status", "Created Date", "Assigned To"],
            [["W1", "New", "03/15/2026", "Dana Whitfield"]],
        )
        batch = CsvSource(tmp_path, roster=Roster(me="Bryan Shaw")).fetch()
        assert any("matched no row" in w for w in batch.warnings)


class TestPartialExports:
    """A technician-level export is work orders and nothing else.

    The rows carry an Asset Id and a Location Id that no file describes. Before
    stubbing, the foreign keys rejected the entire load — for the normal case,
    not an edge case.
    """

    def test_work_order_only_export_loads(self, small_store):
        small_store.upsert_technicians([Technician(id="T1")])
        orders = [
            WorkOrder(
                id="W1", number="W1", title="Fault", status=WorkOrderStatus.NEW,
                asset_id="AST-001", location_id="LOC-01", assigned_to="T1",
                opened_at=NOW - timedelta(days=3),
            )
        ]
        stubbed = small_store.ensure_referenced(orders)
        assert stubbed == {"assets": 1, "locations": 1}
        assert small_store.upsert_work_orders(orders) == 1

    def test_stubs_are_placeholders_not_invented_detail(self, small_store):
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.ensure_referenced(
            [
                WorkOrder(
                    id="W1", number="W1", title="Fault", status=WorkOrderStatus.NEW,
                    asset_id="AST-001", location_id="LOC-01", assigned_to="T1",
                    opened_at=NOW - timedelta(days=3),
                )
            ]
        )
        asset = small_store.asset("AST-001")
        assert asset is not None
        assert asset.manufacturer is None and asset.model is None
        assert small_store.stub_counts() == {"assets": 1, "locations": 1}

    def test_a_later_asset_export_enriches_the_same_id(self, small_store):
        from bnsf_fm.models import Asset, Location

        small_store.upsert_technicians([Technician(id="T1")])
        small_store.ensure_referenced(
            [
                WorkOrder(
                    id="W1", number="W1", title="Fault", status=WorkOrderStatus.NEW,
                    asset_id="AST-001", location_id="LOC-01", assigned_to="T1",
                    opened_at=NOW - timedelta(days=3),
                )
            ]
        )
        small_store.upsert_locations(
            [Location(id="LOC-01", building="Headquarters West", floor="2")]
        )
        small_store.upsert_assets(
            [
                Asset(id="AST-001", tag="AHU-1", name="Air Handler 1",
                      category="Air Handling Unit", location_id="LOC-01",
                      manufacturer="Trane", model="CSAA")
            ]
        )
        asset = small_store.asset("AST-001")
        assert asset is not None and asset.manufacturer == "Trane"
        assert small_store.stub_counts() == {"assets": 0, "locations": 0}

    def test_ensure_referenced_is_idempotent(self, small_store):
        orders = [
            WorkOrder(
                id="W1", number="W1", title="Fault", status=WorkOrderStatus.NEW,
                asset_id="AST-001", location_id="LOC-01",
                opened_at=NOW - timedelta(days=3),
            )
        ]
        assert small_store.ensure_referenced(orders) == {"assets": 1, "locations": 1}
        assert small_store.ensure_referenced(orders) == {"assets": 0, "locations": 0}
