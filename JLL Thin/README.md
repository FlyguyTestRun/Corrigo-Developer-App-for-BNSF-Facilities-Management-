# ThinManager & Thin Clients — briefing pack

Research for the BNSF facilities department, prepared so a colleague can walk
into a vendor sales call knowing what the product actually is, what the words
mean, and which questions are worth asking.

**Read in this order:**

| File | What it answers |
|---|---|
| [`01-what-it-is.md`](01-what-it-is.md) | The one thing to get straight before the call |
| [`02-who-owns-it.md`](02-who-owns-it.md) | Rockwell Automation, and why the ownership matters commercially |
| [`03-how-it-works.md`](03-how-it-works.md) | Architecture and tech stack — what talks to what |
| [`04-glossary.md`](04-glossary.md) | **The vocabulary index.** Skim before the call, keep open during it |
| [`05-jll-bnsf-fit.md`](05-jll-bnsf-fit.md) | Honest read on whether this fits a JLL-run office campus |
| [`06-questions-for-the-call.md`](06-questions-for-the-call.md) | What to ask, and what a weak answer sounds like |

---

## The 60-second version

**"Thin client" is not a program.** It is a *class of device*: a small, cheap,
low-power endpoint with little or no local storage that does almost no
computing itself. It draws a screen. The actual work happens on a server
somewhere else, and the device just shows the picture and sends back keystrokes
and mouse clicks.

**ThinManager is a program** — specifically, management software owned by
**Rockwell Automation**, the industrial automation company. It is what tells a
room full of thin clients who they are, what to display, who is allowed to use
them, and what to do when a server dies.

So they are not two competing products or two halves of a suite. One is the
hardware concept; the other is one vendor's software for managing that
hardware. You can have thin clients without ThinManager. ThinManager without
thin clients to manage makes no sense.

## Why the distinction matters in a sales call

If your colleague opens with "tell us about your two products, thin client and
thin manager," the rep instantly knows the room is unfamiliar with the space —
and the conversation shifts from evaluation to education, on the vendor's
terms. Opening with "we're evaluating centralized endpoint management for our
facility workstations; walk us through where ThinManager fits against a general
VDI stack" produces a very different meeting.

## Provenance and how much to trust this

Assembled from Rockwell Automation and ThinManager vendor documentation, the
2016 acquisition press coverage, and independent comparisons.

**Caveat worth stating plainly:** `thinmanager.com` and
`kb.thinmanager.com` were not directly reachable from the environment this
research ran in, so vendor-page details came through search-result extracts of
those pages rather than by reading them end to end. Everything load-bearing is
sourced and marked. Version numbers, current pricing, and licensing specifics
change — **confirm those on the call rather than quoting this document at the
vendor.** Sources are listed at the foot of each file.
