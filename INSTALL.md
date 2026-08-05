# Installing VMManager

You need libvirt running and your user in the `libvirt` group, whichever route
you take.

- [Quick start](#quick-start)
- [What it needs](#what-it-needs)
- [Arch Linux](#arch-linux)
- [Other distributions](#other-distributions)
- [Running from a checkout](#running-from-a-checkout)
- [A start-menu entry](#a-start-menu-entry)
- [Where it keeps things](#where-it-keeps-things)
- [If something goes wrong](#if-something-goes-wrong)

## Quick start

```sh
git clone <this repository>
cd VmManager
./install.sh
```

The script checks what you have, sets up a virtualenv, installs the app into it,
and offers to add a start-menu entry. Nothing outside the checkout and
`~/.local/share/applications` is touched. Run it again any time; it is safe to
repeat.

Then start it from your menu, or:

```sh
.venv/bin/python -m vmmanager
```

## What it needs

- **Python 3.11** or newer.
- **libvirt**, running, with your user in the `libvirt` group:
  ```sh
  sudo usermod -aG libvirt "$USER"      # log out and back in afterwards
  sudo systemctl enable --now libvirtd
  ```
- **libvirt-python**, from your distribution. This is the one that matters: it
  links against the libvirt on your machine, so pip can only build it when the
  development headers happen to be installed, and the failure is confusing.

  | | |
  |---|---|
  | Arch | `sudo pacman -S libvirt-python` |
  | Debian/Ubuntu | `sudo apt install python3-libvirt` |
  | Fedora | `sudo dnf install python3-libvirt` |

- **PySide6** and **pyte** are fine from either. `install.sh` lets pip fetch
  them if your distribution does not have them - PySide6 brings its own copy of
  Qt. Distribution packages work too, and save the download.

Everything else is optional - see
[Things it needs help for](FEATURES.md#things-it-needs-help-for).

## Arch Linux

There is a PKGBUILD, which pulls the dependencies from the repositories rather
than pip:

```sh
cd packaging && makepkg -si
```

The launcher is then `vmmanager`, on your `PATH` and in your menu.

## Other distributions

Build a wheel and install it:

```sh
python -m build --wheel
pip install --user dist/vmmanager-*.whl
install -Dm644 vmmanager.desktop ~/.local/share/applications/vmmanager.desktop
```

Use `--break-system-packages` only if you understand what it does to your
system Python; a virtualenv, as below, is usually the better answer.

## Running from a checkout

This is what `install.sh` sets up, and what to do by hand if you would rather:

```sh
python -m venv --system-site-packages .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m vmmanager
```

`--system-site-packages` is the important part. Without it the virtualenv cannot
see the libvirt-python your distribution installed, and pip's attempt to build
its own usually fails.

`--debug` turns up the logging.

## A start-menu entry

For a checkout:

```sh
tools/dev-desktop-entry.sh
```

That rewrites `vmmanager.desktop` to point at the checkout and its virtualenv,
installs it under `~/.local/share/applications`, and rebuilds your menu's cache.
Run it again after changing the desktop file - your menu reads a cache, not the
file.

## Where it keeps things

| Path | What |
|---|---|
| `~/.config/vmmanager/themes/` | your themes, one TOML file each |
| `~/.local/share/vmmanager/stats.db` | usage history, definition history, modes |
| `~/.cache/vmmanager/vmmanager.log` | the log |
| `~/.cache/vmmanager/images/` | downloaded cloud images |
| `~/.cache/vmmanager/oslogos/` | downloaded OS logos |
| `~/.config/vmmanager/` (QSettings) | connections, preferences |

Deleting any of them loses that data and nothing else. None of your machines
live here - they belong to libvirt.

## If something goes wrong

**"failed to connect to the hypervisor"** - libvirt is not running, or you are
not in the `libvirt` group. Check with `virsh -c qemu:///system list --all`; if
that fails too, it is not VMManager.

**No graphical console, or SPICE machines show nothing** - SPICE needs
`python-gobject` and spice-glib. VNC machines work without them.

**It starts but looks wrong** - fonts or icons did not load. If you installed a
wheel by hand, check that `vmmanager/assets/` came with it.

**Something else** - the log is at `~/.cache/vmmanager/vmmanager.log`, and
`--debug` makes it say more. A crash is reported in the window as well as
logged.
