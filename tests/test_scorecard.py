"""Scorecard and data-quality maths.

Verified against a fixture small enough to work out on paper. These numbers go
in front of a manager and into a promotion case, so "looks about right" is not
a standard they can be held to.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bnsf_fm.analytics import quality, scorecard
from bnsf_fm.models import Location, Technician, WorkOrder, WorkOrderStatus
from bnsf_fm.report import html as html_report

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _wo(wo_id, *, tech, days_ago, closed_days_ago=None, asset_id=None, location_id=None):
    return WorkOrder(
        id=wo_id,
        number=wo_id,
        title="Fault",
        status=(
            WorkOrderStatus.COMPLETED if closed_days_ago is not None
            else WorkOrderStatus.ASSIGNED
        ),
        assigned_to=tech,
        asset_id=asset_id,
        location_id=location_id,
        opened_at=NOW - timedelta(days=days_ago),
        closed_at=(NOW - timedelta(days=closed_days_ago)) if closed_days_ago is not None else None,
        resolution="Fixed." if closed_days_ago is not None else None,
    )


@pytest.fixture
def crew(small_store):
    """Three technicians. ME closes 6, PEER1 closes 3, PEER2 closes 1.

    Department total 10, so ME's share is exactly 60%, and an even split across
    3 active technicians would be 33.3% — 1.8x par.
    """
    small_store.upsert_locations([Location(id="L", building="B")])
    small_store.upsert_technicians(
        [
            Technician(id="ME", name="Bryan Shaw", is_self=True),
            Technician(id="PEER1"),
            Technician(id="PEER2"),
        ]
    )
    orders = []
    # ME: 6 completions, each closing in exactly 1 day.
    for i in range(6):
        orders.append(_wo(f"M{i}", tech="ME", days_ago=30 + i, closed_days_ago=29 + i))
    # PEER1: 3 completions, each taking 5 days.
    for i in range(3):
        orders.append(_wo(f"A{i}", tech="PEER1", days_ago=30 + i, closed_days_ago=25 + i))
    # PEER2: 1 completion taking 9 days.
    orders.append(_wo("B0", tech="PEER2", days_ago=30, closed_days_ago=21))
    small_store.upsert_work_orders(orders)
    return small_store


class TestScorecard:
    def test_share_of_department_output(self, crew):
        card = scorecard.build(crew, window_days=365, now=NOW)
        assert card.my_completed == 6
        assert card.department_completed == 10
        assert card.my_share == pytest.approx(0.6)

    def test_even_split_baseline_counts_only_active_technicians(self, crew):
        """Counting dormant accounts would shrink the denominator and inflate
        the share — flattering arithmetic a reviewer would catch."""
        crew.upsert_technicians([Technician(id="DORMANT")])
        card = scorecard.build(crew, window_days=365, now=NOW)
        assert card.department_size == 3
        assert card.even_split_share == pytest.approx(1 / 3)
        assert card.share_vs_even == pytest.approx(1.8)

    def test_cycle_time_comparison_against_peer_median(self, crew):
        # ME 1 day; peers are 5 and 9, so the peer median is 7.
        card = scorecard.build(crew, window_days=365, now=NOW)
        cycle = card.comparison("Median days to completion")
        assert cycle is not None
        assert cycle.mine == pytest.approx(1.0, abs=0.01)
        assert cycle.department_median == pytest.approx(7.0, abs=0.01)
        assert cycle.favorable is True   # lower is better
        assert cycle.rank == 1

    def test_volume_rank_and_percentile(self, crew):
        card = scorecard.build(crew, window_days=365, now=NOW)
        volume = card.comparison("Completed work orders")
        assert volume is not None
        assert (volume.rank, volume.of) == (1, 3)
        assert volume.percentile == pytest.approx(1.0)  # beats both peers

    def test_percentile_handles_ties_without_inflating(self, small_store):
        small_store.upsert_technicians(
            [Technician(id="ME", name="Me", is_self=True), Technician(id="P1")]
        )
        small_store.upsert_work_orders(
            [
                _wo("M0", tech="ME", days_ago=10, closed_days_ago=9),
                _wo("A0", tech="P1", days_ago=10, closed_days_ago=9),
            ]
        )
        card = scorecard.build(small_store, window_days=365, now=NOW)
        volume = card.comparison("Completed work orders")
        # One peer, matched not beaten: half credit, not full.
        assert volume.percentile == pytest.approx(0.5)

    def test_volume_caveat_is_always_present(self, crew):
        """Volume reflects assignment, which a technician does not control.
        Handing a manager the number without that note invites the one
        objection that sinks it."""
        card = scorecard.build(crew, window_days=365, now=NOW)
        assert any("how work is assigned" in c for c in card.caveats)

    def test_solo_export_says_the_comparison_is_empty(self, small_store):
        small_store.upsert_technicians([Technician(id="ME", name="Me", is_self=True)])
        small_store.upsert_work_orders([_wo("M0", tech="ME", days_ago=10, closed_days_ago=9)])
        card = scorecard.build(small_store, window_days=365, now=NOW)
        assert any("only your own work orders" in c for c in card.caveats)

    def test_missing_labor_is_called_out(self, crew):
        card = scorecard.build(crew, window_days=365, now=NOW)
        assert any("No labor hours" in c for c in card.caveats)

    def test_no_self_identified_raises_with_a_usable_message(self, small_store):
        small_store.upsert_technicians([Technician(id="P1")])
        small_store.upsert_work_orders([_wo("A0", tech="P1", days_ago=10, closed_days_ago=9)])
        with pytest.raises(scorecard.NoSelfIdentified, match="--me"):
            scorecard.build(small_store, window_days=365, now=NOW)


class TestQuality:
    def test_counts_missing_fields(self, crew):
        report = quality.build(crew, now=NOW)
        by_field = {f.field: f for f in report.findings}
        # The crew fixture links no assets and logs no labor.
        assert by_field["linked asset"].missing == 10
        assert by_field["linked asset"].severity == "blocking"
        assert by_field["labor hours logged"].missing == 10
        # Every work order has an assignee.
        assert by_field["assigned technician"].missing == 0

    def test_anonymization_is_verified_not_asserted(self, crew):
        report = quality.build(crew, now=NOW)
        assert report.names_leaked == []
        assert report.to_dict()["anonymization_ok"] is True

    def test_a_leaked_name_is_reported(self, crew):
        crew.upsert_technicians([Technician(id="OOPS", name="Dana Whitfield")])
        report = quality.build(crew, now=NOW)
        assert report.names_leaked == ["Dana Whitfield"]
        assert report.to_dict()["anonymization_ok"] is False

    def test_date_coverage(self, crew):
        report = quality.build(crew, now=NOW)
        start, end = report.date_range
        assert start is not None and end is not None
        assert report.coverage_days == 5


class TestHtmlReport:
    def test_page_is_self_contained_and_anonymous(self, crew):
        card = scorecard.build(crew, window_days=365, now=NOW)
        page = html_report.render(card, quality=quality.build(crew, now=NOW))
        assert page.startswith("<!doctype html>")
        assert "<link rel=\"stylesheet\"" not in page
        assert "http://" not in page and "https://" not in page
        assert "Bryan Shaw" in page          # you, by name
        assert "Tech 2" in page              # a peer, by label
        assert "PEER1" not in page           # never the raw surrogate

    def test_percentage_deltas_are_in_points_not_percent_of_percent(self, crew):
        card = scorecard.build(crew, window_days=365, now=NOW)
        sla = card.comparison("SLA met rate")
        glyph, wording, _role = html_report._delta_sentence(sla)
        assert "percentage points" in wording or "level with" in wording

    def test_delta_wording_never_relies_on_colour_alone(self, crew):
        card = scorecard.build(crew, window_days=365, now=NOW)
        for comparison in card.comparisons:
            glyph, wording, role = html_report._delta_sentence(comparison)
            assert glyph in {"▲", "▼", "="}
            assert wording, "a status colour must always be paired with words"
            assert role in {"good", "critical", "neutral"}

    def test_html_escapes_untrusted_text(self, crew):
        crew.upsert_technicians(
            [Technician(id="ME", name="<script>alert(1)</script>", is_self=True)]
        )
        card = scorecard.build(crew, window_days=365, now=NOW)
        page = html_report.render(card)
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page
