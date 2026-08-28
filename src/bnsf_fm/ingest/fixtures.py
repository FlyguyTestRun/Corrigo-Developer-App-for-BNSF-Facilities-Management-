"""Synthetic campus data.

This is what the public repo runs on. The shape mirrors a real rail-operations
campus — a mix of office, shop, and yard buildings, HVAC-heavy asset mix, and
crucially a *realistic pathology* in the work order data: a long tail of work
orders that are open for weeks with almost no labor logged against them. That
pathology is the thing the accountability analytics exist to surface, so the
fixtures have to contain it or the dashboard demos as an empty board.

Deterministic: seeded RNG, so tests can assert on exact counts.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

from bnsf_fm.ingest.base import Batch
from bnsf_fm.models import (
    Asset,
    AssetCriticality,
    LaborEntry,
    Location,
    Manual,
    Part,
    PartUsage,
    Priority,
    Technician,
    WorkOrder,
    WorkOrderStatus,
    WorkOrderType,
)

BUILDINGS = [
    ("Network Operations Center", ["1", "2", "3"]),
    ("Headquarters West", ["1", "2", "3", "4"]),
    ("Headquarters East", ["1", "2", "3", "4"]),
    ("Mechanical Shop", ["1"]),
    ("Locomotive Facility", ["1", "2"]),
    ("Central Utility Plant", ["1"]),
    ("Training Center", ["1", "2"]),
    ("Yard Office", ["1"]),
]

# Walking minutes between buildings. Only adjacent pairs are listed; the router
# derives the rest. Values are plausible for a large campus with outdoor paths.
CAMPUS_EDGES: list[tuple[str, str, float]] = [
    ("Network Operations Center", "Headquarters West", 4.0),
    ("Headquarters West", "Headquarters East", 3.0),
    ("Headquarters East", "Training Center", 5.0),
    ("Headquarters West", "Central Utility Plant", 6.0),
    ("Central Utility Plant", "Mechanical Shop", 3.0),
    ("Mechanical Shop", "Locomotive Facility", 7.0),
    ("Locomotive Facility", "Yard Office", 5.0),
    ("Central Utility Plant", "Yard Office", 9.0),
    ("Training Center", "Mechanical Shop", 8.0),
]

ASSET_CATALOG = [
    ("Air Handling Unit", "AHU", "Trane", "Performance Climate Changer", AssetCriticality.CRITICAL),
    ("Rooftop Unit", "RTU", "Carrier", "WeatherMaker 48TC", AssetCriticality.IMPORTANT),
    ("Centrifugal Chiller", "CH", "York", "YK Centrifugal", AssetCriticality.CRITICAL),
    ("Cooling Tower", "CT", "BAC", "Series 3000", AssetCriticality.CRITICAL),
    ("Boiler", "BLR", "Weil-McLain", "SlimFit", AssetCriticality.CRITICAL),
    ("Exhaust Fan", "EF", "Greenheck", "CUBE", AssetCriticality.STANDARD),
    ("Pump", "P", "Bell & Gossett", "Series e-1510", AssetCriticality.IMPORTANT),
    ("Variable Frequency Drive", "VFD", "ABB", "ACH580", AssetCriticality.IMPORTANT),
    ("LED Light Fixture", "LT", "Lithonia", "BLT Series", AssetCriticality.STANDARD),
    ("Overhead Door", "OHD", "Overhead Door", "Model 610", AssetCriticality.STANDARD),
    ("Air Compressor", "AC", "Ingersoll Rand", "R-Series", AssetCriticality.IMPORTANT),
    ("Emergency Generator", "GEN", "Caterpillar", "D400 GC", AssetCriticality.CRITICAL),
    ("Fire Pump", "FP", "Patterson", "PACP", AssetCriticality.CRITICAL),
    ("Split System", "SS", "Mitsubishi", "P-Series", AssetCriticality.STANDARD),
]

TRADES = ["HVAC", "Electrical", "Plumbing", "General Maintenance", "Controls"]

# Faults are keyed to asset category. Random fault-to-asset pairing produced
# nonsense like "generator failed weekly test" on a boiler, which makes the
# history and briefing views read as fake — and those views are the whole
# demonstration. Each entry is (fault, resolution, likely part SKU or None).
FAULTS_BY_CATEGORY: dict[str, list[tuple[str, str, str | None]]] = {
    "Air Handling Unit": [
        ("No cooling on the north zone", "Replaced failed contactor and verified amp draw.", "CNT-40A"),
        ("Belt slipping", "Replaced belt set and re-tensioned.", "BLT-B60"),
        ("Filter change due", "Replaced full filter bank.", "FLT-2425"),
        ("Excessive vibration and noise", "Replaced worn bearing, rebalanced fan wheel.", "BRG-6205"),
        ("Temperature complaint from occupant", "Rebalanced supply, adjusted setpoint.", None),
    ],
    "Rooftop Unit": [
        ("No cooling on the north zone", "Replaced run capacitor, verified charge.", "CAP-45-5"),
        ("Filter change due", "Replaced full filter bank.", "FLT-2020"),
        ("Unit short cycling", "Adjusted control differential, cleaned coil.", None),
        ("Water leak at base of unit", "Cleared condensate drain, replaced trap.", "TRP-P75"),
    ],
    "Centrifugal Chiller": [
        ("Low suction pressure alarm", "Located and repaired refrigerant leak at flare.", None),
        ("Unit short cycling", "Recalibrated chilled water setpoint and flow switch.", None),
        ("Excessive vibration and noise", "Replaced worn bearing, verified alignment.", "BRG-6205"),
    ],
    "Cooling Tower": [
        ("Belt slipping", "Replaced belt set and re-tensioned.", "BLT-A48"),
        ("Excessive vibration and noise", "Rebalanced fan, greased bearings.", "GRS-EP2"),
        ("Water leak at base of unit", "Repaired basin seam, refilled to level.", None),
    ],
    "Boiler": [
        ("Unit short cycling", "Cleaned flame sensor, adjusted control differential.", None),
        ("Water leak at base of unit", "Replaced relief valve and repiped discharge.", None),
        ("Will not fire on call for heat", "Replaced ignition module, verified flame rectification.", None),
    ],
    "Exhaust Fan": [
        ("Belt slipping", "Replaced belt set and re-tensioned.", "BLT-A48"),
        ("Excessive vibration and noise", "Replaced worn bearing, rebalanced fan wheel.", "BRG-6205"),
        ("Fan not running", "Replaced failed contactor.", "CNT-40A"),
    ],
    "Pump": [
        ("Excessive vibration and noise", "Replaced worn bearing, realigned coupling.", "BRG-6205"),
        ("Seal leaking at shaft", "Replaced mechanical seal, verified flush line.", None),
        ("Pump not developing pressure", "Cleared suction strainer, re-primed.", None),
    ],
    "Variable Frequency Drive": [
        ("Fault code on drive display", "Reset drive, tightened loose line-side lugs.", None),
        ("Drive tripping on overcurrent", "Corrected motor parameters, verified insulation.", None),
    ],
    "LED Light Fixture": [
        ("Lights out in corridor", "Replaced two LED drivers.", "LED-DRV"),
        ("Fixture flickering", "Replaced troffer and driver.", "LED-2X4"),
    ],
    "Overhead Door": [
        ("Door will not close fully", "Adjusted limit switch and lubricated track.", "GRS-EP2"),
        ("Door reverses on close", "Realigned photo eye, tested safety reverse.", None),
    ],
    "Air Compressor": [
        ("Compressor will not build pressure", "Replaced intake filter, tightened fittings.", "FLT-2020"),
        ("Excessive vibration and noise", "Replaced worn bearing, verified mounts.", "BRG-6205"),
        ("Moisture in air lines", "Serviced dryer, drained receiver.", None),
    ],
    "Emergency Generator": [
        ("Generator failed weekly test", "Replaced start battery, verified transfer.", "BAT-31A"),
        ("Generator will not crank", "Replaced start battery and cleaned terminals.", "BAT-31A"),
        ("Coolant level low at inspection", "Topped coolant, repaired hose clamp.", None),
    ],
    "Fire Pump": [
        ("Weekly churn test failed", "Replaced packing, verified relief operation.", None),
        ("Pump not developing pressure", "Cleared suction strainer, re-primed.", None),
    ],
    "Split System": [
        ("No cooling on the north zone", "Replaced run capacitor, verified charge.", "CAP-45-5"),
        ("Water leak at base of unit", "Cleared condensate drain, replaced trap.", "TRP-P75"),
        ("Filter change due", "Replaced filter.", "FLT-2020"),
    ],
}

PART_CATALOG = [
    ("FLT-2020", "Pleated Filter 20x20x2", "each", 3.85),
    ("FLT-2425", "Pleated Filter 24x24x4", "each", 9.40),
    ("BLT-A48", "V-Belt A48", "each", 11.20),
    ("BLT-B60", "V-Belt B60", "each", 16.75),
    ("LED-2X4", "LED Troffer 2x4 40W", "each", 58.00),
    ("LED-DRV", "LED Driver 40W", "each", 27.50),
    ("CNT-40A", "Contactor 40A 24V", "each", 34.10),
    ("BRG-6205", "Bearing 6205-2RS", "each", 14.60),
    ("CAP-45-5", "Dual Run Capacitor 45/5", "each", 19.30),
    ("TRP-P75", "Condensate Trap P-75", "each", 22.00),
    ("GRS-EP2", "Grease EP2 14oz", "tube", 7.85),
    ("BAT-31A", "Group 31 Start Battery", "each", 189.00),
]


class FixtureSource:
    """Generates a full synthetic campus. Deterministic for a given seed."""

    name = "fixtures"

    def __init__(
        self,
        *,
        seed: int = 20260828,
        asset_count: int = 200,
        work_order_count: int = 2000,
        history_days: int = 540,
        now: datetime | None = None,
    ) -> None:
        self.rng = random.Random(seed)
        self.asset_count = asset_count
        self.work_order_count = work_order_count
        self.history_days = history_days
        self.now = now or datetime.now(UTC)

    def fetch(self) -> Batch:
        locations = self._locations()
        technicians = self._technicians()
        manuals = self._manuals()
        assets = self._assets(locations)
        parts = self._parts(locations)
        work_orders, labor, usage = self._work(assets, technicians, parts)
        return Batch(
            locations=locations,
            technicians=technicians,
            manuals=manuals,
            assets=assets,
            parts=parts,
            work_orders=work_orders,
            labor_entries=labor,
            part_usage=usage,
        )

    # -- entity builders ---------------------------------------------------

    def _locations(self) -> list[Location]:
        out: list[Location] = []
        for building, floors in BUILDINGS:
            code = "".join(w[0] for w in building.split())[:3].upper()
            for floor in floors:
                for room in ("Mechanical Room", "Corridor", "Open Office", "Roof"):
                    if room == "Roof" and floor != floors[-1]:
                        continue
                    lid = f"LOC-{code}-{floor}-{room[:3].upper()}"
                    out.append(
                        Location(id=lid, building=building, floor=floor, room=room)
                    )
        return out

    def _technicians(self) -> list[Technician]:
        # Names are obviously synthetic. Real technician identities never enter
        # this repo; the private pilot supplies them locally.
        return [
            Technician(id=f"TECH-{i:03d}", name=f"Technician {i:03d}",
                       trade=TRADES[i % len(TRADES)])
            for i in range(1, 13)
        ]

    def _manuals(self) -> list[Manual]:
        out = []
        for i, (name, _tag, mfr, model, _crit) in enumerate(ASSET_CATALOG):
            out.append(
                Manual(
                    id=f"MAN-{i:03d}",
                    manufacturer=mfr,
                    model=model,
                    title=f"{mfr} {model} — Installation, Operation and Maintenance",
                    relative_path=f"manuals/{mfr.lower().replace(' ', '-')}-{i:03d}.pdf",
                    page_count=self.rng.randint(24, 180),
                    text=(
                        f"{name} ({mfr} {model}). Scheduled maintenance: inspect quarterly, "
                        "replace filters at 90-day intervals, verify amp draw against "
                        "nameplate, lubricate bearings semi-annually. Lockout/tagout "
                        "required before servicing."
                    ),
                )
            )
        return out

    def _assets(self, locations: list[Location]) -> list[Asset]:
        mech = [x for x in locations if x.room in ("Mechanical Room", "Roof")]
        out: list[Asset] = []
        for i in range(self.asset_count):
            name, tag, mfr, model, crit = self.rng.choice(ASSET_CATALOG)
            loc = self.rng.choice(mech)
            age_years = self.rng.randint(1, 22)
            out.append(
                Asset(
                    id=f"AST-{i:04d}",
                    tag=f"{tag}-{i:04d}",
                    name=f"{name} {i:04d}",
                    category=name,
                    location_id=loc.id,
                    manufacturer=mfr,
                    model=model,
                    serial=f"SN{self.rng.randint(10**7, 10**8 - 1)}",
                    installed_on=self.now - timedelta(days=age_years * 365),
                    criticality=crit,
                    expected_life_years=self.rng.choice([15, 20, 25, 30]),
                )
            )
        return out

    def _parts(self, locations: list[Location]) -> list[Part]:
        shop = next(x for x in locations if x.building == "Mechanical Shop")
        out = []
        for i, (sku, name, unit, cost) in enumerate(PART_CATALOG):
            reorder_point = self.rng.randint(4, 15)
            # Roughly a third of the catalogue sits at or below its reorder
            # point — this is what the inventory view is meant to catch.
            on_hand = (
                self.rng.randint(0, reorder_point)
                if i % 3 == 0
                else self.rng.randint(reorder_point + 1, reorder_point * 5)
            )
            out.append(
                Part(
                    id=f"PRT-{i:03d}",
                    sku=sku,
                    name=name,
                    unit=unit,
                    on_hand=on_hand,
                    reorder_point=reorder_point,
                    reorder_quantity=reorder_point * 3,
                    location_id=shop.id,
                    unit_cost=cost,
                )
            )
        return out

    def _work(
        self, assets: list[Asset], techs: list[Technician], parts: list[Part]
    ) -> tuple[list[WorkOrder], list[LaborEntry], list[PartUsage]]:
        work_orders: list[WorkOrder] = []
        labor: list[LaborEntry] = []
        usage: list[PartUsage] = []
        by_sku = {p.sku: p for p in parts}

        for i in range(self.work_order_count):
            asset = self.rng.choice(assets)
            tech = self.rng.choice(techs)
            fault, resolution, part_sku = self.rng.choice(
                FAULTS_BY_CATEGORY[asset.category]
            )
            opened = self.now - timedelta(
                days=self.rng.uniform(0, self.history_days),
                hours=self.rng.uniform(0, 24),
            )
            wo_type = self.rng.choices(
                [WorkOrderType.REACTIVE, WorkOrderType.PREVENTIVE, WorkOrderType.INSPECTION],
                weights=[62, 30, 8],
            )[0]
            priority = self.rng.choices(
                [Priority.EMERGENCY, Priority.HIGH, Priority.MEDIUM, Priority.LOW],
                weights=[4, 22, 54, 20],
            )[0]

            # Probability a work order is still open decays with age: recent
            # work is a genuine mix, older work has almost all closed, and what
            # survives is a thin long tail. A flat probability would pile up an
            # absurd backlog of year-old open work orders and make the aging
            # board look broken rather than instructive.
            days_ago = (self.now - opened).days
            p_open = 0.65 if days_ago < 21 else 0.35 * math.exp(-(days_ago - 21) / 45)
            still_open = self.rng.random() < p_open

            if still_open:
                status = self.rng.choices(
                    [
                        WorkOrderStatus.NEW,
                        WorkOrderStatus.ASSIGNED,
                        WorkOrderStatus.IN_PROGRESS,
                        WorkOrderStatus.ON_HOLD,
                    ],
                    weights=[18, 30, 34, 18],
                )[0]
                closed = None
                res = None
            else:
                status = (
                    WorkOrderStatus.CANCELLED
                    if self.rng.random() < 0.04
                    else WorkOrderStatus.COMPLETED
                )
                # Most work closes inside its SLA; a meaningful minority does not.
                hours_to_close = (
                    self.rng.uniform(1, priority.sla_hours)
                    if self.rng.random() < 0.72
                    else self.rng.uniform(priority.sla_hours, priority.sla_hours * 6)
                )
                closed = opened + timedelta(hours=hours_to_close)
                if closed > self.now:
                    closed = self.now
                res = resolution if status is WorkOrderStatus.COMPLETED else "Cancelled — duplicate."

            wo = WorkOrder(
                id=f"WO-{i:05d}",
                number=f"{100000 + i}",
                title=fault,
                description=f"{fault} reported on {asset.name} ({asset.tag}).",
                status=status,
                type=wo_type,
                priority=priority,
                asset_id=asset.id,
                location_id=asset.location_id,
                assigned_to=tech.id if status is not WorkOrderStatus.NEW else None,
                opened_at=opened,
                closed_at=closed,
                resolution=res,
            )
            work_orders.append(wo)

            # Labor. The deliberate pathology: about a third of long-open work
            # orders get little or no time logged against them. Those are the
            # "sitting on it" cases — open for weeks, nobody actually working.
            stalled = still_open and days_ago > 14 and self.rng.random() < 0.34
            if stalled:
                entry_count = self.rng.choice([0, 0, 1])
            elif status is WorkOrderStatus.CANCELLED:
                entry_count = self.rng.choice([0, 1])
            else:
                entry_count = self.rng.randint(1, 4)

            for j in range(entry_count):
                logged = opened + timedelta(hours=self.rng.uniform(1, 72))
                if logged > self.now:
                    logged = self.now
                labor.append(
                    LaborEntry(
                        id=f"LAB-{i:05d}-{j}",
                        work_order_id=wo.id,
                        technician_id=(wo.assigned_to or tech.id),
                        hours=round(self.rng.uniform(0.25, 4.0), 2),
                        logged_at=logged,
                        note=None,
                    )
                )

            # Consume the part the fault actually calls for, so parts history
            # per asset is coherent and the staging suggestions mean something.
            # Faults with no designated part usually consume nothing — a random
            # part on every job would bury the real signal under noise.
            designated = by_sku.get(part_sku) if part_sku else None
            consume = self.rng.random() < (0.75 if designated else 0.12)
            if status is WorkOrderStatus.COMPLETED and consume:
                part = designated or self.rng.choice(parts)
                usage.append(
                    PartUsage(
                        id=f"USE-{i:05d}",
                        work_order_id=wo.id,
                        part_id=part.id,
                        quantity=self.rng.randint(1, 4),
                        used_at=closed or self.now,
                    )
                )

        return work_orders, labor, usage
