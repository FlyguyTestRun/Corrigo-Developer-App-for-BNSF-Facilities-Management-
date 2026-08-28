# Corrigo Facilities Intelligence — BNSF Fort Worth

A facilities intelligence layer over [Corrigo Enterprise](https://www.jll.com/en-us/products/corrigo)
(JLL Technologies' CMMS) for a rail-operations campus: work order accountability,
an asset registry with manuals and service history, route sequencing, and parts
inventory — plus an MCP server so the whole dataset can be queried
conversationally through Claude.

**This repository runs on synthetic data.** It ships a generated campus — 200
assets, 2,000 work orders, twelve technicians — shaped to match a real site.
No operational data is in this repository, and `data/` is gitignored top to
bottom.

```bash
uv venv && uv pip install -e ".[api,dev]"
bnsf-fm seed          # generate the synthetic campus
bnsf-fm backlog       # the aging board
bnsf-fm serve         # dashboard at http://127.0.0.1:8000
```

---

## The problem this solves

Every CMMS reports how many work orders are open. This one reconciles **logged
labor hours against days open**.

A work order open 20 days with 18 hours logged and one open 20 days with 0.5
hours logged look identical on a Corrigo status board. They are completely
different operational situations, and only one of them is an accountability
problem. Surfacing that split turns "who is sitting on work" from an impression
into a short, specific, workable list.

On the shipped synthetic campus that identifies **58 of 105 open work orders as
stalled, holding roughly 4,200 days of accumulated calendar time.**

## Architecture

The load-bearing decision: **`ingest/` is the only module that knows where data
came from.** Everything downstream reads the normalized store and cannot tell a
CSV export from a REST API pull.

```
Corrigo Enterprise
   │
   ├─ Tier 1  CSV / Excel export   works today, no credentials  ─┐
   ├─ Tier 2  REST API (OAuth)     when a sysadmin issues them  ─┤→ ingest/ → SQLite
   └─ Tier 3  synthetic fixtures   tests and this repo          ─┘             │
                                                                               │
              ┌────────────────────────────────────────────────────────────────┤
        FastAPI + dashboard                                              MCP server
                                                              (Claude queries the data)
```

That seam is why the site can start on manual exports today and switch to the
API later as a config change rather than a rewrite. `ingest/corrigo_api.py` is
already written and unit-tested against recorded HTTP fixtures — token refresh
at the 20-minute boundary, host rediscovery, and paging past the 4000-row
ceiling — so it works the day credentials arrive.

### Layout

| Path | What it holds |
|---|---|
| `models/core.py` | Domain vocabulary: work orders, assets, locations, labor, parts |
| `ingest/base.py` | The `IngestionSource` protocol — the seam |
| `ingest/csv_source.py` | Tier 1: Corrigo UI / BI exports, with configurable header mapping |
| `ingest/corrigo_api.py` | Tier 2: OAuth, host discovery, paged queries |
| `ingest/vocab.py` | Corrigo status/priority/type aliases, shared by both sources |
| `ingest/fixtures.py` | The synthetic campus |
| `store/` | SQLite schema and idempotent upserts |
| `analytics/aging.py` | Aging buckets, SLA breach, **stall detection** |
| `analytics/kpi.py` | Cycle time, first-time-fix, backlog growth, per-technician |
| `analytics/routing.py` | Campus graph, route sequencing, parts staging |
| `analytics/inventory.py` | Burn rate, days of cover, reorder points |
| `analytics/registry.py` | Asset dossiers, recurring faults, repair-vs-replace |
| `ai/notes.py` | Field notes → structured work order draft |
| `ai/suggest.py` | History-grounded pre-job briefings |
| `mcp/server.py` | Eleven tools exposing all of the above to Claude |
| `api/` | FastAPI service and the dashboard |

## Commands

```bash
bnsf-fm seed                              # synthetic campus
bnsf-fm load-csv data/raw                 # Tier 1: Corrigo exports
bnsf-fm load-csv data/raw --mapping site-headers.json
bnsf-fm load-api --since 2026-01-01       # Tier 2: REST API

bnsf-fm backlog --limit 20                # aging + stalled list
bnsf-fm kpis --window 90                  # team and technician KPIs
bnsf-fm route --from "Mechanical Shop"    # sequenced day, with parts to stage
bnsf-fm inventory --reorder               # what needs ordering
bnsf-fm asset AHU-0042                    # full dossier
bnsf-fm brief 100412                      # pre-job briefing
bnsf-fm draft 100412 "swapped contactor, 1.5hr, running"
bnsf-fm serve                             # dashboard
```

Every report command accepts `--json`.

## Loading real Corrigo exports

No credentials or approvals needed — export the reports you can already see in
Corrigo, drop the CSVs in `data/raw/`, and run `bnsf-fm load-csv`. Files are
matched by name (`workorders*.csv`, `assets*.csv`, `locations*.csv`,
`labor*.csv`, `parts*.csv`).

Corrigo's export headers vary by report, tenant, and version, so column names
are **configuration, not code**. The defaults in
`ingest/csv_source.py::DEFAULT_MAPPINGS` cover the common spellings. If a run
reports `Could not map required column(s)`, it names the headers actually
present in your file — add your site's spelling to a JSON mapping:

```json
{ "work_orders": { "id": ["BNSF Ticket Ref"], "opened_at": ["Raised On"] } }
```

Loads are idempotent: re-exporting an overlapping window converges rather than
duplicating, which matters because "export the last 90 days every Monday" is
the normal workflow.

## Connecting to the Corrigo REST API

Set three environment variables (see `.env.example`) and run `bnsf-fm load-api`:

```
CORRIGO_CLIENT_ID, CORRIGO_CLIENT_SECRET, CORRIGO_COMPANY_NAME
```

Credentials are issued by a Corrigo **System Administrator** against a user
holding the WSDK role (or any role with Web Services Access) — not something a
technician or site manager can self-provision.
[`docs/credential-request.md`](docs/credential-request.md) is a one-pager to
hand them. [`docs/corrigo-api-notes.md`](docs/corrigo-api-notes.md) records
what we verified about the API and what is still open.

## MCP server

```bash
claude mcp add bnsf-fm -- /path/to/.venv/bin/python -m bnsf_fm.mcp.server
```

Eleven read-only tools — backlog, work orders, KPIs, asset history, search,
replacement candidates, routing, parts staging, inventory, briefings, and note
drafting. Ask questions nobody built a screen for: *"what's open in the Central
Utility Plant over fourteen days"*, *"which chillers are past expected life and
repair-heavy"*, *"what should I stage for WO 100412"*.

## Design commitments

These are enforced in code and covered by tests, not just stated:

- **Read-only against Corrigo.** No write command is implemented. The
  note-drafting feature produces a draft a human reviews and submits; every
  draft carries `requires_human_submission: true`.
- **Pseudonymized by default.** Technicians appear as `TECH-nn` in every report
  and API response unless the caller explicitly passes a reveal flag. The
  default output — the one that gets screenshotted — carries no real names.
- **Local only.** One SQLite file. No cloud service, no telemetry.
- **Degrades without a model.** Note extraction works with no API key and no
  network; Claude improves the prose when configured, and never supplies the
  labor hours or parts, which must stay reproducible and auditable.
- **No silent data corruption.** A work order with no parseable open date is
  dropped with a warning rather than given a default, because a fabricated date
  would skew every aging metric downstream.

## Tests

```bash
pytest              # 155 tests
```

Coverage concentrates on what would be expensive to get wrong: aging and stall
arithmetic against hand-computed fixtures, header mapping and date parsing
across eight Corrigo export formats, ingestion idempotency, and the API client's
token/paging behavior — verified against a mock transport, since we have no
tenant to test against.

## Documentation

- [`docs/business-case.md`](docs/business-case.md) — the argument, for a manager
- [`docs/corrigo-api-notes.md`](docs/corrigo-api-notes.md) — verified API facts and open questions
- [`docs/credential-request.md`](docs/credential-request.md) — the one-pager for a Corrigo sysadmin
