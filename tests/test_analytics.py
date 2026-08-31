"""Analytics correctness, checked against hand-computed values.

The aging and stall arithmetic is the load-bearing part of this project — it is
what a supervisor will act on and what a technician may be asked about. Those
numbers are verified here against a small fixture whose answers can be worked
out on paper, not just smoke-tested against the generated campus.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bnsf_fm.analytics import aging, inventory, kpi, registry, routing
from bnsf_fm.models import (
    Asset,
    LaborEntry,
    Location,
    Part,
    PartUsage,
    Priority,
    Technician,
    WorkOrder,
    WorkOrderStatus,
    WorkOrderType,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _wo(
    wo_id: str,
    *,
    days_ago: float,
    status: WorkOrderStatus = WorkOrderStatus.ASSIGNED,
    priority: Priority = Priority.MEDIUM,
    closed_days_ago: float | None = None,
    assigned: str | None = "T1",
    asset_id: str | None = None,
    location_id: str | None = None,
    wo_type: WorkOrderType = WorkOrderType.REACTIVE,
    title: str = "Fault",
) -> WorkOrder:
    return WorkOrder(
        id=wo_id,
        number=wo_id,
        title=title,
        status=status,
        type=wo_type,
        priority=priority,
        assigned_to=assigned,
        asset_id=asset_id,
        location_id=location_id,
        opened_at=NOW - timedelta(days=days_ago),
        closed_at=(NOW - timedelta(days=closed_days_ago)) if closed_days_ago is not None else None,
        resolution="Fixed." if closed_days_ago is not None else None,
    )


class TestAging:
    def test_bucket_boundaries_are_inclusive_upper(self):
        assert aging.bucket_for(0) == "0-3 days"
        assert aging.bucket_for(3) == "0-3 days"
        assert aging.bucket_for(3.1) == "4-7 days"
        assert aging.bucket_for(14) == "8-14 days"
        assert aging.bucket_for(30) == "15-30 days"
        assert aging.bucket_for(30.5) == "30+ days"
        assert aging.bucket_for(999) == "30+ days"

    def test_stall_detection_uses_hours_per_day(self, small_store):
        # 20 days open with 1 hour logged = 0.05 h/day -> stalled.
        # 20 days open with 10 hours logged = 0.5 h/day -> not stalled.
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_work_orders(
            [_wo("STALLED", days_ago=20), _wo("ACTIVE", days_ago=20)]
        )
        small_store.upsert_labor(
            [
                LaborEntry(id="L1", work_order_id="STALLED", technician_id="T1",
                           hours=1.0, logged_at=NOW - timedelta(days=19)),
                LaborEntry(id="L2", work_order_id="ACTIVE", technician_id="T1",
                           hours=10.0, logged_at=NOW - timedelta(days=19)),
            ]
        )
        stalled = aging.find_stalled(
            small_store.work_orders(open_only=True),
            small_store.labor_hours_by_work_order(),
            now=NOW,
        )
        assert [s.work_order.id for s in stalled] == ["STALLED"]
        assert stalled[0].hours_per_day == 0.05

    def test_young_work_orders_are_never_stalled(self, small_store):
        """New work legitimately sits for a day or two before pickup."""
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_work_orders([_wo("FRESH", days_ago=2)])
        stalled = aging.find_stalled(
            small_store.work_orders(open_only=True), {}, now=NOW
        )
        assert stalled == []

    def test_closed_work_orders_are_never_stalled(self, small_store):
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_work_orders(
            [_wo("DONE", days_ago=60, status=WorkOrderStatus.COMPLETED, closed_days_ago=1)]
        )
        stalled = aging.find_stalled(small_store.work_orders(), {}, now=NOW)
        assert stalled == []

    def test_stalled_sorted_oldest_first(self, small_store):
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_work_orders(
            [_wo("A", days_ago=10), _wo("B", days_ago=90), _wo("C", days_ago=40)]
        )
        stalled = aging.find_stalled(
            small_store.work_orders(open_only=True), {}, now=NOW
        )
        assert [s.work_order.id for s in stalled] == ["B", "C", "A"]

    def test_severity_escalates_with_age_and_priority(self, small_store):
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_work_orders(
            [
                _wo("OLD", days_ago=45),
                _wo("MID", days_ago=20),
                _wo("YOUNG", days_ago=6),
                _wo("URGENT", days_ago=6, priority=Priority.EMERGENCY),
            ]
        )
        by_id = {
            s.work_order.id: s.severity
            for s in aging.find_stalled(
                small_store.work_orders(open_only=True), {}, now=NOW
            )
        }
        assert by_id == {
            "OLD": "severe", "MID": "moderate", "YOUNG": "watch", "URGENT": "severe"
        }

    def test_sla_breach_follows_priority(self):
        # Emergency SLA is 4h, low is 336h (14 days).
        assert _wo("E", days_ago=1, priority=Priority.EMERGENCY).breached_sla(now=NOW)
        assert not _wo("L", days_ago=1, priority=Priority.LOW).breached_sla(now=NOW)
        assert _wo("L2", days_ago=15, priority=Priority.LOW).breached_sla(now=NOW)

    def test_on_hold_abuse_only_flags_long_holds(self, small_store):
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_work_orders(
            [
                _wo("HELD_LONG", days_ago=40, status=WorkOrderStatus.ON_HOLD),
                _wo("HELD_SHORT", days_ago=5, status=WorkOrderStatus.ON_HOLD),
                _wo("OPEN_LONG", days_ago=40, status=WorkOrderStatus.ASSIGNED),
            ]
        )
        flagged = aging.on_hold_abuse(small_store, now=NOW, days=21)
        assert [wo.id for wo in flagged] == ["HELD_LONG"]

    def test_report_buckets_sum_to_total_open(self, store):
        report = aging.build_report(store, now=NOW)
        assert sum(report.buckets.values()) == report.total_open
        assert report.total_open == len(store.work_orders(open_only=True))


class TestKpi:
    def test_first_time_fix_counts_repeats_on_same_asset(self, small_store):
        small_store.upsert_locations([Location(id="L", building="B")])
        small_store.upsert_assets(
            [Asset(id="A1", tag="A1", name="A1", category="Pump", location_id="L")]
        )
        small_store.upsert_technicians([Technician(id="T1")])
        # Two completions 5 days apart on one asset: one is a repeat.
        small_store.upsert_work_orders(
            [
                _wo("W1", days_ago=40, status=WorkOrderStatus.COMPLETED,
                    closed_days_ago=35, asset_id="A1"),
                _wo("W2", days_ago=36, status=WorkOrderStatus.COMPLETED,
                    closed_days_ago=30, asset_id="A1"),
            ]
        )
        # 1 repeat out of 2 completions -> 50%.
        assert kpi.first_time_fix_rate(small_store.work_orders()) == 0.5

    def test_repeat_outside_window_is_not_counted(self, small_store):
        small_store.upsert_locations([Location(id="L", building="B")])
        small_store.upsert_assets(
            [Asset(id="A1", tag="A1", name="A1", category="Pump", location_id="L")]
        )
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_work_orders(
            [
                _wo("W1", days_ago=200, status=WorkOrderStatus.COMPLETED,
                    closed_days_ago=190, asset_id="A1"),
                _wo("W2", days_ago=40, status=WorkOrderStatus.COMPLETED,
                    closed_days_ago=30, asset_id="A1"),
            ]
        )
        assert kpi.first_time_fix_rate(small_store.work_orders()) == 1.0

    def test_only_self_is_named(self, store):
        """Everyone but the current user shows as "Tech N" — there is no flag
        that reveals more, because no other name is stored."""
        report = kpi.build_report(store, now=NOW)
        assert report.technicians
        peers = [t for t in report.technicians if not t.is_self]
        assert peers
        assert all(t.label.startswith("Tech ") for t in peers)
        assert sum(1 for t in report.technicians if t.is_self) == 1

    def test_build_report_takes_no_reveal_argument(self, store):
        with pytest.raises(TypeError):
            kpi.build_report(store, now=NOW, reveal=True)

    def test_display_name_falls_back_when_unlabelled(self):
        """An unlabelled technician still renders as something, never a name."""
        assert Technician(id="abc123").display_name().startswith("Tech ")
        assert Technician(id="x", name="Real Person").display_name().startswith("Tech ")

    def test_backlog_growth_is_opened_minus_closed(self, store):
        report = kpi.build_report(store, window_days=90, now=NOW)
        assert report.backlog_growth == report.opened - report.completed


class TestRouting:
    def test_shortest_paths_are_transitive(self):
        edges = {("A", "B"): 5.0, ("B", "A"): 5.0, ("B", "C"): 7.0, ("C", "B"): 7.0}
        dist = routing.shortest_paths(edges)
        assert dist[("A", "C")] == 12.0
        assert dist[("A", "A")] == 0.0

    def test_same_floor_is_cheapest(self, store):
        router = routing.Router(store)
        a = Location(id="1", building="X", floor="1", room="R1")
        b = Location(id="2", building="X", floor="1", room="R2")
        c = Location(id="3", building="X", floor="2", room="R3")
        assert router.travel_minutes(a, b) < router.travel_minutes(a, c)

    def test_emergency_is_sequenced_first_despite_distance(self, small_store):
        small_store.upsert_locations(
            [
                Location(id="NEAR", building="Mechanical Shop", floor="1"),
                Location(id="FAR", building="Yard Office", floor="1"),
            ]
        )
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_work_orders(
            [
                _wo("NEARBY", days_ago=1, priority=Priority.LOW, location_id="NEAR"),
                _wo("URGENT", days_ago=1, priority=Priority.EMERGENCY, location_id="FAR"),
            ]
        )
        route = routing.Router(small_store).plan(
            small_store.work_orders(open_only=True),
            start_building="Mechanical Shop",
            now=NOW,
        )
        assert route.stops[0].work_order.id == "URGENT"
        assert "emergency" in route.stops[0].reason

    def test_work_orders_without_location_are_unrouted(self, small_store):
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_work_orders([_wo("NOLOC", days_ago=1, location_id=None)])
        route = routing.Router(small_store).plan(
            small_store.work_orders(open_only=True), start_building="Mechanical Shop", now=NOW
        )
        assert route.stops == []
        assert [wo.id for wo in route.unrouted] == ["NOLOC"]

    def test_max_stops_is_respected(self, store):
        route = routing.Router(store).plan(
            store.work_orders(open_only=True), start_building="Mechanical Shop",
            now=NOW, max_stops=4,
        )
        assert len(route.stops) == 4

    def test_part_suggestions_rank_this_units_history_highest(self, small_store):
        small_store.upsert_locations([Location(id="L", building="B")])
        small_store.upsert_assets(
            [
                Asset(id="A1", tag="A1", name="A1", category="Pump", location_id="L"),
                Asset(id="A2", tag="A2", name="A2", category="Pump", location_id="L"),
            ]
        )
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_parts(
            [
                Part(id="P1", sku="SEAL-1", name="Seal", on_hand=5),
                Part(id="P2", sku="BRG-1", name="Bearing", on_hand=5),
            ]
        )
        small_store.upsert_work_orders(
            [
                _wo("H1", days_ago=60, status=WorkOrderStatus.COMPLETED,
                    closed_days_ago=59, asset_id="A1"),
                _wo("H2", days_ago=50, status=WorkOrderStatus.COMPLETED,
                    closed_days_ago=49, asset_id="A2"),
                _wo("NOW", days_ago=1, asset_id="A1"),
            ]
        )
        small_store.upsert_part_usage(
            [
                # Seal used on THIS unit; bearing only on a sibling of same category.
                PartUsage(id="U1", work_order_id="H1", part_id="P1", quantity=1, used_at=NOW),
                PartUsage(id="U2", work_order_id="H2", part_id="P2", quantity=9, used_at=NOW),
            ]
        )
        target = next(wo for wo in small_store.work_orders() if wo.id == "NOW")
        suggestions = routing.suggest_parts(small_store, target)
        assert suggestions[0]["sku"] == "SEAL-1"
        assert "this unit" in suggestions[0]["why"]


class TestInventory:
    def test_days_of_cover_from_observed_burn(self, small_store):
        small_store.upsert_locations([Location(id="L", building="B")])
        small_store.upsert_assets(
            [Asset(id="A1", tag="A1", name="A1", category="Pump", location_id="L")]
        )
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_parts(
            [Part(id="P1", sku="S", name="Seal", on_hand=90, reorder_point=5)]
        )
        small_store.upsert_work_orders(
            [_wo("W", days_ago=10, status=WorkOrderStatus.COMPLETED,
                 closed_days_ago=9, asset_id="A1")]
        )
        # 180 units over a 180-day window = 1/day; 90 on hand = 90 days cover.
        small_store.upsert_part_usage(
            [PartUsage(id="U", work_order_id="W", part_id="P1", quantity=180,
                       used_at=NOW - timedelta(days=9))]
        )
        report = inventory.build_report(small_store, window_days=180, now=NOW)
        status = report.parts[0]
        assert status.daily_burn == 1.0
        assert status.days_of_cover == 90.0
        assert status.suggested_reorder_point == 21  # 1/day * 14 * 1.5

    def test_zero_burn_has_no_cover_figure(self, small_store):
        small_store.upsert_parts([Part(id="P1", sku="S", name="Seal", on_hand=3)])
        status = inventory.build_report(small_store, now=NOW).parts[0]
        assert status.days_of_cover is None
        assert status.suggested_reorder_point == 0

    def test_out_of_stock_sorts_first(self, small_store):
        small_store.upsert_parts(
            [
                Part(id="P1", sku="HAVE", name="Have", on_hand=50, reorder_point=1),
                Part(id="P2", sku="NONE", name="None", on_hand=0, reorder_point=1),
            ]
        )
        report = inventory.build_report(small_store, now=NOW)
        assert report.parts[0].part.sku == "NONE"
        assert report.parts[0].flag == "out of stock"

    def test_reorder_cost_sums_only_flagged_parts(self, small_store):
        small_store.upsert_parts(
            [
                Part(id="P1", sku="LOW", name="Low", on_hand=1, reorder_point=5,
                     reorder_quantity=10, unit_cost=2.0),
                Part(id="P2", sku="OK", name="Ok", on_hand=99, reorder_point=5,
                     reorder_quantity=10, unit_cost=100.0),
            ]
        )
        assert inventory.build_report(small_store, now=NOW).reorder_cost == 20.0


class TestRegistry:
    def test_replacement_signal_needs_age_and_repairs(self, small_store):
        small_store.upsert_locations([Location(id="L", building="B")])
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_assets(
            [
                Asset(id="OLD", tag="OLD", name="Old", category="Pump", location_id="L",
                      installed_on=NOW - timedelta(days=365 * 30), expected_life_years=20),
                Asset(id="NEW", tag="NEW", name="New", category="Pump", location_id="L",
                      installed_on=NOW - timedelta(days=365), expected_life_years=20),
            ]
        )
        small_store.upsert_work_orders(
            [_wo(f"R{i}", days_ago=100 + i, asset_id="OLD",
                 status=WorkOrderStatus.COMPLETED, closed_days_ago=99 + i)
             for i in range(7)]
        )
        assert registry.dossier(small_store, "OLD").replacement_signal(now=NOW).startswith(
            "replace"
        )
        assert registry.dossier(small_store, "NEW").replacement_signal(now=NOW) == "ok"

    def test_recurring_faults_needs_more_than_one(self, small_store):
        small_store.upsert_locations([Location(id="L", building="B")])
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_assets(
            [Asset(id="A", tag="A", name="A", category="Pump", location_id="L")]
        )
        small_store.upsert_work_orders(
            [
                _wo("W1", days_ago=30, asset_id="A", title="Leak"),
                _wo("W2", days_ago=20, asset_id="A", title="Leak"),
                _wo("W3", days_ago=10, asset_id="A", title="Noise"),
            ]
        )
        assert registry.dossier(small_store, "A").recurring_faults == [("Leak", 2)]

    def test_manual_join_is_case_insensitive(self, store):
        asset = next(a for a in store.assets() if a.manufacturer and a.model)
        assert store.manual_for_asset(asset) is not None

    def test_search_filters_combine(self, store):
        building = "Mechanical Shop"
        found = registry.find_assets(store, building=building)
        locations = {loc.id: loc for loc in store.locations()}
        assert found
        assert all(locations[a.location_id].building == building for a in found)
