# Corrigo Enterprise API — verified notes

What we established about the Corrigo Enterprise REST API before writing
`src/bnsf_fm/ingest/corrigo_api.py`, and what is still open.

**Provenance caveat.** `developer.corrigo.com` was not directly reachable from
the environment this research ran in (the network egress proxy returned 403 at
the CONNECT tunnel). Everything below was assembled from search-result extracts
of the developer portal, the JLL Technologies support portal, and
integration-partner documentation. **Confirm each item against the portal
before relying on it in production.** The portal is publicly readable.

Useful entry point: `developer.corrigo.com/llms.txt` — an index of every page
as Markdown plus the endpoints as OpenAPI, published specifically for AI
agents. Feed it to Claude directly rather than reading pages by hand.

---

## Confirmed shape

| Concern | Finding |
|---|---|
| Auth | OAuth 2.0, `client_credentials` grant. `POST https://oauth-pro-v2.corrigo.com/OAuth/token` |
| Token | Bearer token in the `Authorization` header. **Expires in ~20 minutes** — a bulk extract must refresh mid-run |
| Host discovery | **Never hardcode the service host.** `POST /api/v1/cmd/GetCompanyWsdkUrlCommand` against `am-apilocator.corrigo.com`, with a `CompanyName` header, returns the real regional host. Corrigo's guidance notes implementations commonly refresh host and token together |
| Read | `POST /api/v1/query/{Entity}` with a QueryExpression body |
| Write | `POST /api/v1/cmd/{Command}` |
| Entities | `WorkOrder`, `Asset`, `Location`, `Employee`, `Customer`, `Category`, `Task`, `Priority`, `Property`, plus related labor records |
| Row ceiling | **No query returns more than 4000 entities.** Page with `QueryExpression.Count` plus an offset |
| Incremental | `WorkOrder` partitions on time — 24-hour windows are the documented pattern for bulk extraction. **`Location` has no timestamp field**, so it can only be pulled in full |
| Environments | A non-Live "Stage" or "Preview" company serves as a sandbox once a Corrigo system administrator configures it |
| Legacy | A separate SOAP API still exists at `developer-soap.corrigo.com` |

## How credentials are actually issued

This is the constraint that shapes the whole project, so it is worth stating
precisely:

1. A Corrigo user account is created for the integration. The standard role is
   **WSDK** (Web Services Development Kit); any role works provided it carries
   **Permissions → Web Services Access**.
2. That account is linked in global settings, which generates a **client id and
   client secret**.
3. Step 2 requires **System Administrator** role-level access.
4. **The secret is displayed once.** The dialog cannot be reopened for security
   reasons — capture it at issue time or start over.

A maintenance-mechanic login cannot perform any of this. A site facilities
manager most likely cannot either; they can *request* it. The provisioning
action belongs to whoever holds Corrigo sysadmin for the account. See
[`credential-request.md`](credential-request.md).

## Cost

No public developer-program fee surfaced. The paid tiers that are publicly
documented — $5 per work order, or monthly unlimited — are **CorrigoPro**
subscriptions, which is the contractor network, not Enterprise API access.
Enterprise API access on a client account is an entitlement question for the
JLL account team, not a credit-card purchase. Ask; do not assume a cost.

## How this client handles each constraint

`src/bnsf_fm/ingest/corrigo_api.py`:

- `_ensure_token` refreshes on a **3-minute safety margin** before stated
  expiry rather than reacting to a 401 mid-page. A 401 still triggers exactly
  one retry, for the case where the token is revoked early.
- `_discover_host` runs on **every** token refresh, so host and token stay in
  step. Tested in `tests/test_corrigo_api.py::test_host_is_rediscovered_with_every_token_refresh`.
- `MAX_PAGE_SIZE = 2000`, half the documented ceiling, leaving room for the
  service to attach related records. `query_all` stops on a short page and
  probes once past an exactly-full page — the boundary that silently truncates
  if you write `>=` where `<` belongs.
- `query_window` splits a date range into 24-hour slices, which keeps each
  query well under the ceiling and makes a failed run resumable per window.
- Response envelopes vary across versions (`Entities`, `entities`, `Results`,
  `Data`); all observed shapes are accepted.
- Related entities arrive sometimes inline (`{"Asset": {"Id": "A9"}}`) and
  sometimes as a bare id (`{"Asset": "A9"}`). `_ref_id` handles both.

## Open questions to confirm against the portal or a sandbox

1. **Throughput limits.** Requests per second, and whether read-heavy bulk
   extraction is throttled differently from operational queries. There is an
   open discussion thread on the developer portal asking exactly this; no
   published answer found. Also unknown: how many parallel sessions are
   tolerated.
2. **Change feed.** Is there a last-modified field on `WorkOrder` allowing
   incremental pulls of *edited* records, not just newly created ones? Without
   one, a work order that closes after our last pull is missed until a full
   re-extract. Current mitigation: re-pull a trailing 30-day window each run,
   which the idempotent upserts absorb cleanly.
3. **Labor records.** Confirm whether labor is a queryable top-level entity or
   only arrives nested under `WorkOrder`. This client harvests the nested form;
   if a top-level entity exists, pulling it directly would be cheaper.
4. **Exact status and priority vocabularies** for the BNSF Fort Worth tenant.
   The alias tables in `ingest/vocab.py` are generous, but the site's real
   values should be added explicitly rather than relying on fallbacks.
5. **Write commands** for adding a work order note and a labor entry — needed
   only if the draft workflow is ever promoted from "human submits" to
   "submitted through the API with human approval". Not in scope now.

## Sources

- Corrigo Enterprise REST API portal — <https://developer.corrigo.com/>
  ([Authorization](https://developer.corrigo.com/reference/authorization),
  [Executing Queries](https://developer.corrigo.com/reference/queryexpression),
  [Executing Commands](https://developer.corrigo.com/reference/cmd),
  [Entities and Operations](https://developer.corrigo.com/reference/entities),
  [Resources by Region](https://developer.corrigo.com/reference/endpoint))
- [Corrigo Enterprise 9 — Creating a user for the REST/RESTFUL API](https://support.jllt.com/s/article/Corrigo-Enterprise-9---Integration---Creating-a-user-for-the-REST-RESTFUL-API)
- [Corrigo Enterprise 9 — Integration — Auto Discovery](https://support.jllt.com/s/article/Corrigo-Enterprise-9-Integration-Auto-Discovery)
- [API throughput limits & extraction patterns for Location entity](https://developer.corrigo.com/discuss/6a397f14262b2c57a9f9b457) (developer forum)
- [JLLTCorrigo/PostmanCollections](https://github.com/JLLTCorrigo/PostmanCollections) — JLL Technologies' own public Postman collections; the authoritative request examples
- [Corrigo Enterprise SOAP API](https://developer-soap.corrigo.com/) (legacy)
