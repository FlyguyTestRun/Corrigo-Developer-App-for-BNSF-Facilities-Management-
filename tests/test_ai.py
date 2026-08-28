"""Field-note drafting and job briefings.

The extraction rules feed labor records and stock counts, so they are tested
for precision rather than recall: it is far better to leave a field blank for
the technician to fill than to put a wrong number on a billable record.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bnsf_fm.ai import notes, suggest
from bnsf_fm.models import (
    Asset,
    Location,
    Part,
    PartUsage,
    Technician,
    WorkOrder,
    WorkOrderStatus,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


class TestExtraction:
    @pytest.mark.parametrize(
        ("note", "expected"),
        [
            ("took 1.5hr", 1.5), ("2 hours on site", 2.0), ("spent 45 min", 0.75),
            ("3h", 3.0), ("0.25 hrs", 0.25), ("90 minutes", 1.5),
        ],
    )
    def test_hours_are_extracted_including_minutes(self, note, expected):
        assert notes.extract_hours(note) == expected

    @pytest.mark.parametrize(
        "note", ["fixed it", "an hour and a half", "took a while", ""]
    )
    def test_ambiguous_time_is_left_blank_rather_than_guessed(self, note):
        assert notes.extract_hours(note) is None

    def test_on_hold_beats_completed_when_both_appear(self):
        """'Running, but waiting on parts for the guard' is not complete."""
        note = "replaced the belt, unit running, waiting on parts for the guard"
        assert notes.extract_status(note) is WorkOrderStatus.ON_HOLD

    @pytest.mark.parametrize(
        ("note", "expected"),
        [
            ("all done, unit running", WorkOrderStatus.COMPLETED),
            ("on order, will return", WorkOrderStatus.ON_HOLD),
            ("still working this one", WorkOrderStatus.IN_PROGRESS),
            ("looked at it", None),
        ],
    )
    def test_status_inference(self, note, expected):
        assert notes.extract_status(note) is expected

    def test_parts_matched_by_sku_and_by_full_name(self):
        catalog = [
            Part(id="P1", sku="CNT-40A", name="Contactor 40A 24V", on_hand=5),
            Part(id="P2", sku="BLT-A48", name="V-Belt A48", on_hand=5),
        ]
        by_sku = notes.extract_parts("swapped a CNT-40A today", catalog)
        assert [p.sku for p in by_sku] == ["CNT-40A"]
        by_name = notes.extract_parts("installed a new V-Belt A48", catalog)
        assert [p.sku for p in by_name] == ["BLT-A48"]

    def test_quantities_are_read_when_adjacent(self):
        catalog = [Part(id="P1", sku="FLT-2020", name="Filter", on_hand=50)]
        assert notes.extract_parts("replaced 12 FLT-2020", catalog)[0].quantity == 12
        assert notes.extract_parts("used (4) FLT-2020", catalog)[0].quantity == 4
        assert notes.extract_parts("used FLT-2020", catalog)[0].quantity == 1

    def test_generic_words_do_not_claim_a_specific_sku(self):
        """'belt' must not be guessed onto a particular part number."""
        catalog = [Part(id="P2", sku="BLT-A48", name="V-Belt A48", on_hand=5)]
        assert notes.extract_parts("replaced the belt", catalog) == []

    def test_insufficient_stock_is_flagged(self):
        catalog = [Part(id="P1", sku="FLT-2020", name="Filter", on_hand=2)]
        line = notes.extract_parts("replaced 12 FLT-2020", catalog)[0]
        assert line.in_stock is False


class TestDraft:
    @pytest.fixture
    def seeded(self, small_store):
        small_store.upsert_locations([Location(id="L", building="B", floor="1")])
        small_store.upsert_technicians([Technician(id="T1", name="Tech One")])
        small_store.upsert_assets(
            [Asset(id="A1", tag="AHU-1", name="Air Handler 1",
                   category="Air Handling Unit", location_id="L")]
        )
        small_store.upsert_parts(
            [Part(id="P1", sku="CNT-40A", name="Contactor 40A 24V", on_hand=6)]
        )
        small_store.upsert_work_orders(
            [
                WorkOrder(id="W1", number="100001", title="No cooling",
                          status=WorkOrderStatus.IN_PROGRESS, asset_id="A1",
                          location_id="L", assigned_to="T1",
                          opened_at=NOW - timedelta(days=3))
            ]
        )
        return small_store

    def test_draft_is_produced_without_any_model(self, seeded):
        draft = notes.draft_update(
            seeded, "100001",
            "swapped bad contactor, used 1 CNT-40A, 1.5hr, unit running",
            use_model=False,
        )
        assert draft.generated_by == "rules"
        assert draft.labor_hours == 1.5
        assert [p.sku for p in draft.parts] == ["CNT-40A"]
        assert draft.proposed_status is WorkOrderStatus.COMPLETED

    def test_draft_never_claims_to_have_submitted(self, seeded):
        draft = notes.draft_update(seeded, "100001", "did the thing, 1hr", use_model=False)
        assert draft.to_dict()["requires_human_submission"] is True
        assert "draft" in draft.render().lower()

    def test_missing_hours_produces_a_warning(self, seeded):
        draft = notes.draft_update(seeded, "100001", "fixed it", use_model=False)
        assert draft.labor_hours is None
        assert any("no labor time" in w for w in draft.warnings)

    def test_complete_with_no_hours_is_called_out(self, seeded):
        draft = notes.draft_update(seeded, "100001", "all done, running", use_model=False)
        assert any("no labor logged" in w for w in draft.warnings)

    def test_unknown_work_order_raises(self, seeded):
        with pytest.raises(KeyError, match="999999"):
            notes.draft_update(seeded, "999999", "note", use_model=False)

    def test_model_failure_degrades_to_rules(self, seeded, monkeypatch):
        """No API key must not break the feature."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        draft = notes.draft_update(seeded, "100001", "swapped contactor, 1hr", use_model=True)
        assert draft.generated_by == "rules"
        assert draft.resolution
        assert any("rule-based" in w for w in draft.warnings)


class TestBriefing:
    @pytest.fixture
    def seeded(self, small_store):
        small_store.upsert_locations([Location(id="L", building="B", floor="1")])
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_assets(
            [Asset(id="A1", tag="P-1", name="Pump 1", category="Pump", location_id="L")]
        )
        small_store.upsert_parts([Part(id="P1", sku="SEAL-1", name="Seal", on_hand=4)])
        history = [
            WorkOrder(id=f"H{i}", number=f"2000{i}", title="Seal leaking at shaft",
                      status=WorkOrderStatus.COMPLETED, asset_id="A1", location_id="L",
                      assigned_to="T1", opened_at=NOW - timedelta(days=60 - i * 10),
                      closed_at=NOW - timedelta(days=59 - i * 10),
                      resolution="Replaced mechanical seal.")
            for i in range(3)
        ]
        current = WorkOrder(id="W1", number="100001", title="Seal leaking at shaft",
                            status=WorkOrderStatus.ASSIGNED, asset_id="A1", location_id="L",
                            assigned_to="T1", opened_at=NOW - timedelta(days=1))
        small_store.upsert_work_orders([*history, current])
        small_store.upsert_part_usage(
            [PartUsage(id=f"U{i}", work_order_id=f"H{i}", part_id="P1",
                       quantity=1, used_at=NOW - timedelta(days=59 - i * 10))
             for i in range(3)]
        )
        return small_store

    def test_prior_resolutions_become_likely_causes(self, seeded):
        briefing = suggest.brief(seeded, "100001", now=NOW)
        assert "Replaced mechanical seal." in briefing.likely_causes
        assert len(briefing.prior_similar) == 3

    def test_repeat_fault_raises_a_caution(self, seeded):
        briefing = suggest.brief(seeded, "100001", now=NOW)
        assert any("closed 3 times" in c for c in briefing.cautions)

    def test_parts_are_suggested_from_this_units_history(self, seeded):
        briefing = suggest.brief(seeded, "100001", now=NOW)
        assert briefing.parts_to_stage[0]["sku"] == "SEAL-1"

    def test_work_order_without_an_asset_says_so_usefully(self, small_store):
        small_store.upsert_technicians([Technician(id="T1")])
        small_store.upsert_work_orders(
            [WorkOrder(id="W1", number="100001", title="Something",
                       status=WorkOrderStatus.NEW, opened_at=NOW - timedelta(days=1))]
        )
        briefing = suggest.brief(small_store, "100001", now=NOW)
        assert briefing.parts_to_stage == []
        assert any("No asset is linked" in c for c in briefing.cautions)

    def test_missing_manual_is_flagged(self, seeded):
        briefing = suggest.brief(seeded, "100001", now=NOW)
        assert any("No manual on file" in c for c in briefing.cautions)

    def test_unknown_work_order_raises(self, seeded):
        with pytest.raises(KeyError):
            suggest.brief(seeded, "999999", now=NOW)

    def test_render_is_readable_text(self, seeded):
        rendered = suggest.brief(seeded, "100001", now=NOW).render()
        assert "WO 100001" in rendered
        assert "P-1" in rendered
