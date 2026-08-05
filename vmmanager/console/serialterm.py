"""Serial console: libvirt console stream on a thread + a pyte terminal widget."""

from __future__ import annotations

import libvirt
import pyte
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from .. import theme
from ..core.themes import lighten, mix


class SerialSession(QThread):
    """Owns a dedicated libvirt connection and pumps the console stream.

    recv() blocks on this thread; send() is safe from the UI thread because
    libvirt serializes calls per connection.
    """

    received = Signal(bytes)
    closed = Signal(str)  # reason ("" for clean close)

    def __init__(self, uri: str, uuid: str) -> None:
        super().__init__()
        self._uri = uri
        self._uuid = uuid
        self._stream = None
        self._conn = None
        self._stopping = False
        self._pending: list[bytes] = []  # writes queued before the stream opens

    def run(self) -> None:
        try:
            self._conn = libvirt.open(self._uri)
            dom = self._conn.lookupByUUIDString(self._uuid)
            stream = self._conn.newStream(0)
            dom.openConsole(None, stream, 0)
            self._stream = stream
            for data in self._pending:
                stream.send(data)
            self._pending.clear()
        except libvirt.libvirtError as e:
            self.closed.emit(str(e))
            self._cleanup()
            return
        try:
            while not self._stopping:
                data = self._stream.recv(4096)
                if not data:
                    break
                self.received.emit(bytes(data))
            self.closed.emit("")
        except libvirt.libvirtError as e:
            self.closed.emit("" if self._stopping else str(e))
        self._cleanup()

    def _cleanup(self) -> None:
        try:
            if self._stream is not None:
                self._stream.abort()
        except libvirt.libvirtError:
            pass
        try:
            if self._conn is not None:
                self._conn.close()
        except libvirt.libvirtError:
            pass
        self._stream = None
        self._conn = None

    def send(self, data: bytes) -> None:
        if self._stopping:
            return
        if self._stream is None:
            self._pending.append(data)
            return
        try:
            self._stream.send(data)
        except libvirt.libvirtError:
            pass

    def stop(self) -> None:
        self._stopping = True
        try:
            if self._stream is not None:
                self._stream.abort()  # unblocks recv()
        except libvirt.libvirtError:
            pass
        self.wait(2000)


_palette_cache: tuple[object, dict[str, str]] | None = None


def _color_map() -> dict[str, str]:
    """pyte's named colours, taken from the current theme.

    Six of the eight ANSI colours are things the theme already names, so the
    terminal follows it rather than keeping its own opinion. Blue and cyan have
    no token - nothing else in the app needs them - so they stay as they were,
    picked to sit on the inset background. Cached because this is asked for
    every cell of every repaint.
    """
    global _palette_cache
    if _palette_cache is not None and _palette_cache[0] is theme.active:
        return _palette_cache[1]

    bright = 0.18
    colors = {
        "black": mix(theme.BG, theme.TEXT_FAINT, 0.55),
        "red": theme.DANGER,
        "green": theme.OK,
        "brown": theme.WARN,
        "blue": "#8fb8e8",
        "magenta": theme.ACCENT,
        "cyan": "#8fd8d8",
        "white": theme.TEXT,
        "brightblack": theme.TEXT_FAINT,
        "brightwhite": "#ffffff",
    }
    for name in ("red", "green", "brown", "blue", "magenta", "cyan"):
        colors[f"bright{name}"] = lighten(colors[name], bright)
    _palette_cache = (theme.active, colors)
    return colors


def _qcolor(name: str, default: str) -> QColor:
    if name == "default":
        return QColor(default)
    mapped = _color_map().get(name)
    if mapped is not None:
        return QColor(mapped)
    return QColor(f"#{name}") if len(name) == 6 else QColor(default)


class TerminalWidget(QWidget):
    """Minimal but competent VT102/ANSI terminal rendered with QPainter."""

    input_ready = Signal(bytes)
    size_changed = Signal(int, int)  # rows, cols

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self._font = QFont(theme.MONO, 11)
        metrics = QFontMetricsF(self._font)
        self._cw = metrics.horizontalAdvance("M")
        self._ch = metrics.height()
        self._ascent = metrics.ascent()
        self._cols, self._rows = 80, 24
        self._screen = pyte.HistoryScreen(self._cols, self._rows, history=2000)
        self._stream = pyte.ByteStream(self._screen)
        self._connected = False

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        if connected:
            self._screen.reset()
        self.update()

    def feed(self, data: bytes) -> None:
        self._stream.feed(data)
        self.update()

    # -- geometry

    def resizeEvent(self, _event) -> None:
        cols = max(20, int(self.width() / self._cw))
        rows = max(5, int(self.height() / self._ch))
        if (cols, rows) != (self._cols, self._rows):
            self._cols, self._rows = cols, rows
            self._screen.resize(rows, cols)
            self.size_changed.emit(rows, cols)
        super().resizeEvent(_event)

    @property
    def grid_size(self) -> tuple[int, int]:
        return self._rows, self._cols

    # -- painting

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG_INSET))
        p.setFont(self._font)
        if not self._connected:
            p.setPen(QColor(theme.TEXT_FAINT))
            p.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "serial console not connected"
            )
            p.end()
            return
        buffer = self._screen.buffer
        for row in range(self._rows):
            line = buffer[row]
            y = row * self._ch
            for col in range(self._cols):
                ch = line[col]
                if ch.data == " " and ch.bg == "default":
                    continue
                fg = _qcolor(ch.fg, theme.TEXT)
                bg = _qcolor(ch.bg, theme.BG_INSET)
                if ch.reverse:
                    fg, bg = bg, fg
                x = col * self._cw
                if bg.name() != QColor(theme.BG_INSET).name():
                    p.fillRect(int(x), int(y), int(self._cw) + 1, int(self._ch) + 1, bg)
                if ch.bold:
                    bold_font = QFont(self._font)
                    bold_font.setBold(True)
                    p.setFont(bold_font)
                p.setPen(fg)
                p.drawText(int(x), int(y + self._ascent), ch.data)
                if ch.bold:
                    p.setFont(self._font)
        cur = self._screen.cursor
        if not cur.hidden and self.hasFocus():
            p.fillRect(
                int(cur.x * self._cw), int(cur.y * self._ch),
                int(self._cw), int(self._ch), QColor(theme.ACCENT),
            )
            ch = buffer[cur.y][cur.x]
            p.setPen(QColor(theme.BG))
            p.drawText(
                int(cur.x * self._cw), int(cur.y * self._ch + self._ascent), ch.data
            )
        p.end()

    # -- input

    _SPECIAL = {
        Qt.Key.Key_Return: b"\r", Qt.Key.Key_Enter: b"\r",
        Qt.Key.Key_Backspace: b"\x7f", Qt.Key.Key_Tab: b"\t",
        Qt.Key.Key_Escape: b"\x1b",
        Qt.Key.Key_Up: b"\x1b[A", Qt.Key.Key_Down: b"\x1b[B",
        Qt.Key.Key_Right: b"\x1b[C", Qt.Key.Key_Left: b"\x1b[D",
        Qt.Key.Key_Home: b"\x1b[H", Qt.Key.Key_End: b"\x1b[F",
        Qt.Key.Key_PageUp: b"\x1b[5~", Qt.Key.Key_PageDown: b"\x1b[6~",
        Qt.Key.Key_Delete: b"\x1b[3~", Qt.Key.Key_Insert: b"\x1b[2~",
        Qt.Key.Key_F1: b"\x1bOP", Qt.Key.Key_F2: b"\x1bOQ",
        Qt.Key.Key_F3: b"\x1bOR", Qt.Key.Key_F4: b"\x1bOS",
        Qt.Key.Key_F5: b"\x1b[15~", Qt.Key.Key_F6: b"\x1b[17~",
        Qt.Key.Key_F7: b"\x1b[18~", Qt.Key.Key_F8: b"\x1b[19~",
        Qt.Key.Key_F9: b"\x1b[20~", Qt.Key.Key_F10: b"\x1b[21~",
        Qt.Key.Key_F11: b"\x1b[23~", Qt.Key.Key_F12: b"\x1b[24~",
    }

    def keyPressEvent(self, event) -> None:
        mods = event.modifiers()
        key = event.key()
        # Ctrl+Shift+V pastes, like every terminal
        if (
            key == Qt.Key.Key_V
            and mods & Qt.KeyboardModifier.ControlModifier
            and mods & Qt.KeyboardModifier.ShiftModifier
        ):
            text = QApplication.clipboard().text()
            if text:
                self.input_ready.emit(text.replace("\n", "\r").encode())
            return
        if key in self._SPECIAL:
            self.input_ready.emit(self._SPECIAL[key])
            return
        if mods & Qt.KeyboardModifier.ControlModifier:
            if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
                self.input_ready.emit(bytes([key - Qt.Key.Key_A + 1]))
                return
        text = event.text()
        if text:
            self.input_ready.emit(text.encode())

    def focusNextPrevChild(self, _next: bool) -> bool:
        return False
