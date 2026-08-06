"""SPICE console widget built on spice-glib (GObject introspection).

No GTK involved: we use the toolkit-independent SpiceClientGLib channels and
paint the display channel's primary surface ourselves. The GLib main context
is pumped from a QTimer, so everything stays on the Qt UI thread.
"""

from __future__ import annotations

import ctypes

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from .. import theme
from .grab import InputGrab, grab_on_click, release_combo

try:
    import gi

    gi.require_version("SpiceClientGLib", "2.0")
    from gi.repository import GLib, GObject, SpiceClientGLib as Spice

    SPICE_AVAILABLE = True
except (ImportError, ValueError):
    SPICE_AVAILABLE = False


# evdev keycode -> XT scancode; identity below 89, extended (0x100-flagged)
# entries here. spice-glib turns 0x100|code into the e0-prefixed wire form.
_EVDEV_TO_XT = {
    96: 0x100 | 0x1C,   # keypad enter
    97: 0x100 | 0x1D,   # right ctrl
    98: 0x100 | 0x35,   # keypad /
    99: 0x100 | 0x37,   # print screen
    100: 0x100 | 0x38,  # right alt
    102: 0x100 | 0x47,  # home
    103: 0x100 | 0x48,  # up
    104: 0x100 | 0x49,  # page up
    105: 0x100 | 0x4B,  # left
    106: 0x100 | 0x4D,  # right
    107: 0x100 | 0x4F,  # end
    108: 0x100 | 0x50,  # down
    109: 0x100 | 0x51,  # page down
    110: 0x100 | 0x52,  # insert
    111: 0x100 | 0x53,  # delete
    119: 0x45,          # pause (close enough)
    125: 0x100 | 0x5B,  # left super
    126: 0x100 | 0x5C,  # right super
    127: 0x100 | 0x5D,  # menu
}

# fallback when the platform gives no usable native scancode
_QTKEY_TO_XT = {
    Qt.Key.Key_Escape: 0x01, Qt.Key.Key_Backspace: 0x0E, Qt.Key.Key_Tab: 0x0F,
    Qt.Key.Key_Return: 0x1C, Qt.Key.Key_Enter: 0x100 | 0x1C,
    Qt.Key.Key_Control: 0x1D, Qt.Key.Key_Shift: 0x2A, Qt.Key.Key_Alt: 0x38,
    Qt.Key.Key_Space: 0x39, Qt.Key.Key_CapsLock: 0x3A,
    Qt.Key.Key_Up: 0x100 | 0x48, Qt.Key.Key_Down: 0x100 | 0x50,
    Qt.Key.Key_Left: 0x100 | 0x4B, Qt.Key.Key_Right: 0x100 | 0x4D,
    Qt.Key.Key_Home: 0x100 | 0x47, Qt.Key.Key_End: 0x100 | 0x4F,
    Qt.Key.Key_PageUp: 0x100 | 0x49, Qt.Key.Key_PageDown: 0x100 | 0x51,
    Qt.Key.Key_Insert: 0x100 | 0x52, Qt.Key.Key_Delete: 0x100 | 0x53,
}
_QTKEY_TO_XT.update({Qt.Key.Key_F1 + i: 0x3B + i for i in range(10)})
_QTKEY_TO_XT[Qt.Key.Key_F11] = 0x57
_QTKEY_TO_XT[Qt.Key.Key_F12] = 0x58
# number row and letters (QWERTY positions - fallback only)
for i, ch in enumerate("1234567890"):
    _QTKEY_TO_XT[getattr(Qt.Key, f"Key_{ch}")] = 0x02 + i
for scan, ch in [(0x10, "QWERTYUIOP"), (0x1E, "ASDFGHJKL"), (0x2C, "ZXCVBNM")]:
    for i, letter in enumerate(ch):
        _QTKEY_TO_XT[getattr(Qt.Key, f"Key_{letter}")] = scan + i

VD_AGENT_CLIPBOARD_UTF8_TEXT = 1


class SpiceClient(QWidget):
    """Interactive SPICE display; mirrors VncClient's small API."""

    state_changed = Signal(str)  # "connecting" | "connected" | "closed" | error text
    mouse_mode_changed = Signal(int)  # MOUSE_SERVER | MOUSE_CLIENT
    capture_changed = Signal(bool)  # pointer captured for relative mode
    grab_changed = Signal(bool)  # keyboard handed to the guest

    MOUSE_SERVER, MOUSE_CLIENT = 1, 2

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._session = None
        self._display = None
        self._inputs = None
        self._main = None
        self._cursor_channel = None
        self._image: QImage | None = None
        self._fb = None  # (addr, stride, width, height)
        self._buttons_state = 0
        self._active = False
        self._clipboard_out = b""
        # What the guest last sent us. Writing that into the host clipboard
        # raises dataChanged, and offering it straight back to the guest
        # would be a loop - compared by text rather than guarded by a flag,
        # because setText does not promise to emit before it returns.
        self._clipboard_in = ""
        QApplication.clipboard().dataChanged.connect(self._host_clipboard_changed)
        self._mouse_mode = self.MOUSE_CLIENT
        self._captured = False  # pointer grabbed for relative mode
        self._warping = False  # ignore the move event our own warp generates
        self._motion_remainder = [0.0, 0.0]  # sub-pixel delta carry
        self._held: dict[int, int] = {}  # qt key -> scancode currently down
        self.grab = InputGrab(self)
        self.grab.changed.connect(self.grab_changed)
        self._pump = QTimer(self)
        # Nothing moves between ticks: an keystroke on its way out and a
        # frame on its way in each wait for one, so the interval is added to
        # the round trip twice. 4 ms costs a few hundred idle wakeups a
        # second and takes ~16 ms off the worst case.
        self._pump.setInterval(4)
        self._pump.timeout.connect(self._iterate)
        # debounce guest-resolution requests while a window is being dragged
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(600)
        self._resize_timer.timeout.connect(
            lambda: self.request_guest_resolution(self.width(), self.height())
        )

    # -- lifecycle

    def open_tcp(self, host: str, port: int, password: str = "",
                 tls_port: int = -1) -> None:
        self.close_connection()
        if not SPICE_AVAILABLE:
            self.state_changed.emit("error: spice-glib not available on this host")
            return
        session = Spice.Session()
        session.props.host = host
        if port > 0:
            session.props.port = str(port)
        if tls_port > 0:
            from ..pages.settings import console_tls_ca, console_tls_no_verify

            session.props.tls_port = str(tls_port)
            # otherwise only the channels the server marks secure use it
            session.props.secure_channels = ["all"]
            ca = console_tls_ca()
            if ca:
                session.props.ca_file = ca
            if console_tls_no_verify():
                session.props.verify = Spice.SessionVerify(0)
        if password:
            session.props.password = password
        GObject.Object.connect(session, "channel-new", self._on_channel_new)
        GObject.Object.connect(session, "channel-destroy", self._on_channel_destroy)
        GObject.Object.connect(session, "disconnected", self._on_disconnected)
        self._session = session
        self.state_changed.emit("connecting")
        Spice.Session.connect(session)
        self._pump.start()

    def close_connection(self) -> None:
        self.release_all()
        self._pump.stop()
        if self._session is not None:
            try:
                self._session.disconnect()  # Spice.Session.disconnect
            except Exception:  # noqa: BLE001, teardown must not raise
                pass
            # drain pending GLib events so channels finalize
            if SPICE_AVAILABLE:
                ctx = GLib.MainContext.default()
                while ctx.pending():
                    ctx.iteration(False)
        self._session = None
        self._display = None
        self._inputs = None
        self._main = None
        self._fb = None
        self._active = False
        self.update()

    @property
    def active(self) -> bool:
        return self._active

    def _iterate(self) -> None:
        ctx = GLib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)

    # -- channels

    def _on_channel_new(self, _session, channel) -> None:
        if isinstance(channel, Spice.MainChannel):
            self._main = channel
            GObject.Object.connect(
                channel, "main-clipboard-selection-request", self._on_clip_request
            )
            GObject.Object.connect(
                channel, "main-clipboard-selection", self._on_guest_clipboard
            )
            GObject.Object.connect(
                channel, "notify::mouse-mode", self._on_mouse_mode
            )
            self._read_mouse_mode()
        elif isinstance(channel, Spice.DisplayChannel):
            self._display = channel
            GObject.Object.connect(channel, "display-primary-create", self._on_primary)
            GObject.Object.connect(channel, "display-primary-destroy", self._on_primary_gone)
            GObject.Object.connect(channel, "display-invalidate", self._on_invalidate)
            GObject.Object.connect(channel, "display-mark", self._on_mark)
            Spice.Channel.connect(channel)
        elif isinstance(channel, Spice.InputsChannel):
            self._inputs = channel
            Spice.Channel.connect(channel)
        elif isinstance(channel, Spice.CursorChannel):
            self._cursor_channel = channel
            # The signals, not the `cursor` property: its SpiceCursorShape
            # marshals wrongly through PyGObject - see _on_cursor_set - and
            # these carry the same values as plain ints.
            GObject.Object.connect(channel, "cursor-set", self._on_cursor_set)
            GObject.Object.connect(channel, "cursor-hide", self._on_cursor_hide)
            GObject.Object.connect(channel, "cursor-reset", self._on_cursor_reset)
            Spice.Channel.connect(channel)

    def _on_channel_destroy(self, _session, channel) -> None:
        if channel is self._display:
            self._display = None
        elif channel is self._inputs:
            self._inputs = None
        elif channel is self._main:
            self._main = None
        elif channel is self._cursor_channel:
            self._cursor_channel = None

    # -- mouse mode & cursor shape

    def _read_mouse_mode(self) -> None:
        if self._main is None:
            return
        old = self._mouse_mode
        try:
            self._mouse_mode = self._main.props.mouse_mode or self.MOUSE_CLIENT
        except Exception:  # noqa: BLE001
            self._mouse_mode = self.MOUSE_CLIENT
        if self._mouse_mode == self.MOUSE_SERVER:
            # ask for absolute pointer; granted once a tablet or agent exists
            try:
                self._main.request_mouse_mode(self.MOUSE_CLIENT)
            except Exception:  # noqa: BLE001
                pass
        if self._mouse_mode != old:
            if self._mouse_mode == self.MOUSE_CLIENT:
                self.release_pointer()  # absolute mode needs no capture
            self.mouse_mode_changed.emit(self._mouse_mode)

    def _on_mouse_mode(self, _channel, _pspec) -> None:
        self._read_mouse_mode()

    def _on_cursor_set(self, _channel, width, height, hot_x, hot_y, rgba) -> None:
        """Guest cursor shape -> real QCursor, so the pointer looks native.

        The values come from the signal rather than the channel's `cursor`
        property, because SpiceCursorShape marshals wrongly through
        PyGObject - the same trap as display_get_primary(). It reports a
        32x32 Windows cursor as 8x9, and reading `type` as 64 bits swallows
        the width and height that follow it. An 8x9 cursor is invisible,
        which is what "I cannot see my mouse" turned out to be.
        """
        if self._captured:
            return  # captured: the local pointer stays hidden
        if not width or not height or not rgba:
            self.unsetCursor()  # an arrow beats no pointer at all
            return
        try:
            buf = (ctypes.c_ubyte * (width * height * 4)).from_address(int(rgba))
            # .tobytes() is a memcpy; bytes(ctypes_array) builds an int per
            # byte first. The copy matters - spice owns that memory and
            # frees it - but it does not have to be made a byte at a time.
            img = QImage(
                memoryview(buf).cast("B").tobytes(), width, height, width * 4,
                QImage.Format.Format_ARGB32,
            )
            from PySide6.QtGui import QCursor, QPixmap

            self.setCursor(QCursor(QPixmap.fromImage(img), hot_x, hot_y))
        except (TypeError, ValueError, OverflowError):
            self.unsetCursor()

    def _on_cursor_hide(self, _channel) -> None:
        """The guest asked for no pointer at all - a full-screen game, say."""
        self.setCursor(Qt.CursorShape.BlankCursor)

    def _on_cursor_reset(self, _channel) -> None:
        """Back to the host's own arrow; the guest is no longer drawing one."""
        self.unsetCursor()

    def _on_disconnected(self, _session) -> None:
        if self._session is None:
            return
        was_active = self._active
        self.close_connection()
        self.state_changed.emit("closed" if was_active else "error: connection refused")

    # -- display

    def _on_primary(self, _channel, fmt, width, height, stride, _shmid, imgdata) -> None:
        self._fb = (int(imgdata), stride, width, height)
        self._image = QImage(width, height, QImage.Format.Format_RGB32)
        self._image.fill(0xFF000000)
        self._copy_rect(0, 0, width, height)
        if not self._active:
            self._active = True
            self.state_changed.emit("connected")
        self.updateGeometry()
        self.update()

    def _on_primary_gone(self, _channel) -> None:
        self._fb = None

    def _on_mark(self, _channel, _mark) -> None:
        self.update()

    def _copy_rect(self, x: int, y: int, w: int, h: int) -> None:
        if self._fb is None or self._image is None:
            return
        addr, stride, fb_w, fb_h = self._fb
        x, y = max(0, x), max(0, y)
        w = min(w, fb_w - x)
        h = min(h, fb_h - y)
        if w <= 0 or h <= 0:
            return
        img_stride = self._image.bytesPerLine()
        # memoryviews on both sides: slicing one costs a pointer and a length,
        # and the assignment is a memcpy. This used to go through
        # bytes(ctypes_array[...]), which builds a Python int per byte first -
        # eight million of them for a 1080p frame, 146 ms, which capped the
        # console at about seven frames a second however fast the guest drew.
        dst = memoryview(self._image.bits()).cast("B")
        src = memoryview(
            (ctypes.c_ubyte * (stride * fb_h)).from_address(addr)
        ).cast("B")
        row_bytes = w * 4
        if x == 0 and row_bytes == stride == img_stride:
            # a full-width block is contiguous in both: one copy, not h of them
            s0, d0, n = y * stride, y * img_stride, h * stride
            dst[d0 : d0 + n] = src[s0 : s0 + n]
            return
        for row in range(h):
            s0 = (y + row) * stride + x * 4
            d0 = (y + row) * img_stride + x * 4
            dst[d0 : d0 + row_bytes] = src[s0 : s0 + row_bytes]

    def _on_invalidate(self, _channel, x, y, w, h) -> None:
        self._copy_rect(x, y, w, h)
        rect = self._display_rect()
        if self._image is not None and rect.width() > 0:
            sx = rect.width() / self._image.width()
            sy = rect.height() / self._image.height()
            self.update(
                QRect(
                    int(rect.x() + x * sx) - 1, int(rect.y() + y * sy) - 1,
                    int(w * sx) + 3, int(h * sy) + 3,
                )
            )

    # -- painting (same look as the VNC widget)

    def sizeHint(self):  # noqa: N802 - Qt override
        from PySide6.QtCore import QSize

        if self._image is not None:
            return QSize(self._image.width(), self._image.height())
        return QSize(640, 480)

    def _guest_size(self) -> tuple[int, int]:
        """The guest's current resolution, in guest pixels.

        Taken from the display-primary-create signal, which is authoritative
        and fires again on every mode change. Do NOT use
        `display_get_primary()`: it is deprecated and its struct marshals
        wrongly through PyGObject: the fields come back shifted (width
        yields the height, height yields the stride), which silently skews
        every pointer coordinate.
        """
        if self._fb is not None:
            _addr, _stride, width, height = self._fb
            return width, height
        if self._image is not None:
            return self._image.width(), self._image.height()
        return 0, 0

    def _scaling_mode(self) -> str:
        """Preference: always scale, never, or only when fullscreen."""
        try:
            from ..pages.settings import console_scaling

            return console_scaling()
        except Exception:  # noqa: BLE001 - preferences are optional
            return "always"

    def _display_rect(self) -> QRect:
        """Where the guest image is painted, honouring the scaling preference.

        Everything that maps between widget and guest coordinates goes through
        here, so the pointer stays correct in every mode - including "never",
        where the image sits at 1:1 and is cropped rather than shrunk.
        """
        iw, ih = self._guest_size()
        if not iw or not ih:
            return QRect()
        mode = self._scaling_mode()
        allow = mode == "always" or (mode == "fullscreen" and self._is_fullscreen())
        scale = min(self.width() / iw, self.height() / ih) if allow else 1.0
        w, h = max(1, int(iw * scale)), max(1, int(ih * scale))
        return QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h)

    def _is_fullscreen(self) -> bool:
        window = self.window()
        return bool(window and window.isFullScreen())

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._image is not None and self._active:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(self._display_rect(), self._image)
        else:
            p.setPen(Qt.GlobalColor.darkGray)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "no display connected")
        if self._active and self.hasFocus():
            pen = p.pen()
            pen.setColor(theme.ACCENT)
            p.setPen(pen)
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    # -- input

    def _fb_pos(self, pos) -> QPoint | None:
        """Widget point -> guest pixel, undoing the letterbox and the scale."""
        rect = self._display_rect()
        gw, gh = self._guest_size()
        if not gw or not gh or rect.width() == 0 or rect.height() == 0:
            return None
        fx = (pos.x() - rect.x()) * gw / rect.width()
        fy = (pos.y() - rect.y()) * gh / rect.height()
        return QPoint(
            max(0, min(gw - 1, int(fx))),
            max(0, min(gh - 1, int(fy))),
        )

    _BUTTON_NUM = {
        Qt.MouseButton.LeftButton: 1,
        Qt.MouseButton.MiddleButton: 2,
        Qt.MouseButton.RightButton: 3,
    }
    _BUTTON_MASK = {
        Qt.MouseButton.LeftButton: 1,
        Qt.MouseButton.MiddleButton: 2,
        Qt.MouseButton.RightButton: 4,
    }

    # Relative ("server") mouse mode has no shared coordinate space: the guest
    # applies its own pointer acceleration to our deltas, so the two cursors
    # drift apart and the real pointer eventually leaves the window, stranding
    # the guest cursor. The fix is the same one every remote viewer uses:
    # capture the pointer: hide it, keep warping it back to the widget centre,
    # and send the offset from centre as the delta. Ctrl+Alt releases.

    def _capture_pointer(self) -> None:
        if self._captured or not self._active:
            return
        self._captured = True
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.grabMouse()
        self.take_input()  # a captured pointer without the keyboard is half a grab
        self._warp_to_centre()
        self.capture_changed.emit(True)

    def release_pointer(self) -> None:
        if not self._captured:
            return
        self._captured = False
        self.releaseMouse()
        self.unsetCursor()
        self._motion_remainder = [0.0, 0.0]
        self.capture_changed.emit(False)

    # -- input grab
    #
    # The pointer and the keyboard are grabbed separately: a guest with a tablet
    # gives the pointer a shared coordinate space and needs no mouse grab at
    # all, but its keyboard still has Alt+Tab and Super taken out of it before
    # it arrives. Both are given back by the same combination.

    def take_input(self) -> None:
        """Everything the keyboard does goes to the guest from here."""
        if self._active:
            self.grab.take()

    def release_input(self) -> None:
        if not self.grab.held:
            return
        self._release_held_keys()
        self.grab.release()

    def release_all(self) -> None:
        self.release_pointer()
        self.release_input()

    @property
    def captured(self) -> bool:
        return self._captured

    def _centre_global(self):
        from PySide6.QtCore import QPoint as _QPoint

        return self.mapToGlobal(_QPoint(self.width() // 2, self.height() // 2))

    def _warp_to_centre(self) -> None:
        from PySide6.QtGui import QCursor

        self._warping = True
        QCursor.setPos(self._centre_global())

    def _guest_scale(self) -> tuple[float, float]:
        """Guest pixels per widget pixel, so motion feels 1:1 on screen."""
        rect = self._display_rect()
        gw, gh = self._guest_size()
        if not gw or rect.width() == 0 or rect.height() == 0:
            return 1.0, 1.0
        return gw / rect.width(), gh / rect.height()

    def _send_relative(self, global_pos) -> None:
        """Delta from the widget centre, scaled into guest pixels."""
        if self._inputs is None:
            return
        centre = self._centre_global()
        sx, sy = self._guest_scale()
        self._motion_remainder[0] += (global_pos.x() - centre.x()) * sx
        self._motion_remainder[1] += (global_pos.y() - centre.y()) * sy
        dx = int(self._motion_remainder[0])
        dy = int(self._motion_remainder[1])
        self._motion_remainder[0] -= dx
        self._motion_remainder[1] -= dy
        if dx or dy:
            try:
                self._inputs.motion(dx, dy, self._buttons_state)
            except Exception:  # noqa: BLE001 - channel may be mid-teardown
                pass
        self._warp_to_centre()

    def _send_position(self, pos) -> None:
        if self._inputs is None or self._mouse_mode == self.MOUSE_SERVER:
            return
        fb = self._fb_pos(pos)
        if fb is None:
            return
        try:
            self._inputs.position(fb.x(), fb.y(), 0, self._buttons_state)
        except Exception:  # noqa: BLE001 - channel may be mid-teardown
            pass

    def mousePressEvent(self, event) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if self._mouse_mode == self.MOUSE_SERVER and not self._captured:
            self._capture_pointer()  # first click grabs; no click sent through
            return
        # Absolute pointer: no mouse grab needed, but clicking in is still the
        # moment the keyboard should start belonging to the guest.
        if grab_on_click():
            self.take_input()
        self._buttons_state |= self._BUTTON_MASK.get(event.button(), 0)
        self._send_position(event.position())
        if self._inputs is not None and event.button() in self._BUTTON_NUM:
            self._inputs.button_press(self._BUTTON_NUM[event.button()], self._buttons_state)

    def mouseReleaseEvent(self, event) -> None:
        self._buttons_state &= ~self._BUTTON_MASK.get(event.button(), 0)
        if self._inputs is not None and event.button() in self._BUTTON_NUM:
            self._inputs.button_release(self._BUTTON_NUM[event.button()], self._buttons_state)

    def mouseMoveEvent(self, event) -> None:
        if self._captured:
            if self._warping:
                self._warping = False  # ignore the move our own warp caused
                return
            self._send_relative(event.globalPosition().toPoint())
            return
        self._send_position(event.position())

    def leaveEvent(self, event) -> None:
        self._motion_remainder = [0.0, 0.0]
        super().leaveEvent(event)

    def focusOutEvent(self, event) -> None:
        self.release_all()
        super().focusOutEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self.release_all()
        super().hideEvent(event)

    # -- guest resolution
    #
    # Note we do not push the widget size to the guest on resize.
    # Retargeting the guest's mode from under the user changes what they see
    # and briefly desyncs pointer mapping; virt-manager keeps this behind an
    # explicit "auto resize" toggle for the same reason. Our display scales
    # to fit instead, and the pointer transform follows the guest's own size.

    def resizeEvent(self, event) -> None:
        """Optionally ask the guest to match the window.

        Off by default: retargeting the guest's mode from under the user is
        surprising, and it briefly desyncs pointer mapping. The preference
        exists because it is handy on a guest with a working agent.
        """
        super().resizeEvent(event)
        if not self._active:
            return
        try:
            from ..pages.settings import console_resize_guest

            wanted = console_resize_guest()
        except Exception:  # noqa: BLE001 - preferences are optional
            wanted = False
        if wanted:
            self._resize_timer.start()

    def request_guest_resolution(self, width: int, height: int) -> bool:
        """Ask the guest agent for a resolution. Returns False without one."""
        if self._main is None or not self._active:
            return False
        try:
            if not self._main.props.agent_connected:
                return False
            # The display has to be enabled before the config goes out - it
            # is what spice-gtk does, and without it the agent takes the
            # monitor config and does nothing with it. Verified against a
            # live guest: with this line it moves, without it it does not.
            self._main.update_display_enabled(0, True, True)
            self._main.update_display(0, 0, 0, width, height, True)
            self._main.send_monitor_config()
            return True
        except Exception:  # noqa: BLE001 - best effort
            return False

    def mouseDoubleClickEvent(self, event) -> None:
        self.mousePressEvent(event)

    def wheelEvent(self, event) -> None:
        if self._inputs is None:
            return
        delta = event.angleDelta().y() or event.angleDelta().x()
        button = 4 if delta > 0 else 5  # SPICE up / down
        self._inputs.button_press(button, self._buttons_state)
        self._inputs.button_release(button, self._buttons_state)

    def _scancode(self, event) -> int:
        native = event.nativeScanCode()
        if native >= 8:  # xkb keycode = evdev + 8
            evdev = native - 8
            if evdev in _EVDEV_TO_XT:
                return _EVDEV_TO_XT[evdev]
            if evdev <= 88:
                return evdev
        return _QTKEY_TO_XT.get(event.key(), 0)

    def keyPressEvent(self, event) -> None:
        code = self._scancode(event)
        self._held[event.key()] = code
        # The one combination the guest never sees: it is how you get your
        # pointer and your desktop's own shortcuts back.
        if (self._captured or self.grab.held) and release_combo() <= set(self._held):
            self.release_all()
            return
        if code and self._inputs is not None:
            self._inputs.key_press(code)

    def keyReleaseEvent(self, event) -> None:
        code = self._held.pop(event.key(), None) or self._scancode(event)
        if code and self._inputs is not None:
            self._inputs.key_release(code)

    def _release_held_keys(self) -> None:
        """Let go of everything we sent down, so nothing sticks in the guest."""
        if self._inputs is not None:
            for code in self._held.values():
                if code:
                    try:
                        self._inputs.key_release(code)
                    except Exception:  # noqa: BLE001
                        pass
        self._held.clear()

    def send_scancodes(self, codes: list[int]) -> None:
        if self._inputs is None:
            return
        for c in codes:
            self._inputs.key_press(c)
        for c in reversed(codes):
            self._inputs.key_release(c)

    def focusNextPrevChild(self, _next: bool) -> bool:
        return False

    # -- clipboard (needs spice-vdagent in the guest)

    def _host_clipboard_changed(self) -> None:
        """Copying on the host makes it available in the guest, unasked.

        Every other SPICE client does this, and without it Ctrl+C here and
        Ctrl+V in the guest quietly does nothing - the menu had to be used
        instead, which nobody thinks to look for.
        """
        if self._main is None or not self._active:
            return
        text = QApplication.clipboard().text()
        if not text or text == self._clipboard_in:
            return  # empty, or the guest's own clipboard coming back round
        if text.encode("utf-8") == self._clipboard_out:
            return  # already offered; the guest fetches it when it pastes
        self.send_clipboard(text)

    def send_clipboard(self, text: str) -> None:
        """Offer the host clipboard to the guest via the agent."""
        if self._main is None:
            return
        self._clipboard_out = text.encode("utf-8")
        try:
            self._main.clipboard_selection_grab(0, [VD_AGENT_CLIPBOARD_UTF8_TEXT])
        except Exception:  # noqa: BLE001
            pass

    def _on_clip_request(self, _channel, selection, ctype) -> bool:
        if ctype == VD_AGENT_CLIPBOARD_UTF8_TEXT and self._main is not None:
            self._main.clipboard_selection_notify(
                selection, ctype, self._clipboard_out
            )
        return True

    def _on_guest_clipboard(self, _channel, selection, ctype, data, size) -> None:
        if ctype == VD_AGENT_CLIPBOARD_UTF8_TEXT and data is not None:
            try:
                text = bytes(data[:size]).decode("utf-8", "replace")
                self._clipboard_in = text
                QApplication.clipboard().setText(text)
            except (TypeError, ValueError):
                pass
