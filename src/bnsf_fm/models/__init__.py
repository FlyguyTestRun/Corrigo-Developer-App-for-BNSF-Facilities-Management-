"""Normalized domain model.

Every ingestion source (CSV export, REST API, fixtures) produces these types,
and every downstream consumer reads only these types. This is the seam that
lets the pilot start on manual exports today and switch to the Corrigo REST
API later without touching analytics, the dashboard, or the MCP server.
"""

from bnsf_fm.models.core import (
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

__all__ = [
    "Asset",
    "AssetCriticality",
    "LaborEntry",
    "Location",
    "Manual",
    "Part",
    "PartUsage",
    "Priority",
    "Technician",
    "WorkOrder",
    "WorkOrderStatus",
    "WorkOrderType",
]
