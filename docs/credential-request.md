# Requesting Corrigo API access

A one-pager to hand to whoever holds Corrigo System Administrator for the
account. Written so that person can act on it without a meeting.

Nothing in this project is blocked on this request — the CSV path works today.
This is the upgrade, not the prerequisite.

---

## The ask, in one paragraph

> We are building a read-only reporting layer over our own Corrigo work order
> and asset data for the BNSF Fort Worth campus: work order aging, labor
> reconciliation, asset service history, and parts inventory. We would like a
> Corrigo Enterprise REST API service account with **read scope only**, issued
> against a Stage/Preview company if one is available. We are not requesting
> write access, and the tool does not write to Corrigo.

## What specifically needs to be created

Per the Corrigo Enterprise 9 integration documentation:

1. A **service user account** — not a person's login — dedicated to this
   integration.
2. That account assigned the **WSDK** role, or any role carrying
   **Permissions → Web Services Access**.
3. The account linked in **global settings** to generate a **client id** and
   **client secret**.
4. The **company name** for the tenant, needed for the `CompanyName` header
   used in API host discovery.

Note for whoever does step 3: **the client secret is shown once and the dialog
cannot be reopened.** Capture it before closing.

## What we need handed over

| Item | Notes |
|---|---|
| `client_id` | |
| `client_secret` | Shown once — transfer through the approved secrets channel, not email or chat |
| Company name | For the `CompanyName` header |
| Environment | Stage/Preview preferred for the pilot; Live read-only if no sandbox exists |

These go into environment variables on a single machine. They are never
committed — see the repository `.gitignore`, which blocks `.env` and everything
under `data/`.

## Scope and safeguards, stated up front

Because "an API integration" invites reasonable questions, here is the answer
to each before it is asked:

- **Read only.** The client implements query operations. No write command is
  implemented. `bnsf_fm/ingest/corrigo_api.py` contains no call to any Corrigo
  mutation endpoint, and that is enforced by the code, not by policy.
- **AI-drafted updates are never auto-submitted.** The field-note feature
  produces a draft that the technician reviews and enters themselves. Every
  draft is stamped `requires_human_submission: true`.
- **Data stays local.** A SQLite file on one machine. No cloud service, no
  third-party analytics, no data leaves the device.
- **Names are pseudonymized by default.** Technicians appear as `TECH-nn` in
  every report unless the person running it explicitly passes a reveal flag.
  The default output — the version that gets screenshotted or pasted into a
  deck — carries no real names.
- **Rate-respectful.** Queries page at 2000 rows, half the documented 4000
  ceiling, in 24-hour time windows, on a scheduled run — not a live query per
  page view.
- **Auditable.** The integration uses its own service account, so everything it
  does is attributable in Corrigo's logs and can be revoked independently of
  any person's login.

## Questions to put to the same person

1. Is there a **Stage or Preview company** we can point at instead of Live?
2. Are there **throughput limits** we should code against? The developer forum
   has an open question on transactions-per-second for bulk extraction with no
   published answer.
3. Does Enterprise API access on this account carry a **cost or contract
   change**? Nothing public suggests a developer-program fee, but the account
   team would know.
4. Is there an existing **Corrigo Business Intelligence** entitlement? If BI
   already exposes the extracts we need, that may be a shorter path than the
   API for the reporting half of this.

## If the answer is no, or not yet

The project runs on Tier 1 ingestion — CSV and Excel exports pulled from the
Corrigo UI by a user under their own existing permissions, dropped into
`data/raw/`, and loaded with `bnsf-fm load-csv`. Same schema, same analytics,
same dashboard. The only cost of staying on Tier 1 is that refreshing the data
is a manual step rather than a scheduled one.

Nothing needs to be rebuilt when credentials arrive: `ingest/corrigo_api.py` is
already written and unit-tested against recorded HTTP fixtures, including token
refresh at the 20-minute boundary, host rediscovery, and paging past the row
ceiling. Switching is `bnsf-fm load-api` instead of `bnsf-fm load-csv`.
