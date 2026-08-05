# VmManager - Plan

A native desktop app (PySide6/Qt) for managing libvirt/QEMU virtual machines.
Dark UI accented with `#babaff`, live usage graphs, and a toolbox of
day-to-day VM utilities. Think "virt-manager, but pretty and dashboard-first."

## Environment (verified)

- libvirt **12.6.0**, QEMU **11.0.3**, `virsh` present
- `libvirt-python` installed; user is in the `libvirt` group → `qemu:///system` without root
- Python 3.14 venv at `.venv` (system-site-packages) with **PySide6**

## Stack

| Layer | Choice | Why |
|---|---|---|
| App | Python + **PySide6 (Qt 6)** | Native window, no browser; reuses `libvirt-python` in-process |
| Libvirt access | `libvirt-python` (primary), `virsh` subprocess for odd ops | Native API gives events + stats without parsing text |
| Styling | QSS stylesheet + custom-painted widgets (`theme.py`) | Full control over the dark `#babaff` look |
| Charts | Custom `QPainter` sparklines (or PyQtGraph if needed) | Live CPU/RAM/disk/net graphs |
| Live data | Poll worker thread (`QThread`) emitting snapshots every 2s | UI never blocks on libvirt |
| Fonts | Bundled TTFs: Chakra Petch (display), IBM Plex Sans (body), IBM Plex Mono (data) | Loaded via `QFontDatabase`, no system deps |
| Console | Embed `remote-viewer`/virt-viewer, or a VNC client widget (decide at that milestone) | In-app graphical console |

Layout:

```
VmManager/
├── vmmanager/
│   ├── __main__.py        # entry point (+ --screenshot dev flag)
│   ├── theme.py           # design tokens, fonts, QSS
│   ├── libvirt_service.py # poll worker thread + lifecycle actions
│   ├── main_window.py     # shell: sidebar + machines page
│   ├── widgets.py         # VmCard, Led, Rail, Sidebar, HostPanel
│   └── assets/            # fonts, icon.svg
├── .venv/
└── PLAN.md
```

Run: `.venv/bin/python -m vmmanager`

## Design language

Dark, calm, `#babaff` (soft periwinkle) as the single accent. Machines are
presented as **rack bays**: full-width rows with a state-colored rail on the
left edge, a glowing LED (pulses while running), Chakra Petch machine name,
and monospace readouts - a hardware-faceplate aesthetic.

Corners are deliberately shallow - the shipped theme sets all three of
`RADIUS_SM`, `RADIUS` and `RADIUS_LG` to 2. Heavily rounded panels read as soft
and this is a tool for machines. Everything, including corners painted in code,
goes through those three so the scale cannot drift; a test enforces it, and caps
the largest at 8 whatever a theme asks for.

Group headings in the hardware bay are banded rather than merely dimmer text -
they sat in the same visual plane as the rows they were meant to separate.

Tokens live in `assets/themes/vmmanager.toml`, not in the code, and the
Themes page edits copies of it: bg `#1e1e1e`, raised `#141414`, inset `#0a0a0a`,
borders `#2f2f2f`/`#4a4a4a`, text `#e8e8f2`/`#ababab`/`#727272`,
accent `#babaff`, ok `#8fdcb0`, warn `#e8c47f`, danger `#e89090`. Surfaces are
neutral grey; the accent and the state colours carry all the hue.

Hover and pressed shades are not tokens - they are worked out from the ones
above, so any accent stays coherent.

Accent is reserved for: brand mark, active nav, primary buttons, focus.
State colors carry machine state (rail, LED, state label). Everything else
stays neutral.

## Features

### Machines page (done in M1)
- Rack-bay list of all domains, live state via 2s poll.
- Contextual power controls: Start / Resume / Shut down.
- Host readout panel in the sidebar (node, hypervisor, CPU, RAM, VMs up).
- Error banner with auto-reconnect when libvirtd is unreachable.

### VM detail view (tabs)
- **Overview** - live charts: CPU %, memory, disk I/O, network I/O.
- **Console** - graphical console (see stack note), Ctrl+Alt+Del button.
- **Hardware** - vCPUs, memory ballooning, disks, NICs, boot order.
- **Snapshots** - tree view, create/revert/delete.
- **XML** - read/edit domain XML with validation before redefine.

### Lifecycle
Force off, reboot, pause, managedsave, clone, rename, delete (with optional
storage), autostart toggle, beyond the M1 basics. Destructive ops get a
confirm dialog.

### VM creation wizard
Name → ISO/disk → CPU/RAM → disk size → network → review, with virtio +
q35 + UEFI defaults.

### Storage & networks pages
Pools with capacity bars, volume browser, qcow2 resize; virtual networks
with DHCP leases (surface each VM's IP on its card).

### Toolbox
Guest-agent info (IPs, hostname, OS), display screenshots, send keys,
stats history in SQLite for scrub-back charts, "open in virt-manager"
handoff, live QEMU command-line peek.

## Milestones - all shipped

1. **Skeleton** - themed shell, sidebar, host panel, rack-bay machine list.
2. **Live stats** - per-VM CPU sparklines on cards, host usage bars, 4-minute
   history ring buffer feeding the overview charts.
3. **VM detail** - CPU/mem/disk/net charts, hardware inventory, XML editor
   with save-to-define.
4. **Lifecycle** - start/resume/shutdown buttons; right-click menu with
   reboot, pause, force off (confirmed), autostart, clone, delete
   (with optional storage removal).
5. **Console** - live read-only preview via libvirt screenshot streaming
   (~1.5s), one-click virt-viewer for interactive use, save-screenshot.
6. **Create** - new-machine dialog: ISO picker, virtio/q35/UEFI defaults,
   volume allocated in the default pool, network selection.
7. **Storage + networks** - pool capacity bars, volume create/delete;
   network start/stop/autostart with live DHCP leases.
8. **Snapshots + toolbox + polish** - snapshot take/revert/delete;
   guest-agent query, QEMU command-line peek, virt-manager handoff;
   `.desktop` launcher, F5/Ctrl+N/Esc shortcuts, empty states.

## Milestone 9 - full virt-manager replacement (shipped)

- **Interactive embedded console**: pure-Qt VNC client (`vnc.py`) speaking
  RFB 3.8 - Raw/CopyRect/Zlib encodings, DesktopSize, guest→host clipboard,
  DES password auth (verified against openssl), full keyboard/mouse over
  QTcpSocket/QLocalSocket. No spice-gtk, no embedding hacks, Wayland-proof.
- **Serial console tab**: libvirt console stream on a dedicated thread +
  pyte-backed terminal widget (`serialterm.py`).
- **Hardware live edits**: disk/NIC attach-detach (live w/ config fallback),
  cdrom media change/eject, vCPU + memory (live balloon), boot order
  (os-level and per-device), USB/PCI passthrough, virtiofs/9p shares with
  automatic shared-memory backing. Hardware tab reads the persistent config
  so pending next-boot changes are visible.
- **Wizard**: ISO / import-image / empty sources, libosinfo defaults + ISO
  detection, pool picker, UEFI/BIOS, TPM (swtpm), cloud-init NoCloud seed
  (xorrisofs → uploaded into the pool, attached as **virtio disk** - minimal
  cloud kernels lack AHCI and never see a SATA cdrom). New VMs get 12 PCIe
  root ports so hotplug has spare slots, plus a guest-agent channel.
- **Storage**: pool create (dir/netfs)/delete/start/stop/autostart, volume
  resize (shrink confirmed separately).
- **Networks**: create/edit/delete NAT / isolated / bridged with DHCP ranges.
- **Connections & migration**: multiple URIs in Settings, poll worker swaps
  connections on the fly, live/offline migrate via `migrateToURI3` P2P.
- **Managed save**: save/restore/discard + save-to-file / restore-from-file.
- **Preferences**: poll interval, confirmation toggle, connections, ISO dir.
- **Stats history**: every poll tick lands in SQLite
  (`~/.local/share/vmmanager/stats.db`, 30-day retention); overview charts
  scrub back through 30 min - 7 d ranges with a pan slider.

Tested end-to-end against a disposable `vmm-test` Debian 12 cloud image VM
(cloud-init login over the serial console, typed into the VNC framebuffer,
hotplug/detach, managedsave cycle, snapshots, media change). The user's
`win11` machine is never touched by tests.

## Milestone 10 - beyond virt-manager (shipped)

- **SPICE embedded console** (`spice.py`): toolkit-independent spice-glib via
  GObject introspection - GLib context pumped from a QTimer, primary surface
  read via ctypes, damage-rect blits, XT-scancode keyboard (evdev-derived),
  absolute mouse, vdagent clipboard both directions. Verified by typing into
  a live guest through the SPICE inputs channel.
- **SSH-tunnelled remote consoles** (`tunnel.py`): consoles on qemu+ssh://
  hosts forward a free local port via `ssh -N -L` (BatchMode, readiness
  probing, `?keyfile=` honored). Verified end-to-end against
  qemu+ssh://admin@localhost with a temporary self-ssh key.
- **Remote-aware pickers**: every ISO/image/disk chooser can browse
  pools/volumes on the active connection; local file browsing hides on
  remote connections.
- **Clipboard host→guest**: VNC ClientCutText + paste-as-keystrokes; SPICE
  via vdagent. Guest→host already worked.
- **External snapshots + tree**: external disk(+memory) snapshots work on
  running UEFI machines (create/revert/delete all verified on libvirt 12.6);
  snapshot list is now a parent/child tree with a type column. The dialog
  preselects external where internal would fail.
- **Deeper hardware editing**: CPU model (host-passthrough/host-model/
  custom) + topology, video model, sound devices add/remove, per-disk cache
  mode.
- **Pool types**: dir, netfs, fs, logical (LVM), iscsi, zfs - type-specific
  dialog fields; XML validated against libvirt.
- **Guest-agent actions**: ping, fs-freeze/thaw, clock sync, agent
  shutdown/reboot (verified live); wizard cloud-init can preinstall
  qemu-guest-agent + spice-vdagent.
- **Per-disk delete dialog**: every file-backed disk listed with size, each
  individually checkable.

## Milestone 11 - console polish (shipped)

- **Mouse, both modes, both protocols.** SPICE now tracks the session's
  mouse mode: absolute `position()` in client mode, relative `motion()`
  deltas in server mode, with `request_mouse_mode(client)` on connect.
  Verified end-to-end: pointer events from both the VNC and SPICE clients
  counted on a guest virtio-tablet event node. Root cause of "no mouse"
  documented: guests with only a PS/2 mouse (e.g. a virt-manager win11) sit
  in server mode, which the old client ignored, and in client mode the
  guest cursor only exists on the cursor channel, which was not rendered.
- **SPICE cursor channel**: guest cursor shapes become a real QCursor
  (hotspot honored), so the pointer looks native instead of invisible.
- **Hardware → Input menu**: add/remove USB or virtio tablets and keyboards
  (hotplug verified). The console hints "relative mouse, add a tablet"
  when a SPICE session lands in server mode.
- **Guest auto-resize**: on window resize the SPICE client sends a monitor
  config through the agent (debounced), like virt-manager's auto-resize.
- **XML syntax highlighting** (`syntax.py`): element names in accent,
  attributes/values/comments in theme colors, in the domain XML editor.

## Milestone 12 - beyond virt-manager, part 2 (shipped)

- **Cloud image catalog** (`catalog.py`): 7 curated distros with stable
  "latest" URLs, QThread downloader with progress + sha256/sha512 verify
  (both `hash  file` and BSD `SHA256 (file) =` formats), cache in
  `~/.cache/vmmanager/images`, streamed into a pool via
  `svc_upload_volume_from_file` (no RAM spike). Wizard "Catalog..." button
  pre-selects the OS variant and enables cloud-init. Tested with a real
  326 MB Debian 13 download → import → boot.
- **Templates + linked clones**: template flag in domain metadata
  (`http://vmmanager/xmlns/1.0`), TEMPLATE badge, start hidden; linked
  clones create qcow2 overlay volumes via libvirt `<backingStore>` (0.06 s,
  0.2 MB allocation verified), with fresh name/uuid/MACs/nvram. Deploy from
  the context menu.
- **Guest file send**: `guest-file-open/write/close` in 48 KB base64 chunks;
  Toolbox UI. Verified by md5 inside the guest.
- **Scheduled snapshots**: `snap_schedules` table, 60 s scheduler in the
  main window, `auto-<timestamp>` names, prune-beyond-keep verified.
- **SSH tab**: `sshterm.py` forks ssh under a pty (QSocketNotifier, WINSZ
  sync with the terminal grid). Password login + command round-trip
  verified against a real guest.
- **Command palette**: Ctrl+K, subsequence fuzzy match ("con w" →
  "console · win11"), machines/actions/pages.
- **Config history**: poller hashes the persistent XML every ~10 s and
  emits changes; stored in SQLite (50 versions/domain); History tab shows
  unified diffs (DiffHighlighter) vs current and restores any version.
  Record → diff → restore verified.

## Milestone 13 - fleet features (shipped)

Fourteen features in one push, all live-tested against disposable VMs:

- **Live card thumbnails** (toggle in Settings, 5 s refresh via the
  screenshot API) and **guest disk health chips** (agent fsinfo, warn ≥85%).
- **Selection + bulk actions** (ctrl+click; start/stop/force-off/snapshot/
  retag) and **tags** stored in domain metadata with a filter combo.
- **Detachable console** window with F11 fullscreen (widget reparenting;
  the connection survives tab changes while detached).
- **Stacks** (`pages/stacks.py`): saved stack definitions deploy N linked
  clones on `default` or a per-stack isolated network; verified 2-clone
  deploy in 0.5 s and full teardown including the network.
- **Command runner / fetch-file** via guest-exec + guest-file-read.
- **Per-VM timeline**: state transitions recorded on every poll delta,
  merged with snapshot creation times and config-history versions.
- **Disk reclaimer**: backing chains and NVRAM count as referenced; found a
  real orphan on first run.
- **Topology map** (`topology.py`): painted graph, running edges green,
  click a machine to open it.
- **Export/import**: streamed disk download/upload + manifest; verified
  export → delete → import → boot.
- **Tray + notifications** (crash, guest-disk-full), close-to-tray setting.
- **Auto-restart on crash** via libvirt's own `<on_crash>` (no app needed).
- **Power schedules**: start/stop at HH:MM with day filters; fired a real
  shutdown during testing, visible in the timeline.

## Milestone 14 - hardware editor redesign (shipped)

The hardware tab is now a **component bay**: a grouped master list on the
left (SYSTEM / STORAGE / NETWORK / DISPLAY / PERIPHERALS / SHARED FOLDERS)
where every component carries a PCB-silkscreen badge (CPU, MEM, BOOT, DSK,
ODD, NIC, GPU, DSP, SND, INP, USB, PCI, FS), and a faceplate panel on the
right showing the selected component's specs in mono key/value rows with
only *its* actions. The seven scattered "Add X..." buttons collapsed into one
"+ Install hardware ▾" menu (disk, NIC, share, host device, sound, input,
display). Selection is preserved across the 2 s hardware reloads. The
Overview gained a spec-chip row (machine · firmware · vcpu · memory ·
video · boot) in the host-panel readout style.

Follow-up polish: the merged CPU-and-memory dialog split into a dedicated
**Processor** editor (model + sockets/cores/threads with a live vCPU total)
and a **Memory** editor (current/maximum) reached from their own bay rows.
Every faceplate has a **DETAILS | XML** switcher: XML mode shows the
component's own element(s) from the persistent config, syntax-highlighted
and editable, applied via `svc_set_device_xml` (element replacement +
redefine, with a guard that rejects elements that don't belong to the
selected item). System pseudo-items work too - cpu edits `<vcpu>`/`<cpu>`,
memory edits `<memory>`/`<currentMemory>`/`<memoryBacking>`, boot edits
`<os>`. Round-trips verified live (disk cache attr via XML, max-memory via
XML, wrong-element guard).

## Milestone 15 - console pointer tracking (shipped)

Two separate defects, both now covered by a measurement-based test:

- **Relative ("server") mouse mode had no pointer capture.** Guests with only
  a PS/2 mouse (a virt-manager-created Windows VM, for instance) put SPICE in
  relative mode, where the guest applies its own acceleration to our deltas.
  Sending raw widget-space deltas let the two cursors drift apart until the
  real pointer left the window and control was lost. Now the pointer is
  captured like every other viewer does it: hidden, warped back to the widget
  centre each event, deltas taken from the centre offset and **scaled into
  guest pixels**. Ctrl+Alt releases (tracked via held keys, not
  `event.modifiers()`, whose bits for the key being pressed are unreliable);
  releasing also un-sticks any modifier we sent down. Focus loss and
  disconnect release too.
- **`display_get_primary()` must not be used.** It is deprecated *and* its
  struct marshals wrongly through PyGObject: `width` yields the height,
  `height` yields the stride, `format` is garbage. Guest size comes from the
  `display-primary-create` signal, which is authoritative.
- The automatic guest-resolution push on window resize is gone; retargeting
  the guest's mode from under the user desynced the mapping. Scaling to fit
  is the behaviour; `request_guest_resolution()` remains for an opt-in toggle.

**How it's verified:** a probe inside the guest decodes the tablet's evdev
ABS_X/ABS_Y and converts them back to guest pixels, so pointer *position* is
measured rather than assumed (the earlier test only counted bytes arriving).
With a mismatched aspect ratio (a 1280×800 guest letterboxed into a 900×500
and then a 1000×420 widget) the pointer lands pixel-exact on both VNC and
SPICE. Relative mode is covered by seven checks: capture on click, delta
scaling, warp-echo suppression, Ctrl+Alt release with no stuck modifiers,
absolute mode never capturing, and release on focus-loss and disconnect.

## Milestone 16 - advanced features + project restructure (shipped)

Four features, then the code was reorganised.

- **Windows guest tooling**: a checklist dialog (agent responding, agent and
  SPICE channels, tablet, virtio disk/net, disc attached) with one-click fixes
  and the virtio-win ISO downloaded, imported into a pool and attached as an
  extra drive. The catalog downloader now tolerates upstreams that publish no
  checksum, virtio-win is one, and says so instead of pretending otherwise.
- **Incremental backups** on libvirt checkpoints: `backupBegin` in push mode
  with a fresh checkpoint each run, parented to the previous one when
  incremental. Verified live: a full run wrote 954 MB, and the incremental that
  followed wrote ~0 MB, with a correct parent chain and a guard that refuses
  an incremental with no base.
- **PCI passthrough diagnostics**: IOMMU groups and bound drivers read from
  sysfs, with a verdict per device (ready / caution / blocked) and prose
  explaining why. Correctly blocks this host's RTX 4070 Ti because its HDMI
  audio function shares group 13 with `snd_hda_intel`.
- **Disk compaction**: `qemu-img convert` to drop clusters an image no longer
  needs. Two routes - direct when the file is ours to write, otherwise
  streamed out and back through libvirt, as system pools require
  (`/var/lib/libvirt/images` is root-owned, mode 0600). The original is kept
  until the replacement lands. Estimates are an honest floor: qemu-img cannot
  tell that an allocated cluster holds only zeros, so the real saving is often
  larger and gets reported after the rewrite. Verified: a 600 MB image with
  reclaimable clusters came back at ~0 MB with its virtual size intact.

### Structure

`libvirt_service.py` (3,489 lines) and `pages/detail.py` (2,440) were the two
problem files. Now:

- `core/` - the service layer split by concern, with a computed, acyclic
  import graph (models → connection → xmlutil → devices → ... → poller).
  `libvirt_service.py` remains as a façade re-exporting all 88 `svc_*`
  functions so existing imports keep working.
- `pages/detail/` - one module per tab, composed as mixins by `page.py`;
  `common.py` holds the shared imports and small widgets.
- `dialogs/`, `widgets/`, `console/`, `data/` - grouped by what they act on.

Largest file is now 810 lines. The split was done mechanically (AST analysis
for ownership and dependencies) then verified by walking every page, every
detail tab, every hardware component in both view modes, the palette, and by
instantiating all 27 dialogs, which catches the lazily-imported paths a
boot test would miss.

## Milestone 17 - tab strip and control spacing (shipped)

- **The tab strip no longer scrolls.** Eleven tabs overflowed the width, so Qt
  drew its own unstyled scroll arrows, which overlapped the last label
  ("TIMELIN◀▶") and looked nothing like the rest of the app. Rather than only
  styling the arrows, the count came down to eight: the three ways of reaching
  a machine (graphical, serial, SSH) share one **CONSOLE** tab, and the two
  records of what happened (timeline, config versions) share **HISTORY**, each
  behind the same small segmented switcher the hardware faceplate uses. The
  strip is now 552 px wide and fits even at the 880 px minimum window, so the
  arrows can't appear; they are styled anyway as a fallback, and the pane's
  top border gives the strip a hairline to sit on.
  Only the visible console view stays connected - switching segments or tabs
  tears down the others.
- **Buttons had no gap between them.** Every page header built its row with a
  bare `QHBoxLayout()` nested inside a `content` layout that sets
  `setSpacing(0)` - and a Qt layout with no explicit spacing *inherits* its
  parent's, so "Import ▾ + New machine", "Reclaim space ▾ + New pool
  + New volume" and "Map + New network" ran together. The header rows now set
  spacing explicitly.
- **The console action row** dropped from seven controls to four (Send key,
  Paste, Detach, ⋯) because at narrow widths it was squeezing its own labels
  into "econnec" and "irt-viewe". Reconnect, screenshot and virt-viewer moved
  into the ⋯ menu, and the status hint is now allowed to clip instead of
  shoving buttons off the edge.

## Milestone 18 - closing the gap with virt-manager (shipped)

Read virt-manager's source (68k lines) and worked through everything it had
that we didn't. Ten areas, all live-tested:

- **Install paths**: network URL install (a distro install tree, driven through
  `virt-install`, which already knows how to fetch a kernel and initrd and
  match kernel arguments to the distro) and direct kernel boot
  (`<kernel>/<initrd>/<cmdline>`). Verified by asserting the generated argv.
- **Device breadth**: watchdog, USB redirection, vsock, panic notifier,
  smartcard, audio backend, hot-pluggable memory DIMM (which also adds
  `<maxMemory>` and the NUMA cell they require). Several of these taught us
  real constraints, now handled rather than leaked as QEMU errors:
  q35 already ships an itco watchdog, so adding one retargets it; redirdev,
  spicevmc smartcard and SPICE audio all need a SPICE display and say so;
  `<panic>` cannot be attached through the device API and goes via
  `defineXML`. A machine with all of them attached boots.
- **Config fields**: title and description, machine type (offered from the
  hypervisor's own capabilities), boot menu, NIC MAC/model/link state (link
  alone applies live), video 3D acceleration, controller model, PCI ROM BAR
  and USB startup policy.
- **Guest inspection** (`core/inspect.py`): two backends, because neither
  covers every case. A running guest answers through its agent - verified
  listing 323 installed packages with versions. A shut-off one needs
  libguestfs, which is an optional dependency; when it is missing the UI says
  exactly which package to install instead of failing obscurely.
- **Connection breadth**: a guided dialog building URIs for QEMU system and
  **user session**, Xen, LXC, bhyve and Virtuozzo, local or remote over
  ssh/tcp/tls, with a "Test connection" probe. Verified against
  `qemu:///session`.
- **Pool types**: all twelve libvirt supports - dir, netfs, fs, logical, disk,
  iscsi, iscsi-direct, scsi, mpath, rbd (with cephx auth), gluster, zfs. One
  table drives both the XML and the dialog's fields, so they cannot drift.
  Every type generates schema-valid XML; missing inputs are refused with a
  sentence rather than an XML error.
- **Network depth**: IPv6 subnets, DHCPv6, static leases, static routes, DNS
  domain/forwarders/host entries, portgroups with bandwidth, and binding NAT
  to one host interface - created, read back and edited. libvirt silently
  discards an empty `<dhcp/>`, so DHCPv6 emits a real range.
- **Console and preferences**: scaling policy (always/never/fullscreen),
  configurable pointer-release combination, console autoconnect, per-statistic
  polling toggles, and defaults for storage format, firmware, display and CPU
  model. **Guest auto-resize is back as an opt-in setting**, which is the right
  answer
  to what was removed when fixing pointer tracking.
- **Migration options**: tunnelled transport, allow-unsafe, temporary
  migration (this host keeps the definition), destination listen address and
  port, bandwidth cap and max downtime.
- **Clone dialog**: a decision per disk, copy to a path, share the original,
  or skip - instead of `--auto-clone`. Note virt-clone may still share a
  read-only disk rather than copying it.

The hardware bay grew badges for every new class (WDT, URD, VSK, PNC, SCD,
AUD, DIM, CTL) and collapses the dozen spare PCIe root ports into one row.

## Operating-system icons on the machine list

Each card shows the logo of what the machine runs. Detection order: a pinned
override, then the `<libosinfo:os id=.../>` metadata in the domain XML, then
the guest agent, then the machine name. virt-manager writes that same metadata, so
existing machines are already identified - `win11` here resolves from
`http://microsoft.com/win/11`. Name matching takes the longest token first, so
`linuxmint` beats `linux` and `win11` reaches Windows. No answer means no icon
rather than a wrong one.

Icons come from three sources, tried in order, in `data/oslogos.py`:

1. **simple-icons** (CC0). Single-path SVGs fetched once into
   `~/.cache/vmmanager/oslogos` and tinted to the brand colour, so twenty
   machines read as one set rather than twenty clashing logos. Covers 25 keys.
2. **The host icon theme**: `distributor-logo-*.svg`, full colour, for distros
   simple-icons doesn't carry. 7 keys resolve this way here.
3. **Glyphs we paint**, for Windows (no permissive set has Microsoft's marks),
   CachyOS, and the family fallbacks.

Downloads happen once, in a background thread, only when the feature is on, and
fail quietly - sources 2 and 3 need no network, so the list never waits on a
CDN. The override lives in our `<vmmanager>` metadata next to tags and the
template flag; clearing it restores detection. Right-click → *OS icon...* to pin
one, or turn the feature off in Settings.

## Foundations

The features had outrun the engineering under them, so this pass went back and
filled that in.

- **Version control.** ~18k lines had never been committed.
- **Tests.** 118, against libvirt's `test:///` driver plus pure-logic and
  stylesheet checks. Most guard a mistake we already made.
- **XML escaping.** User text was spliced into XML with f-strings at 60 sites,
  and a tag of "R&D" broke it. See `core/xmlesc.py`.
- **libvirt's round trip is lossy.** It accepts escaped input, keeps the raw
  text, then writes some attributes back out unescaped. Checked on the qemu
  driver, not just `test:///`. So a DNS domain containing `&` returns as XML
  libvirt would itself reject, which would break the networks page. We can't
  fix the round trip, so those characters are refused up front, and readers
  skip an object they can't parse instead of failing the page.
- **Events instead of polling.** Round trips per tick went from ~8 + 5.2 per
  machine to a flat 4: 34x fewer at 25 machines, and no longer growing with the
  machine count, which is what capped remote hosts at around 20. State changes
  show up in 3-7 ms.
- **Logging and crash reporting.** An exception in a Qt slot used to leave a
  window that looked hung. It now logs and says so.
- **Packaging.** `pyproject.toml` with a real dependency list, a `vmmanager`
  entry point, a relocatable desktop file, and a PKGBUILD checked against the
  Arch repos. Verified by building the wheel, installing it elsewhere, and
  running it from outside the source tree with fonts and icons intact.

## Templates page

Base images and their descendants. Each card shows the OS, the shared base
size, and the clones by name with their state and their own allocation. Deploy
takes a name and a count, numbering them `web-01`, `web-02`. Marking works from
here as well as the machines list, and unmarking warns when machines still
depend on the disk.

The template/clone link needs no bookkeeping of ours. A linked clone is a qcow2
overlay whose backing file is the template's image, and libvirt records that in
the *volume* XML. Not the domain XML, which carries no backing chain for a
shut-off machine, so reading a domain's own description tells you nothing.
`svc_backing_index()` reads every volume once and returns what each is layered
on plus capacity and allocation; the page joins that against the disk paths the
poller already collects. It runs when the set of machines changes rather than on
the poll tick.

Chains work: a template can itself be a clone of another template, and each
level reports only its own direct children.

## Tuning

CPU pinning, hugepage backing, iothreads and per-disk IO limits, in one dialog
off the hardware bay. The pinning picker reads the host's real topology from
libvirt's capabilities, so it knows cpu 0 and cpu 8 are one physical core here,
and `auto_pin` spreads vCPUs across separate cores before pairing siblings -
two vCPUs on one core contend, which is exactly the stutter people chase.

Two layouts, because the trade-off is real: pairing sibling threads uses half
as many cores and leaves the host whole ones, which is the usual passthrough
choice; one vCPU per core gives the fastest single thread but takes twice the
cores. The choice also decides what the guest can be told, since a guest
topology has to be sockets x cores x threads: a paired layout describes exactly,
while 12 vCPUs spread one-per-core over 8 cores doubles up 4 of them and cannot
be expressed at all. `guest_topology_for` returns None in that case rather than
inventing something, and the dialog says why.

Verified on a real machine: vcpus 0/1 on host core 1, 2/3 on core 2, 4/5 on 3,
6/7 on 4, with the guest told 4 cores of 2 threads. The pairing the guest sees
matches the pairing it actually has.

The topology itself has three modes: leave it alone, match the pinning, or type
it in. A hand-typed topology that contradicts the pinning is allowed, since
there are reasons to want that, but it says so rather than passing silently.

One thing found while testing it: `svc_set_cpu` sets the vCPU count to
sockets x cores x threads, so a topology whose product does not match quietly
changes how many CPUs the machine has - an 8-vCPU machine became a 12-vCPU one.
The dialog refuses that and points at the processor editor. The first version of
the message blamed libvirt for the constraint, which was wrong: libvirt accepts
it, we are the ones changing the count.

The emulator gets placed in tiers: a core the guest is not using, else the idle
sibling threads, else left unpinned, because pinning it onto a core a vCPU owns
is worse than not pinning it.

Hugepages are reported with their free counts read from sysfs, since libvirt
says how many pages exist but not how many are spare, and a guest asking for
pages that are all taken simply fails to start. This host has 2 MiB and 1 GiB
sizes available with none reserved, and the dialog says so rather than letting
you set it and wonder.

Verified against libvirt's own view: after applying, `vcpuPinInfo` reports vcpu
0-3 on cpus 1-4 and `blockIoTune` reports 209715200 read bytes/sec, matching
what was asked for.

## Flattening a linked clone

`blockPull` streams a clone's backing image into its overlay while the machine
runs, after which it owns its disk outright. Verified end to end: chain before,
flatten, chain gone, template deleted, clone still running, and `qemu-img info`
reporting no backing file.

libvirt caches volume metadata, so the first attempt still reported the old
dependency afterwards - the pool needs refreshing once the job finishes.

## Guest features

Hyper-V enlightenments, hiding, CPU flags, Looking Glass and evdev, in one
dialog off the hardware bay. The list of enlightenments comes from libvirt's
domain capabilities rather than a constant, since it grows with QEMU and
offering one this host has never heard of would only fail at define time. This
host reports 16.

The dependency rules were checked by defining each pairing and reading what
libvirt said, not taken from memory:

- `stimer` requires `synic` - confirmed, so the dialog refuses the pair.
- `stimer` requires the `hypervclock` timer - confirmed, and added automatically
  rather than failing on save.
- `synic` requires `vpindex` - **false**. libvirt accepts synic alone. An
  earlier version of this refused it, which would have blocked a valid setup.

Secure boot turned out to be available here after all: the capabilities query
only reports it for q35, and it needs SMM turning on alongside the secboot
firmware, which the writer does.

The reader was checked against the real `win11`, which had 12 enlightenments, a
vendor id, a hidden KVM signature, vmport off and a topoext flag - all set
outside this app and all now visible in it.

Editing was checked for destructiveness first: running the processor, vCPU,
memory and pinning editors over a copy of win11's configuration leaves its
enlightenments, hidden state and CPU flags untouched.

## Modes

Named whole-definition configurations, from a hand-written script that flipped
`win11` between a passthrough setup and a console setup. Saving captures the
persistent XML; switching defines it again, which libvirt does atomically.

The value is in the rails, not the saving:

- the machine must be off, since a definition change only lands on next start
- the XML is run through `virt-xml-validate` before being defined
- a mode carrying a different UUID is refused, because applying it would
  redefine this machine into that one
- the previous definition is saved as "before last switch" automatically
- the list marks which mode is active and whether the definition still matches
  it, comparing with libvirt's own additions (aliases, seclabels) stripped out

Marker files for hooks are supported but usually live under /etc, which this
process cannot write. It reports that and prints the command instead of
claiming success and leaving the hook on the old mode.

## Milestone 19 - ten features (shipped)

Backup restore, a scheduler daemon, nwfilter, SR-IOV/mdev, TLS consoles,
live disk moves, cross-hypervisor import, drag-and-drop to guest, XML diff
previews, and auto-attach USB. Verified against the fake driver plus pure
logic tests (654 total); the notes below say where real-hardware behaviour
is still taken from the specification rather than measured.

- **Incremental backup restore** (`storage.py`): the manifest chain is walked
  from any folder back to its full run, incremental layers are copied and
  rebased onto the layer below with `qemu-img rebase -u` (originals never
  written), flattened with `qemu-img convert`, streamed into a pool and
  defined. Name collisions become `-restored`, which also drops MACs so the
  copy can run beside the original. The chain walk and the rebase plan are
  pure functions with tests including loops, missing parents and chains that
  never reach a full backup.
- **Scheduler daemon** (`scheduler.py`): the snapshot and power-schedule
  decisions were extracted from the window into shared pure functions
  (`snapshots_due`, `wake_actions`), so `vmmanager --daemon` and the app
  cannot drift. The daemon beats a heartbeat file each tick; the app skips
  its own runs while the heartbeat is fresh (150 s), so nothing double-fires.
  `packaging/vmmanager-scheduler.service` is a user unit; `/usr/bin/env`
  does the PATH lookup so it works however vmmanager was installed.
- **Network filters** (`core/nwfilter.py`): list/define/delete, a filters
  dialog off the Networks page with the XML editable in place, and a
  per-NIC filterref with the IP parameter in the NIC editor. The interface
  update falls back to a full redefine on drivers that cannot update an
  interface in place - which is also what the fake driver needs, so the
  fallback is the tested path. `svc_nwfilter_names` maps VIR_ERR_NO_SUPPORT
  to an empty list, so the session driver just doesn't offer filters.
- **SR-IOV + mdev** (`core/mdev.py`): sysfs walkers take a root directory
  and are tested against a fake /sys shaped like GVT-g publishes; mdev
  create/delete goes through libvirt's node-device API (root does the sysfs
  write, not us) and instances attach as `<hostdev type='mdev'>`, a new
  kind threaded through ident/attach/detach/badges (MDV). The passthrough
  dialog annotates PFs and VFs. Not measured on real hardware: no mdev-
  capable GPU on this host.
- **TLS consoles**: the VNC client's socket became a QSslSocket (plain until
  asked), and VeNCrypt is a real state machine - version 0.2, subtype
  choice preferring x509-none, `startClientEncryption` at the handoff, then
  None or VNC auth inside the tunnel. Preference order is tested through a
  QSslSocket subclass with fake I/O, so the isinstance gate and signal
  wiring are real. SPICE sets tls-port/ca-file/secure-channels=all, and a
  TLS-only display (no plain port) is negotiated encrypted regardless of
  the setting. SSH-tunnelled consoles keep using the plain port - the
  tunnel is the encryption. The TLS handshake itself is OpenSSL's; not
  exercised against a live TLS-enabled qemu here.
- **Live disk moves** (`svc_move_disk`): running machines mirror with
  blockCopy (REUSE_EXT on a pre-created volume + TRANSIENT_JOB) and pivot;
  stopped ones clone via `createXMLFrom`. The persistent definition is
  repointed either way, and the source volume is deleted only when asked
  and never while another domain's XML mentions it. The fake driver cannot
  clone across pools, so what is tested is every refusal sentence; the copy
  path follows virsh blockcopy semantics.
- **Cross-hypervisor import** (`core/convert.py`): vmdk/vhdx/vhd/vdi
  convert to qcow2 into the target pool on create (verified with a real
  `qemu-img` round trip); OVF descriptors are parsed namespace-blind
  (producers disagree), filling name/vcpus/memory into the wizard, and OVA
  disks are unpacked selectively from the tar. Multi-disk appliances import
  the boot disk and say so.
- **Drag-and-drop to guest**: files dropped on the console stack go through
  the existing guest-file-send, /tmp or C:\Users\Public by OS, queued
  sequentially with progress in the hint line.
- **XML diff preview**: `svc_definition_diff` canonicalises both sides the
  way mode diffs already did; the XML tab's Save and every mode switch show
  the coloured diff with the apply button on it. Off when confirmations are
  off.
- **Auto-attach USB**: rules in the stats database, a 10 s tick that costs
  nothing when no rules exist, and a pure planner with the property that a
  device already inside any guest - including one with no rule - is left
  alone. First rule wins when two machines claim one device.

## Known limits

- SPICE password auth is supported; SASL is not, for either protocol.
- Anonymous-TLS VeNCrypt subtypes (tls-none/tls-vnc) need ciphers OpenSSL
  disables by default, so x509 subtypes are what actually work.
- Graphics devices can't hot-plug (libvirt limitation) - "Add VNC display"
  edits the config and needs a restart.
- mdev instances are transient across host reboots; persisting them is
  mdevctl's job. Setting an SR-IOV VF count is host configuration, not
  something libvirt exposes.
