#!/bin/sh
# Install a start-menu entry that runs VMManager from this checkout.
#
# The packaged entry (vmmanager.desktop) says `Exec=vmmanager` and `Icon=vmmanager`,
# which only work once the package is installed. This rewrites those two lines to
# point at the checkout and its virtualenv, and leaves everything else alone - so
# the name, keywords and categories stay in one place instead of being retyped
# into a hand-made copy that then drifts.
#
# Usage: tools/dev-desktop-entry.sh
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
source_file="$here/vmmanager.desktop"
target_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
target="$target_dir/vmmanager.desktop"
python="$here/.venv/bin/python"

[ -f "$source_file" ] || { echo "no $source_file" >&2; exit 1; }
[ -x "$python" ] || python=$(command -v python3)

mkdir -p "$target_dir"
sed \
    -e "s|^Exec=.*|Exec=$python -m vmmanager|" \
    -e "s|^Icon=.*|Icon=$here/vmmanager/assets/icon.svg|" \
    -e "/^\[Desktop Entry\]/a Path=$here" \
    "$source_file" > "$target"

echo "wrote $target"

# The menu reads a cache, not the file, so both of these are worth a try. They
# are absent on some desktops, which is not a failure.
update-desktop-database "$target_dir" 2>/dev/null || true
kbuildsycoca6 2>/dev/null || kbuildsycoca5 2>/dev/null || true
