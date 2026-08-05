#!/bin/sh
# Install a start-menu entry that runs VMManager from this checkout.
#
# The packaged entry (vmmanager.desktop) says `Exec=vmmanager` and `Icon=vmmanager`,
# which only work once the package is installed. This rewrites Exec to point at
# the checkout and its virtualenv, and leaves everything else alone - so the name,
# keywords and categories stay in one place instead of being retyped into a
# hand-made copy that then drifts.
#
# The icon is linked into the hicolor theme rather than pointed at by absolute
# path. Both work, but a desktop caches an icon named through the theme in the
# ordinary way and picks changes up; one named by a path outside any theme can
# sit in a stale cache until the session restarts, which is exactly the bug this
# avoids. The link means editing assets/icon.svg still shows up straight away.
#
# Usage: tools/dev-desktop-entry.sh
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
source_file="$here/vmmanager.desktop"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
target_dir="$data_home/applications"
target="$target_dir/vmmanager.desktop"
icon_dir="$data_home/icons/hicolor/scalable/apps"
python="$here/.venv/bin/python"

[ -f "$source_file" ] || { echo "no $source_file" >&2; exit 1; }
[ -x "$python" ] || python=$(command -v python3)

mkdir -p "$icon_dir"
ln -sf "$here/vmmanager/assets/icon.svg" "$icon_dir/vmmanager.svg"
echo "linked $icon_dir/vmmanager.svg"

mkdir -p "$target_dir"
sed \
    -e "s|^Exec=.*|Exec=$python -m vmmanager|" \
    -e "/^\[Desktop Entry\]/a Path=$here" \
    "$source_file" > "$target"

echo "wrote $target"

# The menu reads caches, not the files, so refresh every one that is here.
# They are absent on some desktops, which is not a failure.
update-desktop-database "$target_dir" 2>/dev/null || true
# Only worth running where the theme has an index for it to read; without one
# it refuses and says the cache it built was invalid. GTK ignores a cache
# older than the directory anyway, and KDE does not read this one at all.
if [ -f "$data_home/icons/hicolor/index.theme" ]; then
    gtk-update-icon-cache -f -t "$data_home/icons/hicolor" 2>/dev/null || true
fi
kbuildsycoca6 2>/dev/null || kbuildsycoca5 2>/dev/null || true
