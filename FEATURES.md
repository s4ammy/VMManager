# What VMManager can do

Grouped by where you find it in the app. If you are looking for how to install
it, see [INSTALL.md](INSTALL.md).

- [The machine list](#the-machine-list)
- [Inside a machine](#inside-a-machine)
- [Hardware and tuning](#hardware-and-tuning)
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
(Ctrl+Alt by default, Settings changes it). Under Wayland the compositor keeps a
few keys whatever a client asks for, and the hint line says so rather than
pretending otherwise.

**Display setup** tells you why a console is slower than it should be. A
VGA-class display device has no accelerated driver to install, so the guest
repaints the whole screen for every change and nothing can retarget its
resolution - which is the usual answer to "I installed the virtio drivers and
nothing improved". The check names it, and fixes it: the right display device
for the connection, the SPICE agent channel, a tablet. The guest's resolution
can then follow the window, on VNC as well as SPICE.

**Serial** - a terminal on the machine's serial console, for when networking is
broken and the graphical console tells you nothing.

**SSH** - a real ssh terminal to the machine's address, next to the console.

**Overview** - live CPU, memory, disk and network graphs. Figures are kept for
30 days, so you can scrub back through the last few minutes or the last week.

**XML** - edit the machine's definition directly, with highlighting, and it is
checked before it is saved.

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
- CD/DVD media changed or ejected.
- The **virtio-win driver disc** attached in one step, from a copy already on
  the host, from a storage pool, or downloaded. Windows cannot see a virtio disk
  or network card until the drivers on that disc are installed, and where the
  disc lives is remembered, so the next Windows machine is offered the same one
  instead of another 700 MB.
- vCPU count and memory, including live memory ballooning.
- CPU model and topology, video model, sound, per-disk cache mode, input
  devices, boot order, machine type, boot menu, MAC addresses and link state.
- USB and PCI devices handed to the guest, and folders shared with virtiofs or
  9p.
- Watchdog, USB redirection, vsock, panic notifier, smartcard, hot-plug memory.

**Tuning** covers what makes a passthrough guest feel right:

- **CPU pinning** against the host's real layout, read from libvirt, so it knows
  which logical CPUs share a physical core. Two options: pair sibling threads,
  which leaves the host whole cores, or one vCPU per core for the fastest single
  thread.
- The emulator is parked on CPUs the guest is not using.
- The guest is told which of its own CPUs share a core, because without that its
  scheduler puts two busy threads on one core.
- Hugepage backing, iothreads, and per-disk throughput and IOPS limits.

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

**PCI passthrough diagnostics** show IOMMU groups, which driver each device is
bound to, and a plain verdict on whether passthrough will work and why not.

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
  last N and pruning the rest. Runs while the app is open.
- **Backup to a folder** - definition plus disks, with one-click import back.
- **Incremental backups** built on libvirt checkpoints: the first run starts a
  chain, later runs copy only the blocks that changed.
- **Disk reclaimer** finds volumes nothing refers to any more - backing chains
  and NVRAM files accounted for - and deletes only what you tick.
- **Disk compaction** rewrites qcow2 images without the space they no longer
  need, streaming through libvirt so root-owned pools work.

## Storage and networks

**Storage pools** of every type libvirt supports: plain directories, NFS,
filesystems, LVM, disks, iSCSI, SCSI, multipath, Ceph/RBD, Gluster and ZFS.
Start, stop, autostart, capacity bars, and volume create/delete/resize. The
pool browser is what every disk and ISO picker uses, so remote hosts work
without local paths.

**Networks** - NAT, isolated, bridged and open. IPv4 and IPv6 subnets, DHCP
ranges and fixed addresses, static routes, a DNS domain with forwarders and host
entries, and portgroups with bandwidth limits. Start, stop, autostart, and see
the live DHCP leases.

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
