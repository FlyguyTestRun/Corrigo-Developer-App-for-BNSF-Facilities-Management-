# Facilities intelligence at BNSF Fort Worth — the case

For the facilities manager, and for whoever he escalates it to.

---

## The problem, stated without euphemism

Work orders sit. Not all of them, and not always for bad reasons — but the site
has no way to tell the difference between a work order that is genuinely in
progress and one that is merely open. Both look the same on a status board.

That single gap produces four downstream effects:

1. **Accountability is arbitrary.** Without an aging view, "who is sitting on
   work" is a matter of impression rather than record. That is unfair to the
   people doing the work and useless to the person managing it.
2. **Equipment knowledge lives in people's heads.** Manuals, prior repairs, and
   what actually fixed a recurring fault are not available at the point of
   work.
3. **Walking time is unmanaged.** Nobody sequences a day's work by proximity,
   so techs cross the campus more than they need to.
4. **Parts are tracked informally.** Stock-outs are discovered at the unit.

## What was built

A local reporting and workflow layer over the site's own Corrigo data. It runs
on a laptop, holds data in a single SQLite file, and sends nothing anywhere.

**Four capabilities:**

- **Work order aging and stall detection.** Aging distribution, SLA breach
  flags, and the reconciliation described below.
- **Asset registry with manuals and service history.** Every unit, where it is,
  what it is, its O&M manual, every prior repair, and a repair-vs-replace flag.
- **Route sequencing.** A day's open work orders ordered by building proximity
  and floor, weighted so emergency and past-SLA work is pulled forward, with
  the parts to stage before walking out.
- **Parts inventory.** Stock levels, consumption derived from closed work
  orders, days of cover, and reorder-point recommendations grounded in observed
  burn rate rather than a guess.

Plus an MCP server, so the whole dataset can be queried conversationally
through Claude — including questions nobody built a screen for.

## The insight worth the meeting

Every CMMS reports how many work orders are open. This reconciles **logged
labor hours against days open**.

A work order open 20 days with 18 hours logged and one open 20 days with 0.5
hours logged are identical on a Corrigo status board. They are completely
different operational situations, and only one of them is an accountability
problem. The first is a hard job. The second is a work order nobody has touched.

On the synthetic campus shipped with this repository — sized and shaped to
match a real rail-operations site — that split identifies **58 of 105 open work
orders as stalled, holding roughly 4,200 days of accumulated calendar time.**
Those are not 58 people being lazy; most will have a reason. But right now
nobody is asked, because nobody can see the list.

That list is the deliverable. It is short, it is specific, and it is workable
in an hour a week.

## Why this is defensible to run

The objections a manager should raise, answered before they are asked:

- **It reads; it does not write.** No Corrigo write command is implemented. The
  AI note-drafting feature produces a draft the technician reviews and submits
  themselves — every draft is stamped `requires_human_submission`.
- **Names are pseudonymized by default.** Technicians appear as `TECH-nn`
  everywhere unless someone explicitly asks to see names. The default report —
  the one that gets screenshotted or pasted into a deck — carries none.
- **The metrics are volume-normalized.** Median cycle time and SLA-met rate,
  not raw counts, so nobody is penalized for being handed the hard work.
- **Data never leaves the machine.** Local SQLite, gitignored, no cloud
  service.
- **It needs no permission to start.** The first tier runs on CSV exports any
  user can already pull from Corrigo. API credentials are an upgrade, not a
  prerequisite.

## What the industry evidence says this is worth

Not projections from this project — published figures on the platform this
extends:

- Forrester Consulting's Total Economic Impact study of Corrigo found **238%
  ROI**, and **$1.6M saved over three years** by shifting spend from reactive
  into preventive maintenance.
- JLL's own AI-powered facilities management client story (a global
  semiconductor manufacturer) reports **over 5x ROI**, a **75% reduction in
  maintenance response time**, a **50% decrease in unplanned downtime**, a
  **90% improvement in asset data accuracy**, and the highest client
  satisfaction scores in that account's thirteen-year history.

The through-line in both is the same: the money is in moving work from reactive
to planned, and you cannot plan against equipment whose history you have not
recorded. An asset registry with real service history is the prerequisite for
the number, not a nice-to-have alongside it.

Corrigo Business Intelligence is already moving in this direction — Power BI
integration, asset lifecycle analytics, repair-versus-replace costing. This
project should be read as **extending the platform's own direction of travel at
one site**, not competing with it. That framing matters if it reaches JLLT.

## What it would take to run it for real

| Step | Owner | Effort |
|---|---|---|
| Export three reports from Corrigo, load them | Site | 30 min, once |
| Confirm building list and rough walking times | Site | 1 hour, once |
| Review the stalled list weekly | FM | ~1 hour/week |
| Add O&M manuals as they are collected | Site | ongoing, incremental |
| Request a read-only API service account | Corrigo sysadmin | see `credential-request.md` |

The last row is optional. Everything above it works today.

## Why this generalizes

The architecture separates *where data came from* from *everything that reads
it*. One module knows about CSV exports and the REST API; nothing downstream
does. That means the same codebase runs at any Corrigo site — the ingestion
mapping changes, the analytics do not.

If it works at Fort Worth, it works at the next campus. That is a platform
argument rather than a site tool, and it is the reason this is worth a
conversation above site level.
