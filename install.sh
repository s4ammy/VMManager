#!/bin/sh
# Set VMManager up to run from this checkout.
#
# There is not much to it, but two details catch people out and neither is
# obvious from an error message:
#
#   - the virtualenv needs --system-site-packages, so it can see the
#     libvirt-python your distribution installed. That one links against the
#     libvirt on this machine, and pip can only build it when the development
#     headers are there. PySide6 and pyte are fine from pip.
#   - libvirt access needs you in the libvirt group, and the group only takes
#     effect on your next login.
#
# Safe to run again. Nothing outside this directory and
# ~/.local/share/applications is touched, and nothing needs root.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
cd "$here"

python=${PYTHON:-python3}
venv="$here/.venv"

say()  { printf '\n\033[1m>> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!! %s\033[0m\n' "$*"; }
die()  { printf '\033[31mXX %s\033[0m\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------ checks

command -v "$python" >/dev/null || die "no $python on PATH. Install Python 3.11 or newer."

"$python" - <<'EOF' || die "Python 3.11 or newer is needed."
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
EOF

# libvirt-python is the one that has to come from your distribution: it links
# against the libvirt already installed here, and pip can only build it if the
# development headers happen to be present. PySide6 and pyte are fine from pip -
# PySide6 brings its own copy of Qt - so those are left to the install below.
say "looking for libvirt-python"
if "$python" -c "import libvirt" 2>/dev/null; then
    printf '   found\n'
else
    cat >&2 <<'EOF'
   missing

libvirt-python is not installed for this Python. Install your distribution's
package rather than letting pip try:

  Arch            sudo pacman -S libvirt-python
  Debian/Ubuntu   sudo apt install python3-libvirt
  Fedora          sudo dnf install python3-libvirt

It links against the libvirt on this system, so pip can only build it when the
development headers are installed too - and the failure is not obvious.
EOF
    exit 1
fi

# ------------------------------------------------------------------- venv

if [ -d "$venv" ]; then
    say "reusing $venv"
    # libvirt is the probe, not PySide6: PySide6 may legitimately come from pip
    # inside the virtualenv, whereas libvirt can only come from the system.
    if ! "$venv/bin/python" -c "import libvirt" 2>/dev/null; then
        warn "the existing virtualenv cannot see libvirt-python."
        warn "It was probably made without --system-site-packages."
        warn "Delete $venv and run this again."
        exit 1
    fi
else
    say "making a virtualenv at $venv"
    "$python" -m venv --system-site-packages "$venv"
fi

say "installing VMManager into it"
"$venv/bin/pip" install --quiet --upgrade pip
"$venv/bin/pip" install --quiet -e '.[dev]'

say "checking what it ended up with"
for module in PySide6 libvirt pyte; do
    where=$("$venv/bin/python" -c "import $module; print($module.__file__)" 2>/dev/null) \
        || die "$module still cannot be imported. Nothing else will work."
    case "$where" in
        "$venv"/*) printf '   %-10s from pip\n' "$module" ;;
        *)         printf '   %-10s from the system\n' "$module" ;;
    esac
done

# --------------------------------------------------------------- libvirt

say "checking libvirt"
if "$venv/bin/python" - <<'EOF'
import sys
import libvirt
try:
    libvirt.open("qemu:///system").close()
except libvirt.libvirtError as exc:
    print(f"   cannot reach qemu:///system: {exc}")
    sys.exit(1)
print("   qemu:///system answers")
EOF
then
    :
else
    warn "VMManager will start, but it will have nothing to manage yet."
    warn "Usually one of:"
    warn "    sudo systemctl enable --now libvirtd"
    warn "    sudo usermod -aG libvirt \"\$USER\"   # then log out and back in"
fi

# ---------------------------------------------------------- menu entry

if [ -t 0 ]; then
    printf '\nAdd a start-menu entry for this checkout? [Y/n] '
    read -r answer || answer=n
else
    answer=n  # not a terminal, so do not hang waiting for an answer
fi
case "${answer:-y}" in
    [Nn]*) printf '   skipped. Run tools/dev-desktop-entry.sh later if you change your mind.\n' ;;
    *) "$here/tools/dev-desktop-entry.sh" ;;
esac

say "done"
cat <<EOF

Start it from your menu, or:

    $venv/bin/python -m vmmanager

Add --debug for more logging. The log is at ~/.cache/vmmanager/vmmanager.log.
EOF
