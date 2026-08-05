# Working on VMManager

## Running the tests

```sh
.venv/bin/python -m pytest -q
```

701 tests, about 10 seconds, no display needed. They run against libvirt's own
fake hypervisor (`test:///default`), so they exercise the real service functions
with real libvirt semantics and never touch a real machine. They also stay out of
your own data - the stats database is redirected to a temporary one.

Two things about that fake driver are worth knowing before you write a test for
it:

- Its state is shared by every connection open in the process, and resets only
  when the last one closes. A test that defines a domain has to undefine it, or
  it turns up in tests that expect only the driver's own.
- Several tests shut its domain down deliberately. The `testconn` fixture puts it
  back, so no test depends on another having tidied up.

## What the tests are for

Each one was checked by breaking the code it covers and confirming it fails. A
test that cannot fail is worse than no test.

| File | What it guards |
|---|---|
| `test_vnc.py` | the RFB handshake and pixel decoders, through a fake socket; DES auth against vectors from openssl |
| `test_xml_generation.py` | disk targets, boot order, whole domains defined against the fake driver, cloud-init documents |
| `test_xml_escaping.py` | a tag of `R&D` used to produce XML libvirt refuses |
| `test_ui_smoke.py` | every dialog and page constructed - catches a lazily-imported dialog broken by a refactor |
| `test_dialog_sizing.py` | every dialog squeezed to its smallest size, checking no wrapped text is taller than its label and no two widgets overlap. Seven dialogs used to draw over their own buttons |
| `test_themes.py` | theme files round-tripped, and every value refused unless it is what it claims to be - a colour that closes the CSS rule would otherwise reach the stylesheet |
| `test_theme_qss.py` | arrows drawn with CSS borders render as squares in Qt, and `:hover::subcontrol` draws a stray second copy |
| `test_no_blocking_ui_calls.py` | an AST walk for libvirt calls on the UI thread. Invisible locally; a frozen window over SSH |
| `test_machine_window.py` | machines popped into their own windows: one per machine, each fed from the same poll tick, each torn down on close |
| `test_modes.py` | saving and switching whole definitions, and what happens when a mode's marker cannot be written |
| `test_poller.py` | that descriptions are cached, and that libvirt events uncache them |
| `test_tuning.py` | CPU pinning against a real host topology, hugepages, disk limits |
| `test_features.py` | Hyper-V enlightenments read from host capabilities rather than a list |
| `test_helpers.py` | ssh URI parsing, palette matching, the stats store, the wrapping row layout |
| `test_backup_restore.py` | the chain walk from any backup to its full run, and that rebuilding only ever writes copies |
| `test_move_disk.py` | every way a disk move refuses to start (the copy itself needs real libvirt) |
| `test_vencrypt.py` | VeNCrypt subtype choice and when TLS is preferred, through a real QSslSocket with fake I/O |
| `test_convert.py` | OVF parsed as producers actually write it, OVA unpacking, qemu-img argv |
| `test_mdev.py` | mdev types and SR-IOV read from a fake /sys, node-device XML both ways |
| `test_nwfilter.py` | the per-NIC filterref edit, and no-support answering as no filters |
| `test_scheduler.py` | schedule decisions shared by app and daemon, and the heartbeat handshake |
| `test_usb_rules.py` | the auto-attach plan, including never stealing a device from another guest |
| `test_console_drop.py` | dropped-file mime handling and per-OS guest destinations |
| `test_vfio.py` | PCI addresses that must never reach a root command line, boot-binding files, and option-ROM parsing built to the spec byte by byte |
| `test_hooks.py` | the generated single-GPU scripts: valid shell, undone in reverse, someone else's dispatcher untouched, and isolation that can never leave the host without a CPU |

Coverage is 59% of statements. The rest is mostly Qt wiring, where a unit test
asserts little; what is covered is the code that fails silently.

## Checking the look

```sh
.venv/bin/python -m vmmanager --screenshot out.png
```

Renders the window offscreen and saves a PNG, which is how UI changes get
checked without a display. `QT_QPA_PLATFORM=offscreen` works for scripts too.

## How the code is arranged

```
vmmanager/
├── core/          every libvirt operation, split by concern
│   ├── models.py      value objects handed to the UI
│   ├── connection.py  URI and poll settings, short-lived connections
│   ├── xmlutil.py     domain-XML helpers
│   ├── poller.py      the poll worker
│   ├── osident.py     which OS a machine runs
│   ├── themes.py      theme files: load, validate, save, derive shades
│   ├── restyle.py     applying a theme to a window already on screen
│   ├── modes.py       whole definitions saved under a name, and markers
│   └── domains.py devices.py storage.py networks.py snapshots.py
│       guest.py hostdev.py console.py create.py tuning.py features.py
├── console/       VNC and SPICE clients, serial and SSH terminals, ssh tunnel
├── data/          stats and history store, image catalogue, libosinfo, OS logos
├── dialogs/       grouped by what they act on (machine, hardware, storage…)
├── widgets/       formatting, painted indicators, cards, the app shell
├── pages/         one module per page; detail/ has one per tab, plus
│                  window.py for a machine in a window of its own
├── assets/        fonts, icons, OS logos, the theme that ships with the app
├── theme.py       the stylesheet template and the tokens in use
├── palette.py syntax.py topology.py tasks.py wizard.py main_window.py
└── libvirt_service.py   a façade re-exporting core/ for older imports
```

Import services from `vmmanager.core`, or through the `libvirt_service` façade.
Core submodules import in dependency order, with no cycles.

## Rules that the tests enforce

- **No libvirt call on the UI thread.** Everything goes through
  `tasks.run_task`, which runs it on the thread pool and delivers the result by
  signal. A blocking call is invisible on a local connection and freezes the
  window on a remote one.
- **No colour written as a literal.** Colours come from the theme, so a theme
  can change them. `test_themes.py` fails on a hardcoded token value.
- **Corner radii come from the three tokens**, including corners painted in
  code.
- **Values from a theme file are validated before they reach the stylesheet.**
  A colour is `#rrggbb` or it is refused.

## Two traps worth remembering

**Default arguments are evaluated at import.** `def __init__(self, path=DB_PATH)`
looks harmless and means the path can never be redirected afterwards - which is
how the test suite came to write to the real stats database, and how usage bars
stayed the old accent colour under every theme. Resolve module state inside the
function.

**A stale `.pyc` will lie to you.** Restoring a file with `cp` can leave
bytecode newer than the source. Clear `__pycache__` before concluding that a
change had no effect - it has cost hours twice.
