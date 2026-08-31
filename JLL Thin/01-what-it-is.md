# What these things actually are

## The device spectrum

Endpoints sit on a spectrum defined by **how much work happens locally**:

| | Local OS | Local storage | Local processing | Managed how |
|---|---|---|---|---|
| **Thick client** (a normal PC) | Full Windows/macOS/Linux | Yes | All of it | Individually, or by enterprise IT tooling |
| **Thin client** | Minimal — a small Linux or Windows-embedded OS whose job is to launch a remote session | Little or none | Almost none; it draws the screen | Centrally, from a management server |
| **Zero client** | None | None | A fixed-function chip that decodes one display protocol | Centrally, but locked to that protocol |

The trade-off runs in a straight line. Thick clients are flexible and
independent, and every one of them is a machine somebody has to patch, back up
and eventually replace. Zero clients are the cheapest and most locked-down, and
because the protocol is burned into hardware you are committed to whichever
vendor's ecosystem it speaks. Thin clients sit in between, and that is where
most industrial and control-room deployments land.

## Where the computing goes instead

If the endpoint isn't doing the work, something else is. Two models:

**RDS — Remote Desktop Services** (older name: Terminal Services). One Windows
Server runs many user *sessions* at once, all sharing a single operating system
instance. Cheaper per user, but one badly-behaved application can affect
everyone on that server. **This is the model ThinManager is built around.**

**VDI — Virtual Desktop Infrastructure.** Each user gets a complete virtual
machine on a hypervisor. Better isolation and per-user customization,
considerably more expensive in licensing and hardware.

A useful shorthand: RDS is a shared apartment building, VDI is a street of
individual houses. ThinManager's home turf is the apartment building.

## So what is ThinManager?

Management software for the endpoints. Concretely it handles:

- **Identity and configuration** — which terminal is which, and what each one
  should display when it powers on
- **Boot and firmware delivery** — terminals get their operating firmware from
  ThinManager over the network, not from a local disk
- **Session brokering** — connecting each terminal to the right server session
- **Failover** — if a server dies, terminals move to a backup, log in, and
  restart the applications automatically
- **Access control** — who may see which application, on which terminal
- **Mobility** (the *Relevance* module) — delivering applications to tablets and
  phones based on physical location

The pitch, stripped of marketing: **when a terminal fails, you unplug it, plug
in a replacement, and it configures itself.** No imaging, no reinstalling
applications, no per-device visit. On a site with many near-identical operator
stations, that is the entire argument.

## The second product name you'll hear: Relevance

**Relevance** is ThinManager's location-and-user-based delivery module. It uses
QR codes, Bluetooth beacons, Wi-Fi and GPS as *location resolvers* to decide
what a mobile device may show based on where it physically is.

Vendor framing is "secure walls around your data and applications." The
practical example: a tablet shows a given system's controls only while the
technician is standing in that mechanical room, and loses them on the way out.

Whether Relevance is bundled or licensed separately is a question for the call —
it has been positioned both as a distinct product and as a ThinManager feature
set at different points since the acquisition.

---

## Sources

- [VDI hardware comparison: thin vs. thick vs. zero clients — TechTarget](https://www.techtarget.com/searchvirtualdesktop/feature/VDI-hardware-comparison-Thin-vs-thick-vs-zero-clients)
- [Thin clients vs. zero clients — 10ZiG](https://www.10zig.com/resource/thin-clients-vs-zero-clients-vdi-10zig-technology/)
- [ThinManager overview](https://thinmanager.com/products/thinmanager.php) · [Product profile](https://thinmanager.com/profile/)
- [Relevance for ThinManager (vendor manual, PDF)](https://thinmanager.com/support/manuals/files/TM_8_Relevance_Manual.pdf)
- [ThinManager on FactoryTalk — Rockwell Automation](https://www.rockwellautomation.com/en-us/products/software/factorytalk/operationsuite/thinmanager.html)
