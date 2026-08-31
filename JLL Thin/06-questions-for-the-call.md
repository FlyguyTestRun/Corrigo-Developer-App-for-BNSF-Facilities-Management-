# Questions for the call

Ordered so the early ones establish footing and the later ones surface cost.
Each has a note on **what a weak answer sounds like** — that is usually more
useful than the question itself.

---

## Opening — establish who you're talking to

**1. "Are you Rockwell, or a distributor or system integrator?"**
Rockwell sells heavily through channel. Knowing which determines who is
accountable for implementation, and whether services are being quoted alongside
licences.

**2. "Who would actually implement and support this — and who do we call at 2am?"**
*Weak answer:* vagueness about support ownership, or "your IT team would handle
it." A facilities department without an IT bench needs this named explicitly,
with a contract behind it.

---

## Fit — before any feature discussion

**3. "Can you give us a reference customer that is a commercial facilities
operation, not a manufacturing plant?"**
The most revealing question in the list. ThinManager's native market is
industrial. If they can produce a facilities reference, the fit is real. If
every example is a factory, you have your answer without an argument.

**4. "We're a facilities team on a campus we manage for a client. Where does
this stop making sense?"**
*Weak answer:* "it works anywhere." Every product has a boundary. A rep who
names theirs is worth more than one who doesn't.

**5. "What's the break-even device count for us — below how many terminals does
this cost more than just replacing PCs?"**
Make them show the arithmetic rather than assert a number.

---

## Architecture — the answers determine feasibility

**6. "What has to change on the network — specifically, do we need DHCP options
modified, and whose approval is that?"**
PXE boot needs the network to tell terminals where to find the ThinManager
server. On a client-owned network that is **BNSF IT's** decision, not JLL's.
This question often ends the project timeline discussion before it starts. Ask
it early.

**7. "Does this integrate with Active Directory, and whose AD?"**
Same ownership issue. If it's BNSF's directory, BNSF has to agree.

**8. "How many Remote Desktop Session Hosts do we need for our user count, and
what are their specs?"**
This is where the real compute cost sits. ThinManager itself is light; the
session hosts are not.

**9. "Walk me through what happens when a session host fails at 3am."**
Failover is a headline feature. Have them describe the actual sequence. *Weak
answer:* "it fails over automatically" with no detail on session state, how long
it takes, or what the user sees.

**10. "Redundancy for the ThinManager server itself — stand-alone, mirrored, or
fully redundant? What does each cost?"**
If the management tier is a single point of failure, the resilience argument is
weaker than it sounds.

---

## Licensing and cost — where quotes get thin

**11. "Confirm the licensing unit: is it per terminal device, per concurrent
user, or per session?"**
Current model is **V-FLEX**, per terminal device, all features included. Confirm
it hasn't changed, and confirm how a device that's powered off counts.

**12. "Perpetual or subscription — and what's the five-year total for each?"**
Both exist. Subscription terms run up to five years. Ask for both numbers side
by side.

**13. "What's the Software Maintenance percentage, and is 24×7 support necessary
for our use case?"**
Roughly 20% of licence cost for 8×5, 30% for 24×7. On a campus where a failed
terminal is inconvenient but not an outage, 8×5 may be sufficient — that is a
real saving worth testing rather than accepting the default.

**14. "Does this quote include Windows Server licences and RDS Client Access
Licences?"**
Almost certainly not. **This is the most common omission in a first quote.** Get
the Microsoft cost stated explicitly.

**15. "What thin client hardware do you recommend, and is it *ThinManager Ready*
or *ThinManager Compatible*?"**
Ready hardware has support built in by the manufacturer; Compatible covers
generic devices via PXE. Different cost and supply implications. Also ask
whether you are locked to specific hardware vendors.

**16. "Give me total cost of ownership over five years: licences, maintenance,
thin client hardware, servers, Microsoft licensing, and implementation labour."**
If they will only quote licences, that is not a TCO and should not be treated as
one.

---

## Relevance — only if mobility genuinely matters

**17. "Is Relevance included in V-FLEX or licensed separately?"**
It has been positioned both ways at different points. Get it in writing.

**18. "Show me location-based delivery working — don't describe it."**
A live demonstration of a tablet gaining and losing access as it moves. This is
the feature most likely to be oversold in a slide and underwhelming in practice.

**19. "Which resolver would you actually recommend for our buildings, and why?"**
QR codes, Bluetooth beacons, Wi-Fi, GPS. A rep who has deployed this will have
opinions about which ones survive contact with a real building. One who lists
all four equally probably hasn't.

---

## Roadmap — the one people forget

**20. "ThinManager was acquired in 2016 and serves a small market. What does the
roadmap look like, and is this strategic for Rockwell or maintained?"**
Fair, not hostile. A confident answer names upcoming releases and investment. A
defensive one tells you something too.

---

## After the call — what should exist on paper

- A written quote separating **licences**, **maintenance**, **hardware** and
  **services**
- The **Microsoft licensing** requirement, stated explicitly
- A named **implementation owner** and support path
- At least one **facilities-sector reference** you may contact
- A clear statement of **what BNSF IT must approve** before anything begins

If the last item is missing, nothing else on the list matters yet.
