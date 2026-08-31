# Exporting your work orders from Corrigo

Everything here stays inside what your existing Corrigo login already permits.
No automation runs against your session, nothing is scraped, and no credential
is shared. You export reports you can already see on screen, and the loader
reads the files.

Written from the Corrigo Enterprise 9 documentation. **Exact button placement
varies by tenant configuration**, so this is written as decision points rather
than a fixed click path — find the equivalent in your instance.

---

## Step 0 — Establish what your role can actually see

This determines what is possible, so do it before anything else.

Log in at `enterprise.corrigo.com/CorpNet/Login.aspx`, or your site's tenant
host (JLL tenants look like `jll-<account>.corrigo.com`). Open **Work Orders**
and check two things:

**1. Is the default view "My Work"?**
Technician roles commonly land on *Work Orders (My Work)*. Look for a view
selector offering **All Work Orders** or similar.

- If it exists and returns other people's work orders → the department
  comparison is possible. Continue.
- If every view only ever returns your own → this produces a personal work
  history, and the department comparison waits until someone with broader
  access runs it. That is a fine outcome. **Do not go looking for a way
  around it.**

**2. What Work Zones do you have?**
Users inherit work zones from their team; an administrator may have granted
*All Work Zones* or a specific subset. Whatever your views return is your
legitimate scope. Don't widen it.

---

## Step 1 — Export the work orders

### Path A — Work Order List View (try this first)

`Work Orders` → the list view (`/corpnet/workorder/workorderlist.aspx`).

1. **Filter Created date** from `01/01/2026` through today.
2. **Set status to all — not just Completed.** Open and cancelled work orders
   are what make the aging and stall analysis possible. A completed-only export
   can answer half the question at best.
3. **Configure the visible columns.** List views let you show, hide and reorder
   columns, and the download respects those edits.
4. **Export / download as CSV** (or Excel).

### Path B — Reports module

If the list view offers no export: `Reports` → a Work Order report → set the
date range → run → save the output. Corrigo reports save as **XLS, CSV, XML or
PDF**. Choose CSV or XLS. If Excel export throws an error, that is a known issue
with its own support article — use CSV.

### Path C — Corrigo Business Intelligence

If your account has the BI module it gives cleaner extracts and Power BI
integration. Most technician roles will not have it. Worth one look, not worth
chasing.

---

## Step 2 — Columns

The loader auto-detects common header spellings, so the exact wording does not
matter. The *fields* do.

**Required** — rows missing any of these are dropped at load:

| Field | Why |
|---|---|
| Work Order ID **or** Number | Primary key |
| Status | Open vs closed drives every metric |
| Created / Opened date | **Without it a row cannot be aged, so it is dropped rather than given a fake date** |

**Each of these unlocks something specific:**

| Field | What it unlocks |
|---|---|
| Completed / Closed date | Time-to-completion — your headline number |
| Assigned To / Technician | The entire department comparison |
| Priority | SLA breach detection |
| Type (Reactive / PM / Inspection) | Preventive-vs-reactive mix |
| Asset / Equipment | Service history, recurring faults, repair-vs-replace |
| Location / Building / Space | Route sequencing, per-building backlog |
| Summary and Description | Fault patterns |
| Resolution / Completion notes | What actually fixed it — and the corpus a future retrieval system would be built on |

**Worth chasing separately: labor hours.** If the list view cannot produce hours
per work order, look for a labor, time or timesheet report. Labor hours power
stall detection — comparing logged hours against days open is what separates a
hard job from one nobody has touched, and it is the most original metric in this
project. Without them you still get aging and cycle time.

---

## Step 3 — Save the files

Put exports in `data/raw/`. That directory is gitignored, and CI fails the build
if anything under it is ever committed.

The loader matches on filename:

```
data/raw/workorders-2026.csv    → work orders
data/raw/labor-2026.csv         → labor hours
data/raw/assets.csv             → equipment      (phase 3)
data/raw/locations.csv          → buildings      (phase 3)
```

---

## Step 4 — Before you leave the office, check four things

1. The row count roughly matches what the UI said it would return.
2. The date range really does start in January.
3. **Does the Assigned To column contain names other than yours?** That single
   answer decides whether the department comparison is possible.
4. Whether the technician column holds names or employee IDs. Either works —
   the loader handles both.

---

## Step 5 — Load it

```bash
bnsf-fm load-csv data/raw --me "Your Name As It Appears In The Export"
```

`--me` decides whose name is kept. **Everyone else is anonymized either way** —
their names are discarded as rows are read and never written to the database, so
the file on your laptop cannot leak a co-worker's identity even by accident.
Each becomes a stable `Tech 1`…`Tech N`.

The load prints:

- how many rows landed in each entity,
- **columns that were present but not mapped** — on a first real export these
  are the ones worth adding to a mapping file,
- whether `--me` matched anything (if it didn't, the spelling is wrong),
- how many distinct technicians were seen.

If a required column could not be mapped, the error names the headers actually
present in your file. Add your site's spelling to a JSON mapping and pass
`--mapping`:

```json
{ "work_orders": { "id": ["BNSF Ticket Ref"], "opened_at": ["Raised On"] } }
```

Then:

```bash
bnsf-fm quality                              # what the export was missing
bnsf-fm scorecard --html scorecard.html      # you vs the department
bnsf-fm backlog                              # the department aging board
```

Loads are idempotent — re-exporting an overlapping window converges rather than
duplicating, so "export the last 90 days every Monday" works fine.

---

## What not to do

No automated login, no bulk attachment downloader, no shared credentials, and
no tenant you were not given access to. If something needs a workaround, it
needs a request instead — `docs/credential-request.md` is already written for
exactly that conversation.

---

## Phase 3 — equipment and manuals

Once the work order history is loaded and the scorecard is real:

- **Assets:** `Assets` → Asset Builder → export the equipment list.
- **Manuals:** documents attach to individual asset records and download from
  the asset detail screen (which now shows non-photo attachments in a carousel).
  Collect them by hand into `data/manuals/`, named by manufacturer and model.

That corpus is the retrieval showcase. But the work order history is what earns
the meeting, so do it first.
