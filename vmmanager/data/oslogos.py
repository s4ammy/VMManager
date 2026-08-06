"""OS icons for the machine list.

Sources, tried in order:

1. Artwork bundled under assets/logos, named for the OS it belongs to. This is
   where a logo goes when the alternatives all look wrong: CachyOS is not in
   simple-icons, and the penguin we used to paint for the Linux family was a
   poor likeness of one.
2. simple-icons (CC0): single-path SVGs fetched once into
   ~/.cache/vmmanager/oslogos and tinted with the brand colour. One silhouette
   style throughout, which keeps a list of twenty machines readable.
3. The host icon theme, which often ships distributor-logo-*.svg for distros
   simple-icons doesn't cover. Full colour, used as-is.
4. Bundled artwork named for the *family* rather than the OS - the Tux that
   stands in for any Linux with nothing more specific.
5. Glyphs we paint, for Windows (no permissive set carries Microsoft's marks)
   and as the last resort.

A bundled logo for the exact OS wins outright; one for a whole family only
stands in where there is nothing better, so a distro's own icon still wins.
Nothing downloads unless the feature is on, and everything except 2 works with
no network at all.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ..core.osident import OS_CATALOG, family, is_custom_icon

CACHE_DIR = Path.home() / ".cache" / "vmmanager" / "oslogos"
CDN = "https://cdn.jsdelivr.net/npm/simple-icons@15/icons/{slug}.svg"

# Logos that ship with vmmanager, one file per OS key or family name. Add a
# PNG or SVG called after the key and it is used ahead of everything else.
BUNDLED_DIR = Path(__file__).parent.parent / "assets" / "logos"
BUNDLED_SUFFIXES = (".png", ".svg")

# What simple-icons carries. Microsoft's marks aren't in it, so Windows is
# painted instead.
SIMPLE_ICON_KEYS = frozenset({
    "archlinux", "debian", "ubuntu", "linuxmint", "popos", "elementary",
    "zorin", "fedora", "redhat", "centos", "rockylinux", "almalinux",
    "opensuse", "suse", "manjaro", "endeavouros", "garudalinux", "gentoo",
    "nixos", "alpinelinux", "voidlinux", "kalilinux", "freebsd", "openbsd",
    "netbsd",
})

# Brand colours, so a silhouette still reads as the right OS at 20px.
BRAND_COLORS = {
    "archlinux": "#1793D1", "cachyos": "#00AEEF", "debian": "#A81D33",
    "ubuntu": "#E95420", "linuxmint": "#87CF3E", "popos": "#48B9C7",
    "elementary": "#64BAFF", "zorin": "#0CC1F3", "fedora": "#51A2DA",
    "redhat": "#EE0000", "centos": "#262577", "rockylinux": "#10B981",
    "almalinux": "#0F4266", "opensuse": "#73BA25", "suse": "#0C322C",
    "manjaro": "#35BF5C", "endeavouros": "#7F7FFF", "garudalinux": "#FF6C00",
    "gentoo": "#54487A", "nixos": "#5277C3", "alpinelinux": "#0D597F",
    "voidlinux": "#478061", "kalilinux": "#557C94", "raspbian": "#A22846",
    "devuan": "#4B4B4B", "slackware": "#3B5998", "mageia": "#2397D4",
    "deepin": "#007CFF", "solus": "#5294E2", "qubes": "#3874D8",
    "freebsd": "#AB2B28", "openbsd": "#F2CA30", "netbsd": "#FF6600",
    "windows": "#00A4EF", "linux": "#E8E8F2", "unknown": "#5C5C72",
}


def bundled_logo(name: str) -> Path | None:
    """The logo we ship for this OS key or family name, if there is one."""
    for suffix in BUNDLED_SUFFIXES:
        candidate = BUNDLED_DIR / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _theme_logo_path(key: str) -> Path | None:
    """A distributor logo from any installed icon theme."""
    names = [key, key.replace("linux", ""), key.replace("os", "")]
    if key == "linuxmint":
        names += ["linux-mint", "mint"]
    if key == "popos":
        names += ["pop-os"]
    for base in ("/usr/share/icons", "/usr/local/share/icons"):
        root = Path(base)
        if not root.is_dir():
            continue
        for name in names:
            if not name:
                continue
            for candidate in root.glob(f"*/scalable/apps/distributor-logo-{name}.svg"):
                return candidate
    return None


def cached_svg(key: str) -> Path:
    return CACHE_DIR / f"{key}.svg"


def missing_downloads(keys) -> list[str]:
    """Keys we could fetch but haven't yet."""
    return [
        key for key in dict.fromkeys(keys)
        if key in SIMPLE_ICON_KEYS and not cached_svg(key).exists()
    ]


class LogoDownloader(QThread):
    """Fetches missing logos. Failing quietly is fine here."""

    fetched = Signal(list)  # keys that arrived

    def __init__(self, keys: list[str]) -> None:
        super().__init__()
        self._keys = keys
        self._stop = False

    def stop(self) -> None:
        """Give up on the rest; the window that wanted them is going away."""
        self._stop = True

    def run(self) -> None:
        got = []
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for key in self._keys:
            if self._stop:
                return  # nothing to deliver to any more
            try:
                with urllib.request.urlopen(
                    CDN.format(slug=key), timeout=20
                ) as response:
                    data = response.read()
                if b"<svg" not in data[:400]:
                    continue
                cached_svg(key).write_bytes(data)
                got.append(key)
            except Exception:  # noqa: BLE001 - being offline isn't an error
                continue
        if got and not self._stop:
            self.fetched.emit(got)


# ---------------------------------------------------------------- painting


def _paint_windows(painter: QPainter, rect: QRectF, color: QColor) -> None:
    """Four panes with a gap. Everyone reads this as Windows."""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    gap = rect.width() * 0.10
    pane_w = (rect.width() - gap) / 2
    pane_h = (rect.height() - gap) / 2
    for row in range(2):
        for col in range(2):
            painter.drawRoundedRect(
                QRectF(
                    rect.left() + col * (pane_w + gap),
                    rect.top() + row * (pane_h + gap),
                    pane_w, pane_h,
                ),
                rect.width() * 0.05, rect.width() * 0.05,
            )


def _paint_daemon(painter: QPainter, rect: QRectF, color: QColor) -> None:
    """Horned circle, for the BSDs."""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    w, h = rect.width(), rect.height()
    painter.drawEllipse(
        QRectF(rect.left() + w * 0.14, rect.top() + h * 0.26, w * 0.72, h * 0.62)
    )
    for x_frac in (0.18, 0.62):
        horn = QPainterPath()
        horn.moveTo(rect.left() + w * x_frac, rect.top() + h * 0.40)
        horn.lineTo(rect.left() + w * (x_frac + 0.10), rect.top() + h * 0.04)
        horn.lineTo(rect.left() + w * (x_frac + 0.20), rect.top() + h * 0.40)
        horn.closeSubpath()
        painter.drawPath(horn)


def _paint_generic(painter: QPainter, rect: QRectF, color: QColor) -> None:
    """Dashed square: we don't know."""
    pen = painter.pen()
    pen.setColor(color)
    pen.setWidthF(max(1.2, rect.width() * 0.08))
    pen.setStyle(Qt.PenStyle.DotLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    inset = rect.width() * 0.14
    painter.drawRoundedRect(
        rect.adjusted(inset, inset, -inset, -inset),
        rect.width() * 0.12, rect.width() * 0.12,
    )


# No entry for "linux": assets/logos/linux.png stands in for the whole family,
# and a body-with-two-feet ellipse was never a convincing penguin.
_PAINTERS = {
    "windows": _paint_windows,
    "bsd": _paint_daemon,
    "generic": _paint_generic,
}

_cache: dict[tuple[str, int], QPixmap] = {}


def brand_color(key: str) -> QColor:
    return QColor(BRAND_COLORS.get(key, BRAND_COLORS["unknown"]))


def _tinted(svg_path: Path, size: int, color: QColor) -> QPixmap:
    """Render a monochrome SVG in one colour."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(svg_path)).render(painter, QRectF(0, 0, size, size))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), color)
    painter.end()
    return pixmap


def _plain(svg_path: Path, size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(svg_path)).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def resolve(key: str) -> tuple[str, Path | None]:
    """Where this key's icon comes from, and the file if there is one.

    One decision, used both to draw the icon and to describe it, because two
    copies of this order drifted apart the moment bundled logos were added.
    """
    if is_custom_icon(key):
        source = Path(key)
        return ("your file", source) if source.is_file() else ("missing file", None)

    own = bundled_logo(key)
    if own is not None:
        return "bundled", own
    svg = cached_svg(key)
    if svg.exists():
        return "simple-icons", svg
    from_theme = _theme_logo_path(key)
    if from_theme is not None:
        return "icon theme", from_theme
    for_family = bundled_logo(family(key))
    if for_family is not None:
        return f"bundled, for any {family(key)}", for_family
    return "drawn", None


def logo_pixmap(key: str, size: int = 22) -> QPixmap:
    """Best icon available for this key, at this size.

    A key that looks like a path is an image the user picked. If it has since
    moved, fall through to the drawn fallback rather than showing nothing.
    """
    cache_key = (key, size)
    if cache_key in _cache:
        return _cache[cache_key]

    where, source = resolve(key)
    pixmap = None
    if source is not None:
        pixmap = (
            _tinted(source, size, brand_color(key))  # a silhouette needs a colour
            if where == "simple-icons"
            else _image_pixmap(source, size)
        )
    if pixmap is None:  # nothing to load, or the file turned out to be unreadable
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint = _PAINTERS.get(family(key), _paint_generic)
        paint(painter, QRectF(0, 0, size, size), brand_color(key))
        painter.end()
    _cache[cache_key] = pixmap
    return pixmap


def _image_pixmap(source: Path, size: int) -> QPixmap | None:
    """Fit an image into the icon box, keeping its proportions."""
    if source.suffix.lower() == ".svg":
        return _plain(source, size)
    image = QPixmap(str(source))
    if image.isNull():
        return None
    return image.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _custom_pixmap(path: str, size: int) -> QPixmap | None:
    """Scale a user-chosen image to the icon box, keeping its proportions."""
    source = Path(path)
    if not source.is_file():
        return None
    return _image_pixmap(source, size)


CUSTOM_FILTER = "Images (*.png *.svg *.jpg *.jpeg *.webp *.ico *.bmp)"


def forget_cached_pixmaps() -> None:
    """Drop the pixmap cache so newly downloaded logos get used."""
    _cache.clear()


def all_keys() -> list[str]:
    """Every key the override dialog can offer, in catalogue order."""
    return list(OS_CATALOG)


def source_of(key: str) -> str:
    """Which source this key resolves to, in words."""
    return resolve(key)[0]


def icon_size_hint() -> QSize:
    return QSize(22, 22)
