"""HTTP surface and CLI.

The privacy assertions here are the important ones: the default response from
every endpoint — the one that gets screenshotted, pasted into a deck, or cached
by a browser — must not carry real technician names.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from bnsf_fm.ingest import CAMPUS_EDGES, FixtureSource, load
from bnsf_fm.store import Store

fastapi_testclient = pytest.importorskip("fastapi.testclient")


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    with Store(path) as store:
        load(FixtureSource(now=datetime(2026, 8, 28, tzinfo=UTC)), store)
        store.set_campus_edges(CAMPUS_EDGES)
    monkeypatch.setenv("BNSF_FM_DB", str(path))
    return path


@pytest.fixture
def client(db_path):
    import importlib

    from bnsf_fm.api import app as app_module

    importlib.reload(app_module)  # pick up BNSF_FM_DB
    return fastapi_testclient.TestClient(app_module.app)


class TestApi:
    def test_health_reports_row_counts(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["work_orders"] == 2000
        assert body["assets"] == 200

    def test_backlog_returns_buckets_and_stalled(self, client):
        body = client.get("/backlog").json()
        assert sum(body["buckets"].values()) == body["total_open"]
        assert body["stalled_count"] == len(body["stalled"])

    def test_backlog_hides_names_by_default(self, client):
        body = client.get("/backlog").json()
        assert all(item["assigned_to"] is None for item in body["stalled"])

    def test_kpis_pseudonymize_by_default(self, client):
        body = client.get("/kpis").json()
        assert body["technicians"]
        assert all(t["technician"].startswith("TECH-") for t in body["technicians"])

    def test_kpis_reveal_is_explicit_opt_in(self, client):
        body = client.get("/kpis", params={"reveal_names": "true"}).json()
        assert any(t["technician"].startswith("Technician ") for t in body["technicians"])

    def test_work_order_filters_apply(self, client):
        buildings = client.get("/buildings").json()
        assert buildings
        rows = client.get(
            "/work-orders", params={"building": buildings[0], "min_days_open": 10}
        ).json()
        assert all(r["days_open"] >= 10 for r in rows)
        assert all(r["location"].startswith(buildings[0]) for r in rows)

    def test_work_orders_sorted_oldest_first(self, client):
        rows = client.get("/work-orders", params={"limit": 20}).json()
        ages = [r["days_open"] for r in rows]
        assert ages == sorted(ages, reverse=True)

    def test_asset_detail_includes_history(self, client):
        tag = client.get("/assets", params={"limit": 1}).json()[0]["tag"]
        body = client.get(f"/assets/{tag}").json()
        assert body["tag"] == tag
        assert "work_orders" in body

    def test_unknown_asset_is_404(self, client):
        assert client.get("/assets/NOPE-9999").status_code == 404

    def test_route_includes_parts_to_stage(self, client):
        body = client.get(
            "/route", params={"start_building": "Mechanical Shop", "max_stops": 3}
        ).json()
        assert len(body["stops"]) == 3
        assert all("parts_to_stage" in stop for stop in body["stops"])

    def test_inventory_reorder_filter(self, client):
        full = client.get("/inventory").json()
        only = client.get("/inventory", params={"only_reorder": "true"}).json()
        assert len(only["parts"]) == full["reorder_count"] <= len(full["parts"])

    def test_draft_endpoint_writes_nothing_and_says_so(self, client):
        wo = client.get("/work-orders", params={"limit": 1}).json()[0]["number"]
        body = client.post(
            "/draft",
            json={"work_order_number": wo, "note": "swapped contactor, 1.5hr", "use_model": False},
        ).json()
        assert body["requires_human_submission"] is True
        assert body["labor_hours"] == 1.5
        assert "rendered" in body

    def test_draft_for_unknown_work_order_is_404(self, client):
        response = client.post(
            "/draft", json={"work_order_number": "999999", "note": "x"}
        )
        assert response.status_code == 404

    def test_briefing_endpoint(self, client):
        wo = client.get("/work-orders", params={"limit": 1}).json()[0]["number"]
        body = client.get(f"/briefing/{wo}").json()
        assert body["work_order"] == wo

    def test_limit_bounds_are_enforced(self, client):
        assert client.get("/work-orders", params={"limit": 9999}).status_code == 422
        assert client.get("/kpis", params={"window_days": 0}).status_code == 422


class TestCli:
    def _run(self, argv, db):
        from bnsf_fm.cli import main

        return main([*argv, "--db", str(db)])

    def test_seed_creates_a_populated_store(self, tmp_path, capsys):
        from bnsf_fm.cli import main

        path = tmp_path / "seeded.db"
        assert main(["seed", "--db", str(path), "--work-orders", "50", "--assets", "10"]) == 0
        assert "work_orders" in capsys.readouterr().out
        with Store(path) as store:
            assert store.count("work_orders") == 50

    def test_backlog_renders(self, db_path, capsys):
        assert self._run(["backlog"], db_path) == 0
        out = capsys.readouterr().out
        assert "OPEN WORK ORDERS" in out
        assert "STALLED" in out

    def test_json_output_is_parseable(self, db_path, capsys):
        assert self._run(["backlog", "--json"], db_path) == 0
        payload = json.loads(capsys.readouterr().out)
        assert "buckets" in payload

    def test_kpis_note_pseudonymization(self, db_path, capsys):
        assert self._run(["kpis"], db_path) == 0
        assert "pseudonymized" in capsys.readouterr().out

    def test_route_renders_stops(self, db_path, capsys):
        assert self._run(["route", "--stops", "3"], db_path) == 0
        assert "ROUTE from" in capsys.readouterr().out

    def test_inventory_renders(self, db_path, capsys):
        assert self._run(["inventory"], db_path) == 0
        assert "INVENTORY" in capsys.readouterr().out

    def test_unknown_asset_exits_nonzero(self, db_path, capsys):
        assert self._run(["asset", "NOPE-9999"], db_path) == 1

    def test_draft_from_the_command_line(self, db_path, capsys):
        with Store(db_path) as store:
            number = store.work_orders(open_only=True)[0].number
        assert self._run(["draft", number, "swapped contactor, 2hr"], db_path) == 0
        assert "Labor: 2 h" in capsys.readouterr().out

    def test_load_api_without_credentials_fails_cleanly(self, db_path, monkeypatch, capsys):
        for name in ("CORRIGO_CLIENT_ID", "CORRIGO_CLIENT_SECRET", "CORRIGO_COMPANY_NAME"):
            monkeypatch.delenv(name, raising=False)
        assert self._run(["load-api"], db_path) == 2
        assert "system administrator" in capsys.readouterr().err


class TestMcpTools:
    def test_every_tool_is_callable_and_returns_json_safe_data(self, db_path):
        import importlib

        from bnsf_fm.mcp import server as server_module

        importlib.reload(server_module)
        with Store(db_path) as store:
            number = store.work_orders(open_only=True)[0].number

        results = {
            "backlog_summary": server_module.backlog_summary(),
            "open_work_orders": server_module.open_work_orders(limit=5),
            "team_kpis": server_module.team_kpis(),
            "search_assets": server_module.search_assets(limit=5),
            "replacement_candidates": server_module.replacement_candidates(limit=3),
            "plan_route": server_module.plan_route("Mechanical Shop", max_stops=3),
            "parts_to_stage": server_module.parts_to_stage(number),
            "inventory_status": server_module.inventory_status(),
            "job_briefing": server_module.job_briefing(number),
            "draft_work_order_update": server_module.draft_work_order_update(
                number, "swapped contactor, 1hr"
            ),
        }
        for name, value in results.items():
            json.dumps(value), f"{name} is not JSON-serializable"
        assert results["backlog_summary"]["total_open"] > 0
        assert len(results["open_work_orders"]) == 5

    def test_tools_pseudonymize_by_default(self, db_path):
        import importlib

        from bnsf_fm.mcp import server as server_module

        importlib.reload(server_module)
        kpis = server_module.team_kpis()
        assert all(t["technician"].startswith("TECH-") for t in kpis["technicians"])

    def test_unknown_work_order_returns_an_error_not_an_exception(self, db_path):
        import importlib

        from bnsf_fm.mcp import server as server_module

        importlib.reload(server_module)
        assert "error" in server_module.job_briefing("999999")
        assert "error" in server_module.draft_work_order_update("999999", "x")
