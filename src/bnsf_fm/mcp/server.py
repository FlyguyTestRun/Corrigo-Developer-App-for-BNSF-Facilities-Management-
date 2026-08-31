"""MCP server exposing the facilities store to Claude.

This is what makes the project agentic rather than just a dashboard. Once
registered, Claude can answer questions nobody built a screen for — "what's
open in the Central Utility Plant over fourteen days", "which chillers are past
expected life and repair-heavy", "what should I stage for WO 100412" — against
the same store the dashboard reads.

Read-only by design. Every tool here queries; `draft_work_order_update` returns
a draft for a human to submit, and nothing writes back to Corrigo.

Register with:

    claude mcp add bnsf-fm -- /path/to/.venv/bin/python -m bnsf_fm.mcp.server

Tool results are shaped for reading, not machine consumption: numbers are
rounded to the precision a person would actually quote, and co-workers appear
as "Tech N" — not by policy but because their names were discarded at ingest
and are not in the database to return.
"""

from __future__ import annotations

import json
import os
from typing import Any

from bnsf_fm.ai import notes, suggest
from bnsf_fm.analytics import aging, inventory, kpi, registry, routing
from bnsf_fm.store import DEFAULT_DB_PATH, Store

DB_PATH = os.environ.get("BNSF_FM_DB", str(DEFAULT_DB_PATH))


def _store() -> Store:
    return Store(DB_PATH)


# -- tool implementations ----------------------------------------------------
# Kept as plain functions so they are unit-testable without an MCP runtime.


def backlog_summary(stalled_limit: int = 15) -> dict[str, Any]:
    """Aging distribution, SLA breaches, and the stalled work order list."""
    with _store() as store:
        report = aging.build_report(store)
        data = report.to_dict()
        data["stalled"] = data["stalled"][:stalled_limit]  # type: ignore[index]
        return data


def open_work_orders(
    building: str | None = None,
    min_days_open: float = 0.0,
    priority: str | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Open work orders, filtered by building, age, or priority."""
    with _store() as store:
        locations = {loc.id: loc for loc in store.locations()}
        labor = store.labor_hours_by_work_order()
        out = []
        for wo in store.work_orders(open_only=True):
            loc = locations.get(wo.location_id or "")
            if building and (not loc or loc.building != building):
                continue
            if wo.age_days() < min_days_open:
                continue
            if priority and str(wo.priority) != priority.lower():
                continue
            out.append(
                {
                    "number": wo.number,
                    "title": wo.title,
                    "status": str(wo.status),
                    "priority": str(wo.priority),
                    "days_open": round(wo.age_days(), 1),
                    "hours_logged": round(labor.get(wo.id, 0.0), 2),
                    "past_sla": wo.breached_sla(),
                    "location": loc.label if loc else None,
                }
            )
        out.sort(key=lambda r: r["days_open"], reverse=True)
        return out[:limit]


def team_kpis(window_days: int = 90) -> dict[str, Any]:
    """Team and per-technician KPIs over a trailing window."""
    with _store() as store:
        return kpi.build_report(store, window_days=window_days).to_dict()


def asset_history(asset_tag: str) -> dict[str, Any]:
    """Everything known about one asset: manual, service history, faults."""
    with _store() as store:
        matches = registry.find_assets(store, query=asset_tag)
        if not matches:
            return {"error": f"No asset matching {asset_tag!r}"}
        d = registry.dossier(store, matches[0].id)
        if d is None:
            return {"error": f"Asset {asset_tag!r} not found in registry"}
        data = d.to_dict()
        data["recent_work"] = [
            {
                "number": wo.number,
                "title": wo.title,
                "status": str(wo.status),
                "opened": wo.opened_at.date().isoformat(),
                "resolution": wo.resolution,
            }
            for wo in d.work_orders[:10]
        ]
        return data


def search_assets(
    query: str | None = None,
    building: str | None = None,
    category: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Find assets by free text, building, or category."""
    with _store() as store:
        locations = {loc.id: loc for loc in store.locations()}
        found = registry.find_assets(
            store, query=query, building=building, category=category
        )
        return [
            {
                "tag": a.tag,
                "name": a.name,
                "category": a.category,
                "criticality": str(a.criticality),
                "location": (
                    locations[a.location_id].label if a.location_id in locations else None
                ),
            }
            for a in found[:limit]
        ]


def replacement_candidates(limit: int = 20) -> list[dict[str, Any]]:
    """Assets whose age and repair history warrant a repair-vs-replace review."""
    with _store() as store:
        return [d.to_dict() for d in registry.replacement_candidates(store)[:limit]]


def plan_route(
    start_building: str,
    building: str | None = None,
    max_stops: int = 8,
) -> dict[str, Any]:
    """Sequence today's open work orders into a walking route."""
    with _store() as store:
        locations = {loc.id: loc for loc in store.locations()}
        candidates = [
            wo
            for wo in store.work_orders(open_only=True)
            if not building
            or (
                wo.location_id in locations
                and locations[wo.location_id].building == building
            )
        ]
        # Work the oldest and most urgent first when there are more open work
        # orders than a day can hold.
        candidates.sort(key=lambda wo: (not wo.breached_sla(), -wo.age_days()))
        route = routing.Router(store).plan(
            candidates[: max_stops * 3], start_building=start_building, max_stops=max_stops
        )
        return route.to_dict()


def parts_to_stage(work_order_number: str) -> list[dict[str, Any]]:
    """Parts worth staging before heading out to a work order."""
    with _store() as store:
        work_orders = {wo.number: wo for wo in store.work_orders()}
        wo = work_orders.get(work_order_number)
        if wo is None:
            return [{"error": f"No work order numbered {work_order_number!r}"}]
        return routing.suggest_parts(store, wo)


def inventory_status(only_reorder: bool = False) -> dict[str, Any]:
    """Stock levels, burn rate, days of cover, and reorder recommendations."""
    with _store() as store:
        report = inventory.build_report(store)
        data = report.to_dict()
        if only_reorder:
            data["parts"] = [p.to_dict() for p in report.reorder_now]
        return data


def job_briefing(work_order_number: str) -> dict[str, Any]:
    """Pre-job briefing: prior faults, likely cause, parts, manual, cautions."""
    with _store() as store:
        try:
            return suggest.brief(store, work_order_number).to_dict()
        except KeyError as exc:
            return {"error": str(exc)}


def draft_work_order_update(
    work_order_number: str, note: str, use_model: bool = False
) -> dict[str, Any]:
    """Turn a rough field note into a structured draft. Never submits it."""
    with _store() as store:
        try:
            draft = notes.draft_update(
                store, work_order_number, note, use_model=use_model
            )
        except KeyError as exc:
            return {"error": str(exc)}
        data = draft.to_dict()
        data["rendered"] = draft.render()
        return data


TOOLS = {
    "backlog_summary": backlog_summary,
    "open_work_orders": open_work_orders,
    "team_kpis": team_kpis,
    "asset_history": asset_history,
    "search_assets": search_assets,
    "replacement_candidates": replacement_candidates,
    "plan_route": plan_route,
    "parts_to_stage": parts_to_stage,
    "inventory_status": inventory_status,
    "job_briefing": job_briefing,
    "draft_work_order_update": draft_work_order_update,
}


def build_server() -> Any:
    """Construct the FastMCP server, registering every tool in `TOOLS`."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("bnsf-fm")
    for fn in TOOLS.values():
        server.tool()(fn)
    return server


def main() -> None:
    try:
        build_server().run()
    except ImportError:
        # Without the MCP runtime the module is still useful as a CLI probe,
        # which keeps the tools testable in environments that lack the package.
        print(
            json.dumps(
                {
                    "error": "the 'mcp' package is not installed",
                    "install": "uv pip install -e '.[mcp]'",
                    "tools": sorted(TOOLS),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
