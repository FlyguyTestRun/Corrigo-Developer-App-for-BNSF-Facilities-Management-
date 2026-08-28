"""Tier 2 ingestion: the Corrigo Enterprise REST API.

Written and tested ahead of having credentials, so that the day a Corrigo
system administrator issues a client id and secret this is a config change
rather than a development project.

What the API requires, and why this client is shaped the way it is
------------------------------------------------------------------
* **OAuth 2.0 client_credentials.** POST to the OAuth host for a bearer token.
  The token expires in roughly 20 minutes, so a long extract *must* refresh
  mid-run. `_ensure_token` refreshes on a safety margin rather than waiting for
  a 401.

* **Never hardcode the service host.** Corrigo's own guidance is explicit about
  this: hosts vary by region and version. The real host is discovered by
  calling `GetCompanyWsdkUrlCommand` against the API locator with a
  `CompanyName` header. Because implementations commonly resolve host and token
  together, this client rediscovers the host whenever it refreshes the token.

* **No query returns more than 4000 entities.** `_paged` walks offsets in
  chunks below that ceiling and stops on a short page.

* **`WorkOrder` supports time partitioning; `Location` does not.** Work orders
  are pulled in 24-hour windows for incremental loads. Locations carry no
  modification timestamp, so they can only be pulled in full — which is why
  they are cached rather than re-pulled per run.

Credentials come from the environment, never from a file in the repo:

    CORRIGO_CLIENT_ID, CORRIGO_CLIENT_SECRET, CORRIGO_COMPANY_NAME

This client only reads. Writing back into a production CMMS is deliberately not
implemented here — see `bnsf_fm.ai.notes`, which produces drafts for a human to
submit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from bnsf_fm.ingest.base import Batch
from bnsf_fm.ingest.vocab import (
    PRIORITY_ALIASES,
    STATUS_ALIASES,
    TYPE_ALIASES,
    alias,
    as_text,
    parse_datetime,
)
from bnsf_fm.models import (
    Asset,
    AssetCriticality,
    LaborEntry,
    Location,
    Priority,
    Technician,
    WorkOrder,
    WorkOrderStatus,
    WorkOrderType,
)

DEFAULT_OAUTH_URL = "https://oauth-pro-v2.corrigo.com/OAuth/token"
DEFAULT_LOCATOR_HOST = "https://am-apilocator.corrigo.com"

# The documented ceiling is 4000 entities per query. Staying under it leaves
# room for the service to append related records to a response.
MAX_PAGE_SIZE = 2000

# Refresh this far before the token's stated expiry. A long extract must not
# discover expiry via a mid-page 401.
TOKEN_REFRESH_MARGIN = timedelta(minutes=3)


class CorrigoAuthError(RuntimeError):
    """Authentication or host discovery failed."""


@dataclass
class CorrigoCredentials:
    client_id: str
    client_secret: str
    company_name: str
    oauth_url: str = DEFAULT_OAUTH_URL
    locator_host: str = DEFAULT_LOCATOR_HOST

    @classmethod
    def from_env(cls) -> CorrigoCredentials:
        missing = [
            name
            for name in ("CORRIGO_CLIENT_ID", "CORRIGO_CLIENT_SECRET", "CORRIGO_COMPANY_NAME")
            if not os.environ.get(name)
        ]
        if missing:
            raise CorrigoAuthError(
                f"Missing environment variable(s): {', '.join(missing)}. "
                "Corrigo API credentials are issued by a Corrigo system administrator "
                "against a user holding the WSDK role (or any role with Web Services "
                "Access). See docs/credential-request.md."
            )
        return cls(
            client_id=os.environ["CORRIGO_CLIENT_ID"],
            client_secret=os.environ["CORRIGO_CLIENT_SECRET"],
            company_name=os.environ["CORRIGO_COMPANY_NAME"],
            oauth_url=os.environ.get("CORRIGO_OAUTH_URL", DEFAULT_OAUTH_URL),
            locator_host=os.environ.get("CORRIGO_LOCATOR_HOST", DEFAULT_LOCATOR_HOST),
        )


class CorrigoClient:
    """Authenticated transport: token lifecycle, host discovery, paging."""

    def __init__(
        self,
        credentials: CorrigoCredentials,
        *,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.credentials = credentials
        self._client = client or httpx.Client(timeout=timeout)
        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        self._service_host: str | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CorrigoClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- auth --------------------------------------------------------------

    def _fetch_token(self) -> None:
        response = self._client.post(
            self.credentials.oauth_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            raise CorrigoAuthError(
                f"OAuth token request failed ({response.status_code}): {response.text[:400]}"
            )
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise CorrigoAuthError(f"OAuth response contained no access_token: {payload}")
        self._token = token
        # Corrigo issues ~20-minute tokens. Trust the response when it states a
        # lifetime, but never assume one longer than documented.
        expires_in = int(payload.get("expires_in", 1200))
        self._token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    def _discover_host(self) -> None:
        """Resolve the real service host for this company.

        Hosts vary by region and version, so this is mandatory rather than an
        optimization — a hardcoded host will silently break on a Corrigo
        infrastructure change.
        """
        url = f"{self.credentials.locator_host.rstrip('/')}/api/v1/cmd/GetCompanyWsdkUrlCommand"
        response = self._client.post(
            url,
            json={},
            headers={
                "Authorization": f"Bearer {self._token}",
                "CompanyName": self.credentials.company_name,
                "Content-Type": "application/json",
            },
        )
        if response.status_code != 200:
            raise CorrigoAuthError(
                f"Host discovery failed ({response.status_code}): {response.text[:400]}"
            )
        payload = response.json()
        host = (
            payload.get("WsdkUrl")
            or payload.get("Url")
            or payload.get("wsdkUrl")
            or payload.get("url")
        )
        if not host:
            raise CorrigoAuthError(
                f"GetCompanyWsdkUrlCommand returned no URL. Response: {payload}"
            )
        self._service_host = str(host).rstrip("/")

    def _ensure_token(self) -> None:
        """Refresh the token and host together when the token is near expiry."""
        expiring = (
            self._token_expires_at is None
            or datetime.now(UTC) >= self._token_expires_at - TOKEN_REFRESH_MARGIN
        )
        if self._token is None or expiring:
            self._fetch_token()
            self._discover_host()

    @property
    def service_host(self) -> str:
        self._ensure_token()
        assert self._service_host
        return self._service_host

    # -- transport ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "CompanyName": self.credentials.company_name,
            "Content-Type": "application/json",
        }

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_token()
        url = f"{self._service_host}/{path.lstrip('/')}"
        response = self._client.post(url, json=payload, headers=self._headers())
        if response.status_code == 401:
            # Token rejected earlier than its stated lifetime; refresh once.
            self._token = None
            self._ensure_token()
            response = self._client.post(url, json=payload, headers=self._headers())
        response.raise_for_status()
        return response.json()

    def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.post(f"api/v1/cmd/{name}", payload or {})

    def query(
        self,
        entity: str,
        *,
        criteria: list[dict[str, Any]] | None = None,
        properties: list[str] | None = None,
        offset: int = 0,
        count: int = MAX_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """One page of a QueryExpression against `entity`."""
        expression: dict[str, Any] = {
            "PropertySet": {"Properties": properties} if properties else {"Type": "All"},
            "Paging": {"Offset": offset, "Count": min(count, MAX_PAGE_SIZE)},
        }
        if criteria:
            expression["Criteria"] = criteria
        payload = self.post(f"api/v1/query/{entity}", expression)
        # Corrigo has used several envelope shapes across versions; accept the
        # ones observed rather than assuming a single key.
        for key in ("Entities", "entities", "Results", "results", "Data", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return payload if isinstance(payload, list) else []

    def query_all(
        self,
        entity: str,
        *,
        criteria: list[dict[str, Any]] | None = None,
        properties: list[str] | None = None,
        page_size: int = MAX_PAGE_SIZE,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        """Every entity matching `criteria`, paged under the 4000-row ceiling."""
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.query(
                entity,
                criteria=criteria,
                properties=properties,
                offset=offset,
                count=page_size,
            )
            out.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
            if max_records is not None and len(out) >= max_records:
                return out[:max_records]
        return out

    def query_window(
        self,
        entity: str,
        *,
        field: str,
        start: datetime,
        end: datetime,
        window: timedelta = timedelta(hours=24),
        properties: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Pull a date range in fixed windows.

        Corrigo's guidance for bulk work order extraction is 24-hour windows;
        splitting the range keeps each individual query well under the entity
        ceiling and makes a failed run resumable at window granularity.
        """
        out: list[dict[str, Any]] = []
        cursor = start
        while cursor < end:
            upper = min(cursor + window, end)
            out.extend(
                self.query_all(
                    entity,
                    criteria=[
                        {"PropertyName": field, "Operator": "GreaterOrEqual",
                         "Value": cursor.isoformat()},
                        {"PropertyName": field, "Operator": "Less",
                         "Value": upper.isoformat()},
                    ],
                    properties=properties,
                )
            )
            cursor = upper
        return out


# -- field extraction -------------------------------------------------------

def _first(record: dict[str, Any], *names: str) -> Any:
    """First present, non-empty value among `names`.

    Corrigo responses vary in casing and in whether a related entity arrives
    inline or as a bare id, so every read goes through this rather than
    assuming one spelling.
    """
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return None


def _ref_id(value: Any) -> str | None:
    """Extract an id from either a bare value or a nested reference object."""
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("Id", "id", "EntityId", "Number"):
            if value.get(key) not in (None, ""):
                return str(value[key])
        return None
    return str(value)


class CorrigoApiSource:
    """Reads Corrigo entities and normalizes them into a `Batch`.

    `since` bounds the work order pull. Pass the timestamp of the last
    successful load for an incremental run; omit it for a full backfill.
    """

    name = "corrigo-api"

    def __init__(
        self,
        client: CorrigoClient,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        include_reference_data: bool = True,
    ) -> None:
        self.client = client
        self.since = since
        self.until = until or datetime.now(UTC)
        self.include_reference_data = include_reference_data

    @classmethod
    def from_env(cls, **kwargs: Any) -> CorrigoApiSource:
        return cls(CorrigoClient(CorrigoCredentials.from_env()), **kwargs)

    def fetch(self) -> Batch:
        batch = Batch()

        if self.include_reference_data:
            # Location carries no modification timestamp, so there is no
            # incremental option — it is a full pull, every time, and is cached
            # downstream rather than re-fetched per run.
            for raw in self.client.query_all("Location"):
                loc = self._location(raw)
                if loc:
                    batch.locations.append(loc)

            for raw in self.client.query_all("Employee"):
                tech = self._technician(raw)
                if tech:
                    batch.technicians.append(tech)

            for raw in self.client.query_all("Asset"):
                asset = self._asset(raw)
                if asset:
                    batch.assets.append(asset)

        if self.since:
            raw_wos = self.client.query_window(
                "WorkOrder", field="CreatedDate", start=self.since, end=self.until
            )
        else:
            raw_wos = self.client.query_all("WorkOrder")

        for raw in raw_wos:
            wo = self._work_order(raw)
            if wo:
                batch.work_orders.append(wo)
            else:
                batch.warnings.append(
                    f"work order {_first(raw, 'Id', 'id') or '<no id>'}: "
                    "missing or unparseable open date — skipped"
                )
            batch.labor_entries.extend(self._labor(raw))

        return batch

    # -- mappers -----------------------------------------------------------

    @staticmethod
    def _location(raw: dict[str, Any]) -> Location | None:
        loc_id = _ref_id(_first(raw, "Id", "id", "LocationId"))
        if not loc_id:
            return None
        return Location(
            id=loc_id,
            building=str(
                _first(raw, "PropertyName", "Building", "Property", "Name") or "Unknown"
            ),
            floor=as_text(_first(raw, "Floor", "Level")),
            room=as_text(_first(raw, "Space", "Room", "Area")),
            description=as_text(_first(raw, "Description", "DisplayName")),
        )

    @staticmethod
    def _technician(raw: dict[str, Any]) -> Technician | None:
        tech_id = _ref_id(_first(raw, "Id", "id", "EmployeeId"))
        if not tech_id:
            return None
        active = _first(raw, "IsActive", "Active")
        return Technician(
            id=tech_id,
            name=as_text(_first(raw, "DisplayName", "FullName", "Name")),
            trade=as_text(_first(raw, "Trade", "Skill", "Craft")),
            active=True if active is None else bool(active),
        )

    @staticmethod
    def _asset(raw: dict[str, Any]) -> Asset | None:
        asset_id = _ref_id(_first(raw, "Id", "id", "AssetId"))
        if not asset_id:
            return None
        criticality = str(_first(raw, "Criticality", "Priority") or "").lower()
        return Asset(
            id=asset_id,
            tag=str(_first(raw, "Number", "Tag", "Barcode") or asset_id),
            name=str(_first(raw, "Name", "DisplayName", "Description") or asset_id),
            category=str(_first(raw, "TypeName", "Category", "AssetType") or "Uncategorized"),
            location_id=_ref_id(_first(raw, "Location", "LocationId", "SpaceId")) or "",
            manufacturer=as_text(_first(raw, "Manufacturer", "Make")),
            model=as_text(_first(raw, "Model", "ModelNumber")),
            serial=as_text(_first(raw, "SerialNumber", "Serial")),
            installed_on=parse_datetime(
                as_text(_first(raw, "InstallDate", "InServiceDate"))
            ),
            criticality=(
                AssetCriticality.CRITICAL
                if criticality in ("critical", "high", "1")
                else AssetCriticality.IMPORTANT
                if criticality in ("important", "medium", "2")
                else AssetCriticality.STANDARD
            ),
        )

    @staticmethod
    def _work_order(raw: dict[str, Any]) -> WorkOrder | None:
        wo_id = _ref_id(_first(raw, "Id", "id", "WorkOrderId"))
        opened = parse_datetime(
            as_text(_first(raw, "CreatedDate", "DateCreated", "OpenedDate"))
        )
        if not wo_id or opened is None:
            return None
        status_raw = _first(raw, "StatusName", "Status", "WoStatus")
        return WorkOrder(
            id=wo_id,
            number=str(_first(raw, "Number", "WorkOrderNumber") or wo_id),
            title=str(_first(raw, "Summary", "Subject", "TaskName") or "(no summary)"),
            description=as_text(_first(raw, "Description", "Details")),
            status=alias(
                _ref_id(status_raw), STATUS_ALIASES, WorkOrderStatus.ASSIGNED
            ),
            type=alias(
                _ref_id(_first(raw, "TypeName", "WoType", "Type")),
                TYPE_ALIASES,
                WorkOrderType.REACTIVE,
            ),
            priority=alias(
                _ref_id(_first(raw, "PriorityName", "Priority")),
                PRIORITY_ALIASES,
                Priority.MEDIUM,
            ),
            asset_id=_ref_id(_first(raw, "Asset", "AssetId")),
            location_id=_ref_id(_first(raw, "Location", "LocationId")),
            assigned_to=_ref_id(_first(raw, "AssignedTo", "Actor", "EmployeeId")),
            opened_at=opened,
            closed_at=parse_datetime(
                as_text(_first(raw, "CompletedDate", "ClosedDate", "DateCompleted"))
            ),
            resolution=as_text(_first(raw, "Resolution", "CompletionNotes")),
        )

    @staticmethod
    def _labor(raw: dict[str, Any]) -> list[LaborEntry]:
        """Labor items arriving inline on a work order response.

        Corrigo commonly nests labor under the work order rather than exposing
        it as a separately queryable top-level entity, so it is harvested here.
        """
        wo_id = _ref_id(_first(raw, "Id", "id", "WorkOrderId"))
        items = _first(raw, "LaborItems", "WoLaborItems", "Labor") or []
        if not wo_id or not isinstance(items, list):
            return []
        out: list[LaborEntry] = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            tech_id = _ref_id(_first(item, "Employee", "EmployeeId", "TechnicianId"))
            hours = _first(item, "Hours", "Duration", "LaborHours")
            if not tech_id or hours is None:
                continue
            try:
                hours_value = float(hours)
            except (TypeError, ValueError):
                continue
            logged = parse_datetime(
                as_text(_first(item, "Date", "WorkDate", "CreatedDate"))
            )
            out.append(
                LaborEntry(
                    id=_ref_id(_first(item, "Id", "id")) or f"{wo_id}-labor-{i}",
                    work_order_id=wo_id,
                    technician_id=tech_id,
                    hours=max(hours_value, 0.0),
                    logged_at=logged or datetime.now(UTC),
                    note=as_text(_first(item, "Note", "Comment")),
                )
            )
        return out
