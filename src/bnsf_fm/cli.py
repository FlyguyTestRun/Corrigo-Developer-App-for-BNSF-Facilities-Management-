"""Command line interface.

    bnsf-fm seed                      load synthetic campus data
    bnsf-fm load-csv data/raw         load Corrigo exports (Tier 1)
    bnsf-fm load-api --since 2026-01-01   load via the REST API (Tier 2)
    bnsf-fm backlog                   aging board and stalled work orders
    bnsf-fm kpis --window 90          team and technician KPIs
    bnsf-fm route --from "Mechanical Shop"
    bnsf-fm inventory --reorder
    bnsf-fm asset AHU-0042
    bnsf-fm brief 100412
    bnsf-fm draft 100412 "swapped contactor, 1.5hr, running"
    bnsf-fm scorecard --window 365 --html scorecard.html
    bnsf-fm quality

`--json` on any report command emits machine-readable output.

Co-workers appear as "Tech N" everywhere. That is not a display setting — their
names are discarded when data is loaded and are never written to the database.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bnsf_fm.ai import notes, suggest
from bnsf_fm.analytics import aging, inventory, kpi, quality, registry, routing, scorecard
from bnsf_fm.ingest import CAMPUS_EDGES, CsvSource, FixtureSource, Roster, load
from bnsf_fm.report import html as html_report
from bnsf_fm.store import DEFAULT_DB_PATH, Store


def _bar(count: int, scale: int, width: int = 28) -> str:
    if scale <= 0:
        return ""
    return "#" * max(1, round(count / scale * width)) if count else ""


def cmd_seed(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        written = load(
            FixtureSource(
                seed=args.seed,
                asset_count=args.assets,
                work_order_count=args.work_orders,
            ),
            store,
        )
        store.set_campus_edges(CAMPUS_EDGES)
    print(f"Seeded {args.db}:")
    for entity, count in written.items():
        print(f"  {entity:<16} {count:>6}")
    return 0


def cmd_load_csv(args: argparse.Namespace) -> int:
    # The roster anonymizes as rows are read. Passing no --me still strips every
    # name; --me only decides whose name is kept.
    roster = Roster(me=args.me)
    source = (
        CsvSource.with_mapping_file(args.directory, args.mapping, roster=roster)
        if args.mapping
        else CsvSource(args.directory, roster=roster)
    )
    batch = source.fetch()
    with Store(args.db) as store:
        written = {
            "locations": store.upsert_locations(batch.locations),
            "technicians": store.upsert_technicians(batch.technicians),
            "assets": store.upsert_assets(batch.assets),
            "parts": store.upsert_parts(batch.parts),
        }
        stubbed = store.ensure_referenced(batch.work_orders)
        written["work_orders"] = store.upsert_work_orders(batch.work_orders)
        written["labor_entries"] = store.upsert_labor(batch.labor_entries)
    print(f"Loaded exports from {args.directory}:")
    for entity, count in written.items():
        print(f"  {entity:<16} {count:>6}")

    if any(stubbed.values()):
        print(
            f"\n{stubbed['assets']} assets and {stubbed['locations']} locations were "
            "referenced by work orders but not described in any export. They are "
            "recorded as placeholders; export the asset and location lists to fill "
            "them in."
        )

    # The first run against a real export is always a mapping problem, so say
    # out loud which columns were seen and ignored rather than dropping them
    # silently.
    if source.unmapped_headers:
        print("\nColumns present but not mapped (add spellings to a --mapping file):")
        for filename, headers in source.unmapped_headers.items():
            print(f"  {filename}: {', '.join(headers)}")

    if args.me:
        print(
            f"\nIdentified you as {args.me!r}."
            if roster.matched_self
            else f"\n! {args.me!r} matched no row — check the spelling in the export."
        )
    print(
        f"{roster.distinct_count()} distinct technicians seen; every name but yours "
        "was discarded at load."
    )
    if batch.warnings:
        print(f"\n{len(batch.warnings)} warning(s):")
        for warning in batch.warnings[:20]:
            print(f"  ! {warning}")
        if len(batch.warnings) > 20:
            print(f"  ... and {len(batch.warnings) - 20} more")
    return 0


def cmd_load_api(args: argparse.Namespace) -> int:
    from bnsf_fm.ingest import CorrigoApiSource, CorrigoAuthError

    since = (
        datetime.fromisoformat(args.since).replace(tzinfo=UTC) if args.since else None
    )
    try:
        source = CorrigoApiSource.from_env(since=since)
    except CorrigoAuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    with Store(args.db) as store:
        written = load(source, store)
    print("Loaded from the Corrigo REST API:")
    for entity, count in written.items():
        print(f"  {entity:<16} {count:>6}")
    return 0


def cmd_backlog(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        report = aging.build_report(store)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    print(f"\nOPEN WORK ORDERS: {report.total_open}")
    print(f"  median age {report.median_days_open:.1f} d   "
          f"oldest {report.oldest_days_open:.0f} d   "
          f"past SLA {report.sla_breached}")
    print("\n  Aging distribution")
    scale = max(report.buckets.values(), default=1)
    for label, count in report.buckets.items():
        print(f"    {label:<12} {count:>4}  {_bar(count, scale)}")

    print(f"\n  STALLED: {len(report.stalled)} open work orders with almost no labor logged")
    print(f"  ({report.stalled_hours_at_risk:.0f} days of calendar time sitting in them)\n")
    print(f"    {'WO':<9}{'DAYS':>6}{'HRS':>7}  {'SEV':<9}{'PRIORITY':<11}TITLE")
    for item in report.stalled[: args.limit]:
        print(
            f"    {item.work_order.number:<9}{item.days_open:>6.0f}{item.hours_logged:>7.1f}  "
            f"{item.severity:<9}{str(item.work_order.priority):<11}{item.work_order.title[:38]}"
        )
    if len(report.stalled) > args.limit:
        print(f"    ... and {len(report.stalled) - args.limit} more")
    return 0


def cmd_kpis(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        report = kpi.build_report(
            store, window_days=args.window
        )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    print(f"\nTEAM — trailing {report.window_days} days")
    print(f"  opened {report.opened}   completed {report.completed}   "
          f"backlog {report.backlog_open} ({report.backlog_growth:+d} net)")
    print(f"  median cycle {report.median_cycle_days:.1f} d   "
          f"SLA met {report.sla_met_rate:.0%}   "
          f"first-time fix {report.first_time_fix_rate:.0%}   "
          f"preventive {report.preventive_share:.0%}")
    print(f"\n  {'TECH':<10}{'TRADE':<20}{'DONE':>6}{'OPEN':>6}{'CYCLE':>7}"
          f"{'HRS':>8}{'SLA':>7}{'STALL':>7}")
    for tech in report.technicians:
        print(
            f"  {tech.label:<10}{(tech.trade or ''):<20}{tech.completed:>6}"
            f"{tech.open_now:>6}{tech.median_cycle_days:>7.1f}{tech.hours_logged:>8.1f}"
            f"{tech.sla_met_rate:>7.0%}{tech.stalled_open:>7}"
        )
    print(
        "\n  Co-workers appear as Tech N. Their real names were discarded at "
        "load and are not in the database."
    )
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        locations = {loc.id: loc for loc in store.locations()}
        candidates = [
            wo
            for wo in store.work_orders(open_only=True)
            if not args.building
            or (
                wo.location_id in locations
                and locations[wo.location_id].building == args.building
            )
        ]
        candidates.sort(key=lambda wo: (not wo.breached_sla(), -wo.age_days()))
        route = routing.Router(store).plan(
            candidates[: args.stops * 3],
            start_building=args.start,
            max_stops=args.stops,
        )
        if args.json:
            print(json.dumps(route.to_dict(), indent=2))
            return 0
        print(f"\nROUTE from {route.start_building} — {len(route.stops)} stops, "
              f"{route.total_travel_minutes:.0f} min walking\n")
        for i, stop in enumerate(route.stops, 1):
            print(f"  {i}. WO {stop.work_order.number}  {stop.work_order.title[:44]}")
            print(f"     {stop.location.label if stop.location else '(unknown)'}")
            print(f"     +{stop.travel_minutes:.0f} min  —  {stop.reason}")
            parts = routing.suggest_parts(store, stop.work_order, limit=3)
            if parts:
                staged = ", ".join(
                    f"{p['sku']}{'' if p['in_stock'] else ' (OUT)'}" for p in parts
                )
                print(f"     stage: {staged}")
            print()
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        report = inventory.build_report(store)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0
    rows = report.reorder_now if args.reorder else report.parts
    print(f"\nINVENTORY — {len(report.parts)} parts, {len(report.reorder_now)} at reorder "
          f"(${report.reorder_cost:,.2f} to restock)\n")
    print(f"  {'SKU':<12}{'ON HAND':>8}{'RP':>5}{'SUGG':>6}{'BURN/D':>8}"
          f"{'COVER':>8}  {'FLAG':<26}NAME")
    for status in rows:
        cover = f"{status.days_of_cover:.0f}d" if status.days_of_cover is not None else "-"
        print(
            f"  {status.part.sku:<12}{status.part.on_hand:>8}{status.part.reorder_point:>5}"
            f"{status.suggested_reorder_point:>6}{status.daily_burn:>8.2f}{cover:>8}  "
            f"{status.flag:<26}{status.part.name[:28]}"
        )
    return 0


def cmd_asset(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        matches = registry.find_assets(store, query=args.tag)
        if not matches:
            print(f"No asset matching {args.tag!r}", file=sys.stderr)
            return 1
        d = registry.dossier(store, matches[0].id)
        if d is None:
            return 1
        if args.json:
            print(json.dumps(d.to_dict(), indent=2))
            return 0
        info = d.to_dict()
        print(f"\n{info['tag']} — {info['name']}")
        print(f"  {info['location']}")
        print(f"  {info['manufacturer']} {info['model']}   serial {info['serial']}")
        print(f"  age {info['age_years']} y of {info['expected_life_years']} y expected"
              f"   criticality {info['criticality']}")
        print(f"  {info['work_order_count']} work orders "
              f"({info['open_work_orders']} open, {info['reactive_count']} reactive)"
              f"   {info['total_labor_hours']} labor hours")
        print(f"  signal: {info['replacement_signal']}")
        if info["manual"]:
            print(f"  manual: {info['manual']['title']}")
        if d.recurring_faults:
            print("\n  Recurring faults")
            for title, count in d.recurring_faults:
                print(f"    {count}x  {title}")
        print("\n  Recent work")
        for wo in d.work_orders[:8]:
            print(f"    {wo.opened_at.date()}  {wo.number:<8}{str(wo.status):<12}{wo.title[:40]}")
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        try:
            briefing = suggest.brief(store, args.work_order)
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(briefing.to_dict(), indent=2) if args.json else "\n" + briefing.render())
    return 0


def cmd_draft(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        try:
            draft = notes.draft_update(
                store, args.work_order, args.note, use_model=args.model
            )
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(draft.to_dict(), indent=2) if args.json else "\n" + draft.render())
    return 0


def cmd_scorecard(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        try:
            card = scorecard.build(store, window_days=args.window)
        except scorecard.NoSelfIdentified as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        report = quality.build(store) if args.html else None

    if args.html:
        path = Path(args.html)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_report.render(card, quality=report), encoding="utf-8")
        print(f"Wrote {path}  ({path.stat().st_size // 1024} KB, self-contained)")
        if not args.json:
            return 0

    if args.json:
        print(json.dumps(card.to_dict(), indent=2))
        return 0

    print(f"\nSCORECARD — {card.me}, trailing {card.window_days} days\n")
    print(f"  You completed {card.my_completed} of {card.department_completed} "
          f"department work orders — {card.my_share:.0%}")
    print(f"  An even split across {card.department_size} technicians would be "
          f"{card.even_split_share:.0%}, so you are at {card.share_vs_even:.1f}x par")
    print(f"\n  {'METRIC':<30}{'YOU':>9}{'MEDIAN':>9}{'RANK':>10}  READ")
    for c in card.comparisons:
        unit = c.unit
        fmt = (lambda v: f"{v:.0%}") if unit == "%" else (
            (lambda v: f"{v:.1f}") if unit == "days" else (lambda v: f"{v:g}")
        )
        mark = "+" if c.favorable else "-"
        print(
            f"  {c.metric:<30}{fmt(c.mine):>9}{fmt(c.department_median):>9}"
            f"{f'{c.rank} of {c.of}':>10}  {mark} {c.percentile:.0%} percentile"
        )
    print("\n  How to read this:")
    for note in card.caveats:
        print(f"    - {note}")
    return 0


def cmd_quality(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        report = quality.build(store)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    start, end = report.date_range
    print(f"\nDATA QUALITY — {report.work_orders} work orders, "
          f"{report.technicians} technicians")
    if start and end:
        print(f"  covering {start.date()} to {end.date()} "
              f"({report.coverage_days} days)")
    print(f"\n  {'FIELD':<28}{'MISSING':>12}{'SHARE':>8}  IMPACT")
    for finding in sorted(report.findings, key=lambda f: f.share, reverse=True):
        print(
            f"  {finding.field:<28}{f'{finding.missing}/{finding.total}':>12}"
            f"{finding.share:>8.0%}  {finding.severity}"
        )
    blocking = report.blocking
    if blocking:
        print("\n  What the gaps cost:")
        for finding in blocking:
            print(f"    - {finding.field}: {finding.costs}")
    if report.names_leaked:
        print(f"\n  ! {len(report.names_leaked)} co-worker name(s) found in the "
              "database — the anonymizer did not do its job. This is a bug.")
    else:
        print("\n  Anonymization verified: no co-worker names stored.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("error: uvicorn not installed. Run: uv pip install -e '.[api]'", file=sys.stderr)
        return 2
    uvicorn.run("bnsf_fm.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="path to the SQLite store")
    parser.add_argument("--json", action="store_true", help="emit JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bnsf-fm",
        description="Facilities intelligence over Corrigo Enterprise data.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("seed", help="load synthetic campus data")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--seed", type=int, default=20260828)
    p.add_argument("--assets", type=int, default=200)
    p.add_argument("--work-orders", dest="work_orders", type=int, default=2000)
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("load-csv", help="load Corrigo CSV/BI exports (Tier 1)")
    p.add_argument("directory", nargs="?", default="data/raw", type=Path)
    p.add_argument("--mapping", type=Path, help="JSON file of site-specific header spellings")
    p.add_argument(
        "--me",
        help="your name or employee id exactly as it appears in the export's "
        "technician column; everyone else is anonymized either way",
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.set_defaults(func=cmd_load_csv)

    p = sub.add_parser("load-api", help="load via the Corrigo REST API (Tier 2)")
    p.add_argument("--since", help="ISO date; omit for a full backfill")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.set_defaults(func=cmd_load_api)

    p = sub.add_parser("backlog", help="aging board and stalled work orders")
    _add_common(p)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_backlog)

    p = sub.add_parser("kpis", help="team and technician KPIs")
    _add_common(p)
    p.add_argument("--window", type=int, default=90)
    p.set_defaults(func=cmd_kpis)

    p = sub.add_parser("route", help="sequence today's open work orders")
    _add_common(p)
    p.add_argument("--from", dest="start", default="Mechanical Shop")
    p.add_argument("--building", help="restrict to one building")
    p.add_argument("--stops", type=int, default=8)
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("inventory", help="stock levels and reorder recommendations")
    _add_common(p)
    p.add_argument("--reorder", action="store_true", help="only parts at reorder point")
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("asset", help="full dossier for one asset")
    _add_common(p)
    p.add_argument("tag")
    p.set_defaults(func=cmd_asset)

    p = sub.add_parser("brief", help="pre-job briefing for a work order")
    _add_common(p)
    p.add_argument("work_order")
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("draft", help="turn a field note into a work order draft")
    _add_common(p)
    p.add_argument("work_order")
    p.add_argument("note")
    p.add_argument("--model", action="store_true", help="use Claude for the prose")
    p.set_defaults(func=cmd_draft)

    p = sub.add_parser("scorecard", help="you against the department")
    _add_common(p)
    p.add_argument("--window", type=int, default=365)
    p.add_argument("--html", help="also write a shareable self-contained HTML page here")
    p.set_defaults(func=cmd_scorecard)

    p = sub.add_parser("quality", help="what the export was missing")
    _add_common(p)
    p.set_defaults(func=cmd_quality)

    p = sub.add_parser("serve", help="run the dashboard API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    func: Any = args.func
    return int(func(args))


if __name__ == "__main__":
    raise SystemExit(main())
