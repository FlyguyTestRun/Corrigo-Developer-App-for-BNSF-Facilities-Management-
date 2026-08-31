# Does this fit a JLL-managed campus?

An honest read, written to be useful rather than encouraging. Nobody benefits
from walking into a sales call already sold.

## Start with the awkward question

ThinManager is **industrial** software, sold by an **industrial automation**
vendor, whose native customer is a manufacturing plant with PLCs, HMIs and
SCADA. A JLL-managed office and operations campus is not that.

That does not disqualify it. It does mean the first question is not "how much?"
but **"what problem are we actually solving, and is this the natural tool for
it?"** If the honest answer is "we have a lot of shared workstations that are a
pain to maintain," that is a general IT problem with a large field of general IT
solutions — IGEL, Stratodesk, Citrix, Omnissa/VMware Horizon, 10ZiG — several of
which are cheaper and better staffed for a commercial-office context.

ThinManager earns its place when there is genuine **OT** in the picture:
building automation front-ends, control-room displays, equipment interfaces —
things where the industrial heritage stops being a mismatch and starts being the
reason it works.

## Where it plausibly fits at BNSF Fort Worth

**1. Shared operational workstations.** Mechanical rooms, shop terminals, guard
posts, dispatch desks — machines used by many people across shifts, running a
small fixed set of applications. This is the strongest case. Failure recovery
becomes a hardware swap rather than a rebuild, which for a facilities team with
no dedicated IT bench is a real operational difference.

**2. Building management system front-ends.** A BMS is structurally the
facilities equivalent of SCADA — HVAC, lighting, fire, access control. This is
the closest thing on a commercial campus to ThinManager's native use case, and
the honest test of whether the fit is real. If the campus BMS has multiple
operator terminals that need identical configuration and high availability, the
argument is legitimate.

**3. Harsh or awkward locations.** Thin clients have no spinning disk and often
no fan. Mechanical spaces, dusty shops and unconditioned areas are exactly where
ordinary PCs die early. If the site is currently losing PCs to environment, that
is a cost you can actually quantify.

**4. Location-bound mobile access (Relevance).** Delivering a system's controls
to a tablet only while the technician is physically standing in that mechanical
room. Genuinely interesting for a facilities team, and worth seeing demonstrated
rather than described — ask them to show it, not slide it.

## Where the case gets weak

**General office desktops.** Knowledge workers with varied applications, local
peripherals and personal configuration are a poor thin-client fit, and this is
not the product to reach for. Do not let the conversation drift there.

**Anything BNSF's own IT owns.** This is the one that decides the whole thing.
On a campus JLL manages for a client, network, Active Directory, server
infrastructure and endpoint policy are very likely **BNSF's**, not JLL's.
ThinManager needs DHCP options changed, servers stood up, AD integration and
firewall rules. **If BNSF IT is not in the room, the project does not exist yet
regardless of what the quote says.** Establishing who owns which layer is more
important than any feature comparison.

**Small device counts.** Licensing is per terminal, plus 20–30% maintenance, plus
Windows Server and RDS CALs, plus the session-host hardware. Below some
threshold, the centralized architecture costs more than the PCs it replaces. Ask
the vendor directly where that break-even sits for a deployment of your size and
make them show the arithmetic.

**Adding a system nobody on site can support.** A facilities team without
in-house IT depth taking on a Windows server tier, an RDS estate and a niche
management product is taking on real operational risk. Who administers this at
2am when a session host stops responding? If the answer is a system integrator
on a support contract, that contract is part of the cost.

## The comparison to insist on

Before accepting a ThinManager quote, get a like-for-like on:

| Option | Roughly what it means |
|---|---|
| **Do nothing** | Keep replacing PCs. The baseline. Get the real current number: how many failures a year, how many hours per rebuild. |
| **General thin-client management** | IGEL, Stratodesk, 10ZiG — hardware-agnostic, commercial-IT oriented, generally cheaper and more widely supported |
| **ThinManager** | Stronger where OT and industrial interfaces are involved; deepest failover and location-based features |
| **Whatever BNSF IT already runs** | If they already have a VDI or endpoint-management standard, that is very likely the answer, and the cheapest one |

That last row is the one people skip and shouldn't. The cheapest good outcome is
usually extending something the client already owns and already staffs.

## What a decision actually needs

1. A real count of the workstations in scope, and what each one runs.
2. Their current failure rate and rebuild cost — the number the business case
   rests on.
3. Which of them touch OT/BMS systems versus ordinary office use.
4. Written confirmation of who owns network, AD and servers — JLL or BNSF.
5. A five-year TCO including Microsoft licensing and implementation labour, not
   just ThinManager licences.
6. At least one reference customer that is a **commercial facilities operation**,
   not a manufacturing plant.

Point 6 is the single most revealing request you can make. If the vendor can
produce a facilities-management reference, the fit is real. If every reference
is a factory, that is the honest answer to the fit question and it arrived
without anyone having to argue about it.

---

## Sources

- [ThinManager on FactoryTalk — Rockwell Automation](https://www.rockwellautomation.com/en-us/products/software/factorytalk/operationsuite/thinmanager.html)
- [ThinManager deployment guide for remote operation (Rockwell, PDF)](https://literature.rockwellautomation.com/idc/groups/literature/documents/at/tm-at001_-en-p.pdf)
- [SCADA vs BMS — technical differences and integration](https://industrialmonitordirect.com/blogs/knowledgebase/scada-vs-bms-technical-differences-and-integration-guide)
- [Thin client alternatives — Stratodesk](https://stratodesk.com/top-4-thin-client-alternatives-for-vdi-daas-environments/)
- [ThinManager alternatives — SourceForge listing](https://sourceforge.net/software/product/ThinManager/alternatives)
- [ThinManager licensing resource centre](https://thinmanager.com/licensing/)
