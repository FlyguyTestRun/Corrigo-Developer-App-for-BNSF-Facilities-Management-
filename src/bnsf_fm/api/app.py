"""FastAPI service backing the dashboard.

Read-only over the local store. `POST /draft` is the one non-GET route and it
still writes nothing — it returns a draft for a human to review and submit into
Corrigo themselves.

No endpoint can disclose a co-worker's name, because identities are anonymized
at ingest and the names are not in the database to serve. Labels are "Tech N";
only the person who loaded the data appears by name.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from bnsf_fm.ai import notes, suggest
from bnsf_fm.analytics import aging, inventory, kpi, registry, routing
from bnsf_fm.store import DEFAULT_DB_PATH, Store

DB_PATH = os.environ.get("BNSF_FM_DB", str(DEFAULT_DB_PATH))

app = FastAPI(
    title="BNSF Facilities Intelligence",
    description=(
        "Work order accountability, asset registry, routing and parts inventory "
        "over Corrigo Enterprise data."
    ),
    version="0.1.0",
)


def _store() -> Store:
    return Store(DB_PATH)


STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    """The dashboard: a single static page, no build step."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    with _store() as store:
        return {
            "status": "ok",
            "db": DB_PATH,
            "work_orders": store.count("work_orders"),
            "assets": store.count("assets"),
        }


@app.get("/backlog")
def get_backlog() -> dict[str, Any]:
    with _store() as store:
        return aging.build_report(store).to_dict()


@app.get("/work-orders")
def get_work_orders(
    building: str | None = None,
    priority: str | None = None,
    min_days_open: float = 0.0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    with _store() as store:
        locations = {loc.id: loc for loc in store.locations()}
        labor = store.labor_hours_by_work_order()
        rows = []
        for wo in store.work_orders(open_only=True):
            loc = locations.get(wo.location_id or "")
            if building and (not loc or loc.building != building):
                continue
            if priority and str(wo.priority) != priority.lower():
                continue
            if wo.age_days() < min_days_open:
                continue
            rows.append(
                {
                    "number": wo.number,
                    "title": wo.title,
                    "status": str(wo.status),
                    "priority": str(wo.priority),
                    "type": str(wo.type),
                    "days_open": round(wo.age_days(), 1),
                    "hours_logged": round(labor.get(wo.id, 0.0), 2),
                    "past_sla": wo.breached_sla(),
                    "location": loc.label if loc else None,
                }
            )
        rows.sort(key=lambda r: r["days_open"], reverse=True)
        return rows[:limit]


@app.get("/kpis")
def get_kpis(
    window_days: Annotated[int, Query(ge=7, le=730)] = 90,
) -> dict[str, Any]:
    with _store() as store:
        return kpi.build_report(store, window_days=window_days).to_dict()


@app.get("/assets")
def get_assets(
    q: str | None = None,
    building: str | None = None,
    category: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    with _store() as store:
        locations = {loc.id: loc for loc in store.locations()}
        found = registry.find_assets(store, query=q, building=building, category=category)
        return [
            {
                "tag": a.tag,
                "name": a.name,
                "category": a.category,
                "criticality": str(a.criticality),
                "manufacturer": a.manufacturer,
                "model": a.model,
                "location": (
                    locations[a.location_id].label if a.location_id in locations else None
                ),
            }
            for a in found[:limit]
        ]


@app.get("/assets/{tag}")
def get_asset(tag: str) -> dict[str, Any]:
    with _store() as store:
        matches = registry.find_assets(store, query=tag)
        if not matches:
            raise HTTPException(status_code=404, detail=f"No asset matching {tag!r}")
        dossier = registry.dossier(store, matches[0].id)
        if dossier is None:
            raise HTTPException(status_code=404, detail=f"Asset {tag!r} not in registry")
        data = dossier.to_dict()
        data["work_orders"] = [
            {
                "number": wo.number,
                "title": wo.title,
                "status": str(wo.status),
                "opened": wo.opened_at.isoformat(),
                "closed": wo.closed_at.isoformat() if wo.closed_at else None,
                "resolution": wo.resolution,
            }
            for wo in dossier.work_orders
        ]
        return data


@app.get("/assets-review")
def get_replacement_candidates(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    with _store() as store:
        return [d.to_dict() for d in registry.replacement_candidates(store)[:limit]]


@app.get("/route")
def get_route(
    start_building: str = "Mechanical Shop",
    building: str | None = None,
    max_stops: Annotated[int, Query(ge=1, le=40)] = 8,
) -> dict[str, Any]:
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
        candidates.sort(key=lambda wo: (not wo.breached_sla(), -wo.age_days()))
        route = routing.Router(store).plan(
            candidates[: max_stops * 3], start_building=start_building, max_stops=max_stops
        )
        data = route.to_dict()
        for stop, rendered in zip(route.stops, data["stops"], strict=True):  # type: ignore[arg-type]
            rendered["parts_to_stage"] = routing.suggest_parts(store, stop.work_order, limit=3)
        return data


@app.get("/inventory")
def get_inventory(only_reorder: bool = False) -> dict[str, Any]:
    with _store() as store:
        report = inventory.build_report(store)
        data = report.to_dict()
        if only_reorder:
            data["parts"] = [p.to_dict() for p in report.reorder_now]
        return data


@app.get("/briefing/{work_order_number}")
def get_briefing(work_order_number: str) -> dict[str, Any]:
    with _store() as store:
        try:
            return suggest.brief(store, work_order_number).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


class DraftRequest(BaseModel):
    work_order_number: str
    note: str
    use_model: bool = False


@app.post("/draft")
def post_draft(request: DraftRequest) -> dict[str, Any]:
    """Turn a field note into a reviewable draft. Writes nothing to Corrigo."""
    with _store() as store:
        try:
            draft = notes.draft_update(
                store,
                request.work_order_number,
                request.note,
                use_model=request.use_model,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        data = draft.to_dict()
        data["rendered"] = draft.render()
        return data


@app.get("/buildings")
def get_buildings() -> list[str]:
    with _store() as store:
        return sorted({loc.building for loc in store.locations()})
