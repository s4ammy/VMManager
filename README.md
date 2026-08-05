# VMManager

A desktop app for managing libvirt/QEMU virtual machines on Linux. Built to
replace virt-manager, and to do the things virt-manager leaves you doing by hand.

Machines are shown as wide rows with live usage graphs. The console is built in,
for both VNC and SPICE. Everything about a machine's hardware is editable, and
most of it while the machine runs.

```sh
git clone <this repository>
cd VmManager
./install.sh
```

See **[INSTALL.md](INSTALL.md)** for what it needs and the other ways to install
it, and **[FEATURES.md](FEATURES.md)** for what it can do.

## A look at it

![The machine list: each machine as a wide row with its state, operating-system logo, memory and vCPU count, and a Start button](Previews/machines.png)

Machines as rack bays. The strip down the left edge and the LED carry the state,
and the host's own load sits in the corner of the sidebar.

![The hardware tab: a list of every device on the left, grouped into system, network, display and peripherals, with the processor's details on the right](Previews/hardware.png)

Every device on the machine, grouped. Right-click a row to take it off; the panel
on the right edits whatever is selected. `debug *` beside Start is the current
mode, with the `*` saying the definition has drifted from it.

![The history tab: a list of timestamps on the left and a coloured diff of the selected version against the current one on the right](Previews/history.png)

Every change to a machine's definition is kept. Pick a version to see what
changed, and restore it if you want it back.

## A few things it does that virt-manager does not

- **Cloud images in the wizard.** Pick Debian, Ubuntu, AlmaLinux, Rocky or Arch;
  it downloads, verifies, imports and fills in the defaults.
- **Templates and linked clones.** Deploy a copy-on-write clone in under a
  second, and flatten it into a standalone disk later.
- **Modes.** A passthrough machine is really two machines - one with the graphics
  card handed over, one with a console to watch it boot. Save each as a named
  mode and switch between them safely.
- **Tuning that knows your host.** CPU pinning against the real core and thread
  layout read from libvirt, with the emulator parked out of the way.
- **Incremental backups** on libvirt checkpoints, copying only changed
  blocks - and restore of a whole chain back into a bootable machine.
- **Config history.** Every definition change kept, shown as a diff, restorable.
- **A window per machine**, so you can work on several at once.
- **Themes.** Colours, corner radii and fonts in a file you can edit in the app.
- **Guest features** people normally paste into raw XML: Hyper-V
  enlightenments, KVM hiding, Looking Glass, evdev passthrough, secure boot.
- **Passthrough diagnostics** that say why a device will or will not work.

Plus a command palette, scheduled snapshots and power schedules (with a
background service so they fire while the app is closed), live disk moves
between pools, imports from VMware/VirtualBox/Hyper-V, network filters,
vGPU/mediated devices, auto-attach USB rules, TLS consoles, a topology map,
an SSH terminal, guest health warnings, and a disk reclaimer.
[The full list](FEATURES.md).

## Requirements

Linux, Python 3.11+, libvirt, and your user in the `libvirt` group. PySide6 for
the interface. [Details](INSTALL.md#what-it-needs).

## Contributing

`.venv/bin/python -m pytest -q` runs 654 tests in about 10 seconds against
libvirt's fake hypervisor - no real machines involved, no display needed.
**[DEVELOPING.md](DEVELOPING.md)** covers how the code is arranged, what the
tests are for, and the rules they enforce.

## Licence

GPL-2.0-or-later. See [LICENSE](LICENSE). The bundled fonts and operating-system
logos have their own terms, listed in [ATTRIBUTION.md](ATTRIBUTION.md).

## Found a problem?

Please open an issue on GitHub. It helps a lot if you say what you did, what
happened instead, and include the relevant part of
`~/.cache/vmmanager/vmmanager.log` - starting the app with `--debug` makes that
log more useful.

Bug reports about a machine that will not start are much easier to act on with
its definition attached, which the XML tab will show you.

## A note on how this was built

VMManager was written with the help of [Claude](https://claude.ai), Anthropic's
AI assistant. Every feature was tested against real libvirt rather than taken on
trust, and the test suite is there so you do not have to take this on trust
either.
