# How it works — architecture and tech stack

## The stack, top to bottom

```
                         ┌─────────────────────────────┐
   ADMIN                 │  ThinManager  (admin GUI)   │
                         └──────────────┬──────────────┘
                                        │ configures
                         ┌──────────────▼──────────────┐
   MANAGEMENT            │  ThinServer  (Windows        │  ← the actual engine:
   (Windows Server)      │  service + config database)  │    runs headless, holds
                         └───┬───────────────────┬──────┘    all configuration
                             │                   │
              firmware +     │                   │  brokers sessions
              configuration  │                   │
                             │                   ▼
                             │      ┌────────────────────────────┐
   COMPUTE                   │      │ Remote Desktop Session      │
   (Windows Server)          │      │ Hosts — the servers that    │
                             │      │ actually run the apps       │
                             │      └────────────┬───────────────┘
                             │                   │ RDP  (TCP 3389)
                             ▼                   ▼
                    ┌─────────────────────────────────────┐
   ENDPOINTS        │ Thin clients · PCs · tablets/phones │
                    │ (draw the screen, send input back)  │
                    └─────────────────────────────────────┘
```

Three tiers: something to **manage** the endpoints, something to **run** the
applications, and the **endpoints** themselves. ThinManager is only the first
tier. The applications still need Windows servers to run on, and those are
licensed separately by Microsoft — a cost that is easy to leave out of a
comparison and worth pinning down on the call.

## The two components you install

Installing ThinManager puts down **two** things, and the distinction matters:

- **ThinServer** — a Windows **service**. This is the engine. It runs in the
  background, starts automatically, holds the configuration database, and does
  the real work of serving firmware and configuration to terminals. Nothing
  works without it. It runs under a specific service account, which is a common
  source of setup trouble.
- **ThinManager** — the **administrative interface**. A console for a human to
  configure ThinServer. You can close it and everything keeps running.

If the rep says "ThinManager is down," the useful follow-up is *which one* — the
console being closed is a non-event; the ThinServer service stopping takes the
floor down.

## What happens when a terminal powers on

This sequence is the heart of the product, and understanding it makes most of
the vocabulary fall into place:

1. **The terminal powers on with no operating system of its own.**
2. **It asks the network who it is.** DHCP assigns it an IP address, and tells
   it the address of the ThinManager server and the name of a boot file. (That
   information can come from DHCP options or from ThinManager's own built-in
   PXE server.)
3. **It downloads firmware from ThinManager** over the network — this is **PXE
   boot**. The terminal has now become a functioning ThinManager client without
   anything having been installed on it locally.
4. **It downloads its configuration** — which is where the identity lives. The
   configuration says which applications this terminal shows and which server to
   connect to.
5. **It connects to a Remote Desktop Session Host over RDP** (TCP port 3389) and
   logs in.
6. **From then on it is a picture frame.** Keystrokes and mouse movements go up
   to the server; screen images come back down. All processing is server-side.

Step 3 is what delivers the headline benefit. Because nothing persists on the
device, a dead terminal is replaced by physically swapping the box — the
replacement boots, identifies itself, pulls the same configuration, and resumes.
No imaging, no software install, no configuration visit.

## Key capabilities, translated

| Vendor term | What it actually does |
|---|---|
| **Failover** | Terminals are configured with a list of servers. If one dies, the terminal moves to the next, logs in, and relaunches the applications by itself. |
| **Redundancy** | Applies to the ThinManager servers themselves — available as stand-alone, mirrored, or fully redundant pairs, so the management tier isn't a single point of failure. |
| **MultiSession** | One terminal displaying several application sessions at once, normally cascaded with one active at a time — a single screen switching between several systems. |
| **MultiMonitor** | Driving multiple physical displays from one terminal, with different content on each. |
| **TermSecure / Relevance User Services** | The access-control layer. Ties a user to permitted applications, so a login can carry a person's applications to whichever terminal they walk up to. Integrates with **Active Directory**, so passwords stay managed in one place rather than becoming a second credential set. |
| **Relevance** | Location-based delivery — QR codes, Bluetooth beacons, Wi-Fi, GPS as *resolvers* that decide what a mobile device may show based on where it is. |

## System requirements

Light. Vendor guidance is that if a machine can run Windows Server 2003 or
newer, it can run ThinManager without noticeable performance impact — the
management tier is not the demanding part of the architecture. **The Remote
Desktop Session Hosts are where you spend the money**, because that is where all
the actual application processing happens for every concurrent user.

Current generation is **ThinManager v14**, organized around four vendor-stated
pillars: productivity, visualization, security and mobility.

## Licensing — the mechanics

- **V-FLEX** licensing, introduced August 2019, is the current model. **One
  V-FLEX licence covers all product features for one terminal device.** So you
  count devices, not features.
- Available as **perpetual** or **subscription**. Subscription terms run up to
  five years, payable annually or in full up front.
- **Volume discounting** applies and is automatic on Rockwell's commerce portal.
- **Software Maintenance is charged on top**: roughly **20%** of the terminal
  connection licence price for 8×5 support, **30%** for 24×7.

Two things to hold in mind: the licence count follows **terminals**, so the
question "how many endpoints, over five years, including growth?" drives the
whole number — and the **Windows Server and RDS Client Access Licences are a
separate Microsoft cost** that is not part of the ThinManager quote.

---

## Sources

- [ThinManager system overview (vendor manual, PDF)](https://thinmanager.com/technotes/01_Intro/Manual60/TM6_Chapter2_System%20Overview.pdf)
- [ThinManager and PXE Boot — Knowledge Base](https://kb.thinmanager.com/index.php/ThinManager_and_PXE_Boot) · [PXE Boot tech note (PDF)](https://www.thinmanager.com/technotes/04_Configuration/ThinManager_PXEBoot.pdf)
- [PXE Server configuration (vendor manual, PDF)](https://thinmanager.com/technotes/01_Intro/Manual60/TM6_Chapter11_PXE%20Server%20Configuration.pdf)
- [ThinManager manual v10 (PDF)](https://thinmanager.com/support/manuals/files/ThinManual_10.pdf)
- [Active Directory and TermSecure (PDF)](https://thinmanager.com/technotes/09_Security/ActiveDirectoryAndTermSecure.pdf)
- [System requirements — Knowledge Base](https://kb.thinmanager.com/index.php/System_Requirements) · [Installation requirements (PDF)](https://thinmanager.com/support/ThinManagerInstallationRequirements_3.20.2024.pdf)
- [Licensing resource centre](https://thinmanager.com/licensing/) · [ThinManager: Licensing — Rockwell support](https://support.rockwellautomation.com/app/answers/answer_view/a_id/1072135/~/thinmanager:-licensing-)
- [ThinManager ordering guide (PDF)](https://literature.rockwellautomation.com/idc/groups/literature/documents/qr/tm-qr001_-en-p.pdf) · [Software maintenance (PDF)](https://thinmanager.com/software-maintenance/RA_TM-SoftwareMaintenance_202110143P.pdf)
