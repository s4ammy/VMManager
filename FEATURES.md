# What VMManager can do

Grouped by where you find it in the app. If you are looking for how to install
it, see [INSTALL.md](INSTALL.md).

- [The machine list](#the-machine-list)
- [Inside a machine](#inside-a-machine)
- [Hardware and tuning](#hardware-and-tuning)
- [Single-GPU passthrough](#single-gpu-passthrough)
- [Modes](#modes)
- [Making machines](#making-machines)
- [Templates, clones and stacks](#templates-clones-and-stacks)
- [Backups and snapshots](#backups-and-snapshots)
- [Storage and networks](#storage-and-networks)
- [Connections and remote hosts](#connections-and-remote-hosts)
- [The app itself](#the-app-itself)
- [Things it needs help for](#things-it-needs-help-for)

---

## The machine list

Every machine as a wide row, with a coloured strip and an LED for its state, a
logo for the operating system it runs, a CPU graph, its IP address, and the
button you most likely want - Start, Resume, Restore or Shut down.

- **Right-click** for the rest: reboot, pause, force off, save state, migrate,
  autostart, clone, tags, OS icon, delete, or open it in its own window.
- **Save and restore state.** Save to libvirt's own store, or to a `.vmstate`
  file you can boot later from *Import → Restore from file*.
- **Select several** with ctrl+click, then start, stop, snapshot or retag them
  together. Filter the list by tag.
- **Live thumbnails** - optional real console previews on each row.
- **Auto-attach USB** - right-click → *Auto-attach USB…*, tick a device, and
  it is handed to that machine whenever it is plugged in while the machine
  runs. A device already inside another guest is left alone.
- **Guest health** - disk usage from inside the guest, with a warning before it
  fills up. Needs the guest agent.
- **Delete** shows every disk with its size, and removes only the ones you tick.

**Operating-system logos** are worked out from what libvirt recorded about the
machine (the same `libosinfo` id virt-manager writes), then from its name. Pick
one by hand with *right-click → OS icon…*, point it at your own image file, or
turn the whole thing off in Settings. Logos come from artwork shipped with the
app, from [simple-icons](https://simpleicons.org) (downloaded once), from your
desktop's icon theme, or drawn as a last resort - so it works offline.

## Inside a machine

Eight tabs. Open a machine in a **window of its own** from the button in its
header, or by right-clicking it in the list, and work on several at once. Its
menus and dialogs open on that window, wherever you have put it.

**Console** - a real console in the app, for both VNC and SPICE. Full keyboard
and mouse, including relative-mode pointers and the guest's own cursor shapes.
Clipboard in both directions, send-key combinations, screenshots. Consoles on
remote hosts are tunnelled over SSH for you. Detach it into its own window with
F11 for fullscreen.

Clicking the display hands the **whole keyboard to the guest** - Alt+Tab, Super
and this app's own shortcuts included - until you press the release combination
(Ctrl+Alt by default, Settings changes it). On X11 that is every key without
exception.

On Wayland it is every key except Super and Alt+Tab, and the reason is Qt
rather than your compositor: the protocol for taking those exists and KWin,
Mutter and Sway all offer it, but Qt's Wayland plugin does not implement the
request, so the console never gets to ask. The hint line under the display
says which of the two you have. If you want the missing keys, Settings has
**"Capture Super and Alt+Tab as well"**, which starts the app under XWayland,
where the grab is granted - at the cost of native Wayland output for the rest
of the app, which is why it is not the default.

**Display setup** tells you why a console is slower than it should be. A
VGA-class display device has no accelerated driver to install, so the guest
repaints the whole screen for every change and nothing can retarget its
resolution - which is the usual answer to "I installed the virtio drivers and
nothing improved". The check names it, and fixes it: the right display device
for the connection, the SPICE agent channel, a tablet. The guest's resolution
can then follow the window, on VNC as well as SPICE.

**Send a USB device to the guest** from the console's ⋯ menu, for as long as
it is plugged in. Different from assigning it as hardware: it travels over the
SPICE connection, the host keeps it, and unplugging gives it straight back.

**Drop a file on the console** and it lands inside the guest through the
agent - /tmp on Unix guests, C:\Users\Public on Windows. Several files
queue up and send one after another.

**Encrypted consoles**, as an option in Settings: VeNCrypt (TLS, x509) for
VNC and TLS channels for SPICE, with a CA-certificate field and a
skip-verification switch for self-signed setups. A server that only offers
TLS is negotiated encrypted regardless of the setting.

**Serial** - a terminal on the machine's serial console, for when networking is
broken and the graphical console tells you nothing.

**SSH** - a real ssh terminal to the machine's address, next to the console.

**Overview** - live CPU, memory, disk and network graphs. Figures are kept for
30 days, so you can scrub back through the last few minutes or the last week.

**XML** - edit the machine's definition directly, with highlighting, and it is
checked before it is saved. Saving first shows a **diff of what actually
changes** - the same coloured diff the History tab draws - so a stray edit is
caught before it lands. Mode switches show the same preview. Turning
confirmations off in Settings skips it.

**Toolbox** - run a command inside the guest, fetch a file out of it, send a
file in, and read a timeline of everything that has happened to the machine.
Also *Inspect*: the operating system, hostname and installed software, read
through the agent or straight off the disks of a machine that is switched off.

**History** - every change to the definition is kept. See what changed as a
diff, and put any earlier version back.

## Hardware and tuning

Everything about the machine's hardware is editable, and where libvirt allows
it, while the machine runs. **Right-click any device in the list to take it off
the machine**; rows that are properties rather than devices - the processor,
memory, boot order - say so instead of offering it. Removal asks first, unless
you turn that off in Settings, and nothing on disk is deleted.

- Disks and network cards, added and removed live, falling back to the next
  restart when live is not possible.
- **Grow a disk** from its faceplate, and a running machine is told about it
  straight away - with the honest note that the guest still has to extend the
  partition and the filesystem inside. Growing only: shrinking throws away
  whatever was past the new end and the filesystem finds out later.
- CD/DVD media changed or ejected.
- The **virtio-win driver disc** attached in one step, from a copy already on
  the host, from a storage pool, or downloaded. Windows cannot see a virtio disk
  or network card until the drivers on that disc are installed, and where the
  disc lives is remembered, so the next Windows machine is offered the same one
  instead of another 700 MB.
- vCPU count and memory, including live memory ballooning.
- CPU model and topology, video model, sound, per-disk cache mode, input
  devices, boot order, machine type, boot menu, MAC addresses and link state.
- **Fields you can just type in.** A device's properties are on its faceplate
  as the controls themselves, not behind an Edit button: change one and a
  Save and Discard pair appears, and only what you actually changed is
  written. Discard puts back what the machine says. The processor's model,
  topology and chipset; memory; the boot order; name and notes; a disk's
  cache mode; a network card's MAC, model, link and filter; the video
  adapter; the watchdog action; a controller's model; a passed-through
  card's ROM - all of them edit in place. The Save pair sits below the
  panel rather than in it, so a change made at the top of a long faceplate
  does not put the button that saves it out of sight. What each field means
  is a **?** beside it rather than a paragraph under it - the explanation is
  there when you want it and out of the way when you do not.
- **Disks** carry a serial (what the guest reads as the drive's serial number,
  and so how udev names it under `/dev/disk/by-id`), a discard mode (`unmap`
  passes the guest's TRIM through to the host image, which is what stops a
  thin image only ever growing), and read-only and shareable flags.
- **Displays** are editable in full: SPICE or VNC, what it listens on -
  address, socket, or nothing at all, which is what a machine with its GPU
  handed over wants - the address itself, an explicit port or libvirt's
  choice, a password you can reveal, and OpenGL.
- **Boot devices are ticked on and off** and moved with arrows on the
  faceplate. The last one cannot be unticked: libvirt accepts a machine that
  boots from nothing, and it looks like a broken disk rather than a setting.
- **Shared memory** as a checkbox on the memory faceplate, which is what
  virtiofs and Looking Glass both need.
- **A network card's link** can be pulled and put back - the software
  equivalent of unplugging the cable, with the card left on the machine.
- **A passed-through card's video BIOS** is a field on the device, with the
  dump beside it: it reads the ROM from the card, trims it to the legacy
  image a guest looks for, and warns if the result names a different card.
- The **overview** names the machine itself: its UUID, hypervisor,
  architecture and emulator binary, which were previously only in the XML.
- USB and PCI devices handed to the guest, and folders shared with virtiofs or
  9p.
- Watchdog, USB redirection, vsock, panic notifier, smartcard, hot-plug memory.

**Tuning** and **Guest features** stay as dialogs rather than faceplate fields.
Each is a dozen or more controls that only make sense against each other - a
pinning matrix, a table of CPU flags - and spreading them down a faceplate
would make them harder to read, not easier.

**Tuning** covers what makes a passthrough guest feel right:

- **CPU pinning** against the host's real layout, read from libvirt, so it knows
  which logical CPUs share a physical core. Two options: pair sibling threads,
  which leaves the host whole cores, or one vCPU per core for the fastest single
  thread.
- The emulator is parked on CPUs the guest is not using.
- The guest is told which of its own CPUs share a core, because without that its
  scheduler puts two busy threads on one core.
- Hugepage backing, iothreads, and per-disk throughput and IOPS limits.
- **CPU weight and ceiling**: a weight only matters when the host is
  contended - a machine at 2048 gets twice the CPU of one at the default 1024
  and gives up nothing while the host is idle - while a ceiling is enforced
  either way. They survive a repin, which they share an XML element with.

**Guest features** are the settings people usually paste into raw XML:

- Hyper-V enlightenments, offered from what your host actually supports rather
  than a fixed list.
- Hiding the KVM signature and the VMware port, for guests that look for them.
- Per-flag CPU features, with a require/disable policy each.
- Looking Glass shared memory.
- evdev input passthrough, so a keyboard and mouse can be shared without handing
  over the whole USB device.
- Secure boot.

Where libvirt insists on a dependency, it is handled for you - turning on
`stimer` brings the `hypervclock` timer with it.

**Why won't it start?** libvirt's answer to a failed start is accurate and
rarely useful. This looks at the host instead and says what the definition can
no longer count on: a disk or ISO that moved, a UEFI variables file that went
with it, hugepages nobody reserved, pinning that names CPUs this host does not
have, a card the host driver took back. It runs itself after a failed start
and sits in the machine's menu for looking before trying.

**PCI passthrough diagnostics** show IOMMU groups, which driver each device is
bound to, and a plain verdict on whether passthrough will work and why not.
SR-IOV devices are annotated - a physical function shows how many VFs are
enabled, a virtual function names its parent.

And then **fix it**, rather than leaving the fix as an exercise:

- **Bind to vfio-pci now** - the whole card, every function of it, off the
  host driver without a reboot. Works when nothing on the host is using it.
- **Bind at boot** - a GPU the host driver claims first usually cannot be
  taken back, so this writes a modprobe.d file claiming it for vfio-pci and
  rebuilds the initramfs, which is the step everyone forgets. Reversible from
  the same dialog.
- **Give it back** to whatever normally drives it.
- The **IOMMU-off message names the exact kernel parameter** for this host -
  `amd_iommu=on` or `intel_iommu=on` - and where its bootloader keeps the
  command line.

**Video BIOS files.** Some cards, consumer NVIDIA especially, will not
initialise in a guest unless it is handed a copy of their video BIOS. The
host-device options can point at a ROM file, or **dump it from the card** and
trim it to the legacy image a guest looks for - the hex-editor step from every
passthrough guide, done by walking the ROM's own image table. A ROM that names
a different card is pointed out; one with no legacy image at all is refused
rather than handed over broken.

## Single-GPU passthrough

Handing over the *only* graphics card means the host has to let go of it
first: stop the desktop, take the virtual consoles and the boot framebuffer
off it, unload the driver - then all of that backwards when the machine stops.
That is a page of shell everybody copies from the same few gists and debugs
with the screen turned off.

*Install hardware → Single-GPU passthrough…* writes it, for this host: its
display manager (read from systemd, not guessed), its driver, its card and
every function on that card. The script is shown in full before anything is
written, and stays yours to edit afterwards.

- They are **libvirt hooks**, so they run whether or not this app is open.
- They go under `/etc/libvirt/hooks/qemu.d/<machine>/`, beside anyone else's.
  An existing `/etc/libvirt/hooks/qemu` of your own is **never overwritten** -
  it says so and leaves it alone.
- The audio function's driver is deliberately left loaded: `snd_hda_intel`
  drives the host's own sound cards too, and libvirt's managed detach unbinds
  the one device without silencing everything.
- Everything is logged to the journal - `journalctl -t vmmanager-hook` - since
  there is no screen to print to at the time.

**CPU isolation, while the machine runs.** Pinning stops the guest wandering
across cores; it does not stop the *host* scheduling its own work onto the
cores the guest is pinned to, which is what is left of the stutter. The hooks
can set systemd's `AllowedCPUs` on `system.slice`, `user.slice` and
`init.scope` to everything the guest is not using, and hand it all back
afterwards. The guest's own qemu lives in `machine.slice` and is left alone.
A pinning that would leave the host no CPUs at all is refused, since nothing
would be left to run the undo. Optionally the performance governor too, where
the host has one to switch.

**Mediated devices (vGPU)** - the types the host's driver advertises (NVIDIA
vGPU, Intel GVT-g), instances created and deleted through libvirt's
node-device API, and assigned from *Install hardware → Mediated device*.
Instances are transient across host reboots, and the dialog says so.

## Modes

A machine with a graphics card handed to it is really two machines: one with the
card, its audio function and a USB controller passed through and no console at
all, and one with a plain VGA device and a console so you can watch it boot
without losing your desktop.

A **mode** is the machine's whole definition saved under a name. Save each
setup once, then switch between them.

- The machine has to be off. A mode only takes effect on the next start, so
  switching a running machine would mislead you.
- The saved definition is schema-checked before it is used, and a mode saved
  from a different machine is refused.
- What was there before is kept as *before last switch*.
- The button beside Start names the current mode and switches to another. The
  machine list shows it too, as `SHUTOFF · DEBUG`.
- A mode is the whole definition, not a set of edits, so switching back reverts
  anything you changed since that mode was saved. Save it again to keep a
  change.

A mode can also name a **marker** file to write its own name into, for something
outside libvirt that needs to know which mode is in use - usually a libvirt hook
deciding whether to release the graphics card. Markers normally belong to root:

- Before switching, you are told if the marker cannot be written, and what it
  will go on saying.
- After switching, you are offered the chance to write it through `pkexec`.
- The script named in *Settings → Modes* is checked for any mention of the
  marker, so one that nothing reads gets pointed out instead of quietly having
  no effect.

The marker path belongs to the mode and the script is a setting. Neither is
hardcoded.

## Making machines

**New machine** installs from an ISO, from a network install tree by URL,
imports a disk image you already have, or starts empty.

**Unattended Windows installs**, the counterpart to cloud-init. Tick it in
the wizard, give a user, a password and the edition, and Setup is answered in
advance: partitioning, locale, the account, and Windows 11's demand for a
Microsoft account. It also points Setup at the virtio storage driver, which is
what otherwise leaves you at "where do you want to install Windows?" with an
empty disk list - the driver folder is offered under every letter the disc
might get, since Setup ignores the ones that are not there.

- Sensible defaults per operating system from libosinfo (952 variants), detected
  from the ISO you picked.
- UEFI or BIOS, TPM 2.0, and a pool picker that works on remote hosts.
- **cloud-init**: user, password, SSH key and hostname baked into a seed the
  machine reads on first boot.
- New machines get virtio throughout, a q35 chipset, a guest-agent channel, and
  spare PCIe root ports so hotplug works later without a fight.

**Cloud image catalogue** - pick Debian, Ubuntu, AlmaLinux, Rocky or Arch in the
wizard. It downloads once, checks the checksum, imports into a pool and fills in
the defaults. No browser, no manual conversion.

**Machines from other hypervisors** - the import path also takes VMware,
VirtualBox and Hyper-V disks (vmdk, vdi, vhdx/vhd) and whole OVA/OVF
appliances. The disk is converted to qcow2 into the target pool on create;
an appliance's descriptor fills in the name, CPU count and memory. Only the
first disk of a multi-disk appliance is imported, and the wizard says so.

## Templates, clones and stacks

- Mark a machine as a **template**.
- Deploy **linked clones** from it in under a second - they share the template's
  disk copy-on-write instead of copying it.
- **Flatten** a clone later to pull the shared image into its own disk, while it
  runs, after which the template can be deleted.
- **Stacks** are groups of clones, optionally on a network of their own, brought
  up and torn down together.

## Backups and snapshots

- **Snapshots**, internal or external. External ones work with UEFI and on
  running machines, and can include memory state. Shown as a parent/child tree.
- **Scheduled snapshots** per machine - hourly, daily or weekly, keeping the
  last N and pruning the rest. They run while the app is open, or all the
  time with the **scheduler service**: `vmmanager --daemon`, with a systemd
  user unit shipped in `packaging/`. The app notices the service and stands
  its own timers down, so nothing fires twice.
- **Backup to a folder** - definition plus disks, with one-click import back.
- **Incremental backups** built on libvirt checkpoints: the first run starts a
  chain, later runs copy only the blocks that changed.
- **Restore a backup chain**: point it at any backup folder and the full run
  plus every incremental before it are reassembled into a bootable machine -
  *Import → Restore incremental backup*, or the Backups tab. The original
  folders are never written to, and a machine that still exists comes back as
  `name-restored` with fresh MACs so the two can run side by side.
- **Disk reclaimer** finds volumes nothing refers to any more - backing chains
  and NVRAM files accounted for - and deletes only what you tick.
- **Disk compaction** rewrites qcow2 images without the space they no longer
  need, streaming through libvirt so root-owned pools work.

## Storage and networks

**Move a disk to another pool** from its faceplate on the Hardware tab. A
running machine's disk is mirrored onto the new volume with blockCopy and
pivoted over - no downtime; a stopped one is cloned through the storage API.
The old volume is deleted only if asked, and never while another machine
still refers to it.

**Storage pools** of every type libvirt supports: plain directories, NFS,
filesystems, LVM, disks, iSCSI, SCSI, multipath, Ceph/RBD, Gluster and ZFS.
Start, stop, autostart, capacity bars, and volume create/delete/resize. The
pool browser is what every disk and ISO picker uses, so remote hosts work
without local paths.

**Networks** - NAT, isolated, bridged and open. IPv4 and IPv6 subnets, DHCP
ranges and fixed addresses, static routes, a DNS domain with forwarders and host
entries, and portgroups with bandwidth limits. Start, stop, autostart, and see
the live DHCP leases.

**Network filters** - libvirt's per-NIC firewall rules (`clean-traffic`,
anti-spoofing, and your own), managed from *Networks → Filters…* and assigned
per interface in the NIC editor, with the optional IP parameter that pins a
guest to one address.

**Topology map** - networks and machines as a live graph you can click through.

## Connections and remote hosts

QEMU system *and* user session, plus Xen, LXC, bhyve and Virtuozzo. Local, or
remote over ssh, tcp or tls, set up by a guided dialog that tests the connection
before saving it.

**Live migration** between connections, with tunnelled transport, temporary
moves, bandwidth caps and downtime limits.

## The app itself

- **Themes** - the colours, corner radii and fonts live in a file, not in the
  code. Duplicate the one that ships with the app and edit the copy on the
  Themes page, or write one by hand into `~/.config/vmmanager/themes/`. Edits
  show up in the running window as you make them. You name thirteen colours; the
  hover and pressed shades, and whether text on a coloured button should be
  light or dark, are worked out from those - so a green accent does not leave you
  with a purple hover.
- **Command palette** - `Ctrl+K`, type a few letters, Enter. Start, stop or
  open any machine, or jump to any page.
- **Tray icon and notifications** for crashes and full disks, and an option to
  close to the tray rather than quit.
- **Power schedules** - start and stop machines at set times.
- **Auto-restart on crash**, enforced by libvirt itself, so it works when the
  app is not running.
- **Settings** - connections, how often usage is sampled, confirmation prompts,
  the ISO directory, and the hook script mode markers are checked against.
  Machine state arrives on libvirt events rather than by polling, so the sample
  interval only affects graph detail and how much work the host does.

Shortcuts: `F5` refresh · `Ctrl+N` new machine · `Ctrl+K` palette · `Esc` back.

Also here: paste-as-keystrokes, per-disk delete, `?keyfile=` in SSH URIs,
clipboard sharing both ways, guest auto-resize.

## Things it needs help for

All optional, and all degrade quietly if missing:

| For | Install |
|---|---|
| SPICE consoles | `python-gobject` and spice-glib |
| Installing from ISO or URL, cloning | `virt-install`, `virt-clone` |
| Compacting and converting disks | `qemu-img` |
| cloud-init seed images | `xorrisofs` |
| Inspecting a machine that is switched off | `libguestfs` |
| TPM 2.0 | `swtpm` |
