"""Corrigo REST client, tested against recorded HTTP behavior.

We have no credentials and no tenant to test against, so these tests pin the
three behaviors most likely to be wrong on the first real run, and most
expensive to discover then:

1. The bearer token expires in ~20 minutes and a bulk extract runs longer than
   that. The client must refresh *before* expiry, not after a 401.
2. The service host must be discovered via `GetCompanyWsdkUrlCommand`, never
   hardcoded, and re-resolved whenever the token is refreshed.
3. No query returns more than 4000 entities, so paging must be correct —
   including the boundary case where the last page is exactly full.

A mock transport stands in for Corrigo. It is deliberately strict about
headers, because a missing `CompanyName` is the kind of thing that works in
one region and fails in another.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from bnsf_fm.ingest.corrigo_api import (
    MAX_PAGE_SIZE,
    CorrigoApiSource,
    CorrigoAuthError,
    CorrigoClient,
    CorrigoCredentials,
)
from bnsf_fm.models import Priority, WorkOrderStatus

CREDS = CorrigoCredentials(
    client_id="cid", client_secret="secret", company_name="BNSF-FTW"
)
SERVICE_HOST = "https://us-api.corrigo.com"


class FakeCorrigo:
    """A minimal stand-in for the Corrigo endpoints this client touches."""

    def __init__(self, *, entities: list[dict] | None = None, expires_in: int = 1200):
        self.entities = entities or []
        self.expires_in = expires_in
        self.token_requests = 0
        self.discovery_requests = 0
        self.query_requests: list[dict] = []
        self.reject_next_token = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        if url.endswith("/OAuth/token"):
            self.token_requests += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"token-{self.token_requests}",
                    "expires_in": self.expires_in,
                    "token_type": "Bearer",
                },
            )

        if "GetCompanyWsdkUrlCommand" in url:
            self.discovery_requests += 1
            assert request.headers.get("CompanyName") == "BNSF-FTW"
            assert request.headers.get("Authorization", "").startswith("Bearer ")
            return httpx.Response(200, json={"WsdkUrl": SERVICE_HOST})

        if "/api/v1/query/" in url:
            assert request.headers.get("CompanyName") == "BNSF-FTW"
            if self.reject_next_token:
                self.reject_next_token = False
                return httpx.Response(401, json={"error": "expired"})
            import json as _json

            body = _json.loads(request.content)
            paging = body.get("Paging", {})
            offset, count = paging.get("Offset", 0), paging.get("Count", MAX_PAGE_SIZE)
            self.query_requests.append({"offset": offset, "count": count, "body": body})
            return httpx.Response(200, json={"Entities": self.entities[offset : offset + count]})

        return httpx.Response(404, json={"error": f"unexpected {url}"})

    def client(self) -> CorrigoClient:
        transport = httpx.MockTransport(self.handler)
        return CorrigoClient(CREDS, client=httpx.Client(transport=transport))


class TestAuthAndDiscovery:
    def test_host_is_discovered_not_hardcoded(self):
        fake = FakeCorrigo()
        with fake.client() as client:
            assert client.service_host == SERVICE_HOST
        assert fake.discovery_requests == 1

    def test_token_is_reused_while_valid(self):
        fake = FakeCorrigo(entities=[{"Id": "1"}])
        with fake.client() as client:
            client.query("WorkOrder")
            client.query("WorkOrder")
            client.query("WorkOrder")
        assert fake.token_requests == 1
        assert fake.discovery_requests == 1

    def test_token_refreshes_before_the_20_minute_expiry(self):
        """A bulk extract outlives one token; refresh must be pre-emptive."""
        fake = FakeCorrigo(entities=[{"Id": "1"}], expires_in=1200)
        with fake.client() as client:
            client.query("WorkOrder")
            assert fake.token_requests == 1
            # Advance past the refresh margin without reaching hard expiry.
            client._token_expires_at = datetime.now(UTC) + timedelta(minutes=2)
            client.query("WorkOrder")
        assert fake.token_requests == 2, "client did not refresh ahead of expiry"

    def test_host_is_rediscovered_with_every_token_refresh(self):
        fake = FakeCorrigo(entities=[{"Id": "1"}])
        with fake.client() as client:
            client.query("WorkOrder")
            client._token_expires_at = datetime.now(UTC) + timedelta(minutes=2)
            client.query("WorkOrder")
        assert fake.discovery_requests == fake.token_requests == 2

    def test_unexpected_401_triggers_one_retry(self):
        fake = FakeCorrigo(entities=[{"Id": "1"}])
        with fake.client() as client:
            client.query("WorkOrder")
            fake.reject_next_token = True
            result = client.query("WorkOrder")
        assert result == [{"Id": "1"}]
        assert fake.token_requests == 2

    def test_missing_env_vars_explain_who_issues_credentials(self, monkeypatch):
        for name in ("CORRIGO_CLIENT_ID", "CORRIGO_CLIENT_SECRET", "CORRIGO_COMPANY_NAME"):
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(CorrigoAuthError) as exc:
            CorrigoCredentials.from_env()
        message = str(exc.value)
        assert "CORRIGO_CLIENT_ID" in message
        assert "system administrator" in message  # points at the real blocker

    def test_failed_token_request_raises_clearly(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="invalid_client")

        client = CorrigoClient(CREDS, client=httpx.Client(transport=httpx.MockTransport(handler)))
        with pytest.raises(CorrigoAuthError, match="OAuth token request failed"):
            client.query("WorkOrder")

    def test_discovery_without_a_url_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith("/OAuth/token"):
                return httpx.Response(200, json={"access_token": "t", "expires_in": 1200})
            return httpx.Response(200, json={"Unexpected": "shape"})

        client = CorrigoClient(CREDS, client=httpx.Client(transport=httpx.MockTransport(handler)))
        with pytest.raises(CorrigoAuthError, match="no URL"):
            client.query("WorkOrder")


class TestPaging:
    def test_page_size_stays_under_the_4000_entity_ceiling(self):
        fake = FakeCorrigo(entities=[{"Id": str(i)} for i in range(10)])
        with fake.client() as client:
            client.query("WorkOrder", count=99_999)
        assert fake.query_requests[0]["count"] <= 4000
        assert fake.query_requests[0]["count"] == MAX_PAGE_SIZE

    def test_query_all_pages_through_everything(self):
        total = MAX_PAGE_SIZE * 2 + 137
        fake = FakeCorrigo(entities=[{"Id": str(i)} for i in range(total)])
        with fake.client() as client:
            results = client.query_all("WorkOrder")
        assert len(results) == total
        assert [r["offset"] for r in fake.query_requests] == [
            0, MAX_PAGE_SIZE, MAX_PAGE_SIZE * 2
        ]

    def test_exactly_full_last_page_triggers_one_more_probe(self):
        """The boundary that silently truncates if `>=` is used instead of `<`."""
        fake = FakeCorrigo(entities=[{"Id": str(i)} for i in range(MAX_PAGE_SIZE)])
        with fake.client() as client:
            results = client.query_all("WorkOrder")
        assert len(results) == MAX_PAGE_SIZE
        assert len(fake.query_requests) == 2, "did not probe past a full page"

    def test_empty_result_set(self):
        fake = FakeCorrigo(entities=[])
        with fake.client() as client:
            assert client.query_all("WorkOrder") == []

    def test_max_records_caps_the_pull(self):
        fake = FakeCorrigo(entities=[{"Id": str(i)} for i in range(MAX_PAGE_SIZE * 3)])
        with fake.client() as client:
            results = client.query_all("WorkOrder", max_records=MAX_PAGE_SIZE + 5)
        assert len(results) == MAX_PAGE_SIZE + 5

    def test_time_windows_split_a_range_into_24_hour_slices(self):
        fake = FakeCorrigo(entities=[])
        start = datetime(2026, 8, 1, tzinfo=UTC)
        end = datetime(2026, 8, 6, tzinfo=UTC)
        with fake.client() as client:
            client.query_window("WorkOrder", field="CreatedDate", start=start, end=end)
        # Five days, one query per 24-hour window.
        assert len(fake.query_requests) == 5
        first = fake.query_requests[0]["body"]["Criteria"]
        assert first[0]["Value"] == start.isoformat()
        assert first[1]["Value"] == (start + timedelta(days=1)).isoformat()

    def test_partial_final_window_is_not_dropped(self):
        fake = FakeCorrigo(entities=[])
        start = datetime(2026, 8, 1, tzinfo=UTC)
        end = datetime(2026, 8, 3, 6, tzinfo=UTC)  # 2.25 days
        with fake.client() as client:
            client.query_window("WorkOrder", field="CreatedDate", start=start, end=end)
        assert len(fake.query_requests) == 3
        assert fake.query_requests[-1]["body"]["Criteria"][1]["Value"] == end.isoformat()

    @pytest.mark.parametrize("key", ["Entities", "entities", "Results", "Data"])
    def test_response_envelope_variants_are_accepted(self, key):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/OAuth/token"):
                return httpx.Response(200, json={"access_token": "t", "expires_in": 1200})
            if "GetCompanyWsdkUrlCommand" in url:
                return httpx.Response(200, json={"WsdkUrl": SERVICE_HOST})
            return httpx.Response(200, json={key: [{"Id": "1"}]})

        client = CorrigoClient(CREDS, client=httpx.Client(transport=httpx.MockTransport(handler)))
        assert client.query("WorkOrder") == [{"Id": "1"}]


class TestMapping:
    def test_work_order_mapping_handles_nested_references(self):
        raw = {
            "Id": "W1",
            "Number": "100500",
            "Summary": "No cooling",
            "StatusName": "In Progress",
            "PriorityName": "High",
            "TypeName": "Corrective",
            "Asset": {"Id": "A9"},
            "Location": {"Id": "L3"},
            "AssignedTo": {"Id": "E7"},
            "CreatedDate": "2026-03-15T08:00:00Z",
            "CompletedDate": None,
        }
        wo = CorrigoApiSource._work_order(raw)
        assert wo is not None
        assert (wo.id, wo.number) == ("W1", "100500")
        assert wo.status is WorkOrderStatus.IN_PROGRESS
        assert wo.priority is Priority.HIGH
        assert (wo.asset_id, wo.location_id, wo.assigned_to) == ("A9", "L3", "E7")
        assert wo.closed_at is None

    def test_work_order_without_open_date_is_rejected(self):
        assert CorrigoApiSource._work_order({"Id": "W1", "StatusName": "New"}) is None

    def test_bare_id_references_also_work(self):
        wo = CorrigoApiSource._work_order(
            {"Id": "W1", "Asset": "A9", "CreatedDate": "2026-03-15", "StatusName": "New"}
        )
        assert wo is not None and wo.asset_id == "A9"

    def test_inline_labor_items_are_harvested(self):
        entries = CorrigoApiSource._labor(
            {
                "Id": "W1",
                "LaborItems": [
                    {"Id": "L1", "Employee": {"Id": "E1"}, "Hours": 2.5,
                     "Date": "2026-03-15T10:00:00Z"},
                    {"Id": "L2", "Employee": "E2", "Hours": "1.25", "Date": "2026-03-16"},
                    {"Id": "L3", "Hours": 4},  # no employee — unusable, skipped
                ],
            }
        )
        assert [(e.technician_id, e.hours) for e in entries] == [("E1", 2.5), ("E2", 1.25)]
        assert all(e.work_order_id == "W1" for e in entries)

    def test_labor_without_items_is_empty_not_an_error(self):
        assert CorrigoApiSource._labor({"Id": "W1"}) == []

    def test_location_maps_property_name_to_building(self):
        loc = CorrigoApiSource._location(
            {"Id": "L1", "PropertyName": "Headquarters West", "Floor": 2}
        )
        assert loc is not None
        assert (loc.building, loc.floor) == ("Headquarters West", "2")

    def test_source_fetch_normalizes_end_to_end(self):
        fake = FakeCorrigo(
            entities=[
                {
                    "Id": "W1",
                    "Number": "100001",
                    "Summary": "Belt slipping",
                    "StatusName": "Completed",
                    "CreatedDate": "2026-03-15T08:00:00Z",
                    "CompletedDate": "2026-03-15T11:00:00Z",
                    "LaborItems": [
                        {"Id": "L1", "Employee": {"Id": "E1"}, "Hours": 3.0,
                         "Date": "2026-03-15T09:00:00Z"}
                    ],
                }
            ]
        )
        with fake.client() as client:
            batch = CorrigoApiSource(client, include_reference_data=False).fetch()
        assert len(batch.work_orders) == 1
        assert batch.work_orders[0].status is WorkOrderStatus.COMPLETED
        assert len(batch.labor_entries) == 1
        assert batch.labor_entries[0].hours == 3.0
