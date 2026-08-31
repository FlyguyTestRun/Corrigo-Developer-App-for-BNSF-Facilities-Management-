# Glossary & term index

The vocabulary, grouped by the conversation it belongs to. **Skim the first two
sections before the call; keep the file open during it.**

Terms marked **[TM]** are ThinManager-specific product names — if a rep uses one
without explaining it, that is a fair moment to stop and ask. Everything else is
general industry vocabulary the rep will assume you already know.

---

## 1. The concepts you cannot fake

**Thin client** — A stripped-down endpoint device with minimal local operating
system and little or no storage, which displays a session running on a remote
server. It draws the screen and sends back input. *It is a class of device, not
a program.*

**Zero client** — Goes further: no operating system at all, just a fixed-function
chip that decodes one display protocol. Cheapest and most locked-down; commits
you to one vendor's protocol.

**Thick client / fat client** — A normal PC. Runs its own operating system and
applications locally. What thin clients are meant to replace.

**Endpoint** — Generic term for whatever sits in front of the user: thin client,
PC, tablet, phone.

**RDS — Remote Desktop Services** — Microsoft's technology for running many user
sessions on one shared Windows Server. Formerly **Terminal Services**, and you
will hear both. **This is the model ThinManager is built around.**

**VDI — Virtual Desktop Infrastructure** — The alternative: each user gets a
complete dedicated virtual machine. Better isolation, materially more expensive.

**Session** — One user's running environment on a server. The thing the thin
client is displaying.

**Session broker** — The component deciding which server a given connection is
routed to, and reconnecting a user to their existing session.

---

## 2. Infrastructure and protocols

**RDP — Remote Desktop Protocol** — Microsoft's protocol for carrying screen
images down and keyboard/mouse input up. Runs on **TCP port 3389**. The main
transport in a ThinManager deployment.

**PXE boot** ("pixie boot") — *Preboot Execution Environment.* Lets a device
with no operating system boot over the network. The device asks the network for
an address and a boot file, downloads its operating firmware, and starts.
**This is how a ThinManager terminal comes to life with nothing installed on it.**

**DHCP** — The network service handing out IP addresses. In this architecture it
also tells a booting terminal where the ThinManager server is and what boot file
to request. ThinManager also ships its own PXE server for cases where the
corporate DHCP server can't be changed — a practical point, since **changing
DHCP options usually requires the customer's network team**, and that is often
the real scheduling constraint on a deployment.

**Firmware** — The small operating image the terminal downloads at boot. Not
installed on the device; delivered fresh from the management server every time.

**Terminal Server / Remote Desktop Session Host (RDSH)** — The Windows server
that actually runs the applications. Where the compute cost lives.

**CAL — Client Access Licence** — Microsoft's licence to connect to a Windows
Server. **RDS CALs are a separate cost from ThinManager licences** and are a
classic omission from a first quote.

**Active Directory (AD)** — Microsoft's directory service; the corporate user and
password database. ThinManager integrates with it so users aren't managing a
second set of credentials.

---

## 3. ThinManager product terms

**ThinManager** **[TM]** — The management software. Also the name of the
administrative console specifically.

**ThinServer** **[TM]** — The Windows **service** that is the actual engine —
holds the configuration database and serves firmware and configuration to
terminals. Runs headless. *The console is not the product; this is.*

**Relevance** **[TM]** — The location- and user-based delivery module. Decides
what content a device may show based on who the user is and where they
physically are.

**Location resolver** **[TM]** — The mechanism Relevance uses to determine
location: **QR code**, **Bluetooth beacon**, **Wi-Fi**, **GPS**, or **NFC**.

**TermSecure** **[TM]** — The older name for the user-based access control layer;
in current versions the vocabulary is **Relevance User Services**. Grants a user
access to specific applications, including applications hidden from the terminal
by default, and lets a user's applications follow them between terminals.

**Display Client** **[TM]** — ThinManager's term for a deliverable *thing to
show* — an application, a desktop, a web page, a camera feed, a virtual machine.
Not a physical client. **This term causes the most confusion**, because "client"
everywhere else in the conversation means a device.

**MultiSession** **[TM]** — One terminal showing several Display Clients at once,
normally cascaded with one visible at a time.

**MultiMonitor** **[TM]** — Driving several physical screens from one terminal,
with different content on each.

**Terminal** **[TM]** — In ThinManager's vocabulary, a configured endpoint. The
licensing unit.

**V-FLEX** **[TM]** — The current licensing model (since August 2019). One
licence covers all features for one terminal device. Perpetual or subscription.

**Failover** **[TM]** — Automatic switching to a backup server when one fails,
including logging back in and relaunching applications without human action.

**Redundancy** **[TM]** — Applied to the ThinManager servers themselves:
stand-alone, mirrored, or fully redundant configurations.

**ThinManager Ready / ACP Enabled** **[TM]** — Hardware with ThinManager support
built in by the manufacturer. Contrast with **ThinManager Compatible**, which
covers generic off-the-shelf devices brought in via PXE boot. Worth asking which
category any recommended hardware falls into, and what that implies for cost and
supply.

---

## 4. Rockwell and industrial context

**Rockwell Automation** — The owner (NYSE: ROK). Industrial automation company;
also Allen-Bradley.

**ACP — Automation Control Products** — ThinManager's original developer,
founded 1999, acquired by Rockwell in September 2016.

**FactoryTalk** — Rockwell's software portfolio. ThinManager sits inside its
Operation Suite.

**Connected Enterprise** — Rockwell's strategy label for connecting plant-floor
data to enterprise systems. The stated rationale for buying ACP.

**HMI — Human-Machine Interface** — The operator screen for a piece of industrial
equipment. A primary thing ThinManager delivers in its native market.

**SCADA — Supervisory Control and Data Acquisition** — Software that monitors and
controls industrial processes across a site.

**PLC — Programmable Logic Controller** — The industrial computer that actually
controls equipment. Allen-Bradley's core business.

**OT — Operational Technology** — The technology that runs physical equipment, as
distinct from **IT**. Different security models, different teams, often
different budgets. **Which side of that line this purchase falls on determines
who has to approve it.**

**BMS — Building Management System** — The facilities equivalent of SCADA:
HVAC, lighting, fire and access control for a building. **This is the relevant
analogue for a JLL campus.**

---

## 5. Commercial terms

**Perpetual licence** — Buy once, own indefinitely. Larger up-front cost.

**Subscription licence** — Recurring fee; ThinManager terms run up to five years.

**Software Maintenance** — Support and upgrade rights, charged as a percentage of
licence cost: roughly **20% for 8×5**, **30% for 24×7**.

**Volume discount** — Applied automatically at higher device counts.

**System Integrator (SI)** — The firm that typically implements this. Often the
party actually on your sales call.

**TCO — Total Cost of Ownership** — The number that matters. **Must include**
ThinManager licences + maintenance + thin client hardware + Windows Server
licences + RDS CALs + the servers themselves + implementation labour. A quote
covering only the first line is not a TCO.

---

## Quick reference: what to say vs. what not to

| Instead of | Say |
|---|---|
| "your thin client program" | "the thin client endpoints" or "ThinManager" — depending which you mean |
| "thin manager" (two words) | "ThinManager" (one word, it's a product name) |
| "the client software" | "the firmware" (on the device) or "the Display Client" (the delivered content) |
| "the server" | "the ThinManager server" or "the session host" — they are different machines doing different jobs |

---

## Sources

Vendor manuals and knowledge base ([ThinManager manuals](https://thinmanager.com/support/manuals/),
[Knowledge Base](https://kb.thinmanager.com/index.php/System_Requirements),
[PXE boot](https://kb.thinmanager.com/index.php/ThinManager_and_PXE_Boot),
[Active Directory & TermSecure](https://thinmanager.com/technotes/09_Security/ActiveDirectoryAndTermSecure.pdf),
[Relevance manual](https://thinmanager.com/support/manuals/files/TM_8_Relevance_Manual.pdf)),
[Rockwell licensing documentation](https://thinmanager.com/licensing/),
and [TechTarget's client-type comparison](https://www.techtarget.com/searchvirtualdesktop/feature/VDI-hardware-comparison-Thin-vs-thick-vs-zero-clients).
