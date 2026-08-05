"""Pure-Qt VNC (RFB 3.8) client: no external viewer needed for the console.

Speaks Raw, CopyRect and Zlib encodings plus the DesktopSize pseudo-encoding,
which is exactly what QEMU's built-in VNC server negotiates. Parsing happens
on the UI thread off QTcpSocket.readyRead - rectangles arrive as zlib streams
and decompress in C, so even full-frame updates stay comfortably fast.
"""

from __future__ import annotations

import struct
import zlib

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtNetwork import QAbstractSocket, QLocalSocket, QTcpSocket
from PySide6.QtWidgets import QApplication, QWidget

from .. import theme

ENC_RAW = 0
ENC_COPYRECT = 1
ENC_ZLIB = 6
ENC_DESKTOP_SIZE = -223


# ---------------------------------------------------------------- DES (VNC auth)
# VNC authentication DES-encrypts a 16-byte challenge with the password as
# key, each key byte bit-reversed. Single-use, so a compact table version.

_PC1 = [56, 48, 40, 32, 24, 16, 8, 0, 57, 49, 41, 33, 25, 17,
        9, 1, 58, 50, 42, 34, 26, 18, 10, 2, 59, 51, 43, 35,
        62, 54, 46, 38, 30, 22, 14, 6, 61, 53, 45, 37, 29, 21,
        13, 5, 60, 52, 44, 36, 28, 20, 12, 4, 27, 19, 11, 3]
_PC2 = [13, 16, 10, 23, 0, 4, 2, 27, 14, 5, 20, 9,
        22, 18, 11, 3, 25, 7, 15, 6, 26, 19, 12, 1,
        40, 51, 30, 36, 46, 54, 29, 39, 50, 44, 32, 47,
        43, 48, 38, 55, 33, 52, 45, 41, 49, 35, 28, 31]
_SHIFTS = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]
_IP = [57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
       61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7,
       56, 48, 40, 32, 24, 16, 8, 0, 58, 50, 42, 34, 26, 18, 10, 2,
       60, 52, 44, 36, 28, 20, 12, 4, 62, 54, 46, 38, 30, 22, 14, 6]
_FP = [39, 7, 47, 15, 55, 23, 63, 31, 38, 6, 46, 14, 54, 22, 62, 30,
       37, 5, 45, 13, 53, 21, 61, 29, 36, 4, 44, 12, 52, 20, 60, 28,
       35, 3, 43, 11, 51, 19, 59, 27, 34, 2, 42, 10, 50, 18, 58, 26,
       33, 1, 41, 9, 49, 17, 57, 25, 32, 0, 40, 8, 48, 16, 56, 24]
_E = [31, 0, 1, 2, 3, 4, 3, 4, 5, 6, 7, 8, 7, 8, 9, 10,
      11, 12, 11, 12, 13, 14, 15, 16, 15, 16, 17, 18, 19, 20, 19, 20,
      21, 22, 23, 24, 23, 24, 25, 26, 27, 28, 27, 28, 29, 30, 31, 0]
_P = [15, 6, 19, 20, 28, 11, 27, 16, 0, 14, 22, 25, 4, 17, 30, 9,
      1, 7, 23, 13, 31, 26, 2, 8, 18, 12, 29, 5, 21, 10, 3, 24]
_SBOX = [
    [14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
     0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
     4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
     15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13],
    [15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
     3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5,
     0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
     13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9],
    [10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
     13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
     13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
     1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12],
    [7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
     13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
     10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
     3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14],
    [2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
     14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
     4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
     11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3],
    [12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
     10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
     9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
     4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13],
    [4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
     13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
     1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
     6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12],
    [13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
     1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
     7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
     2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11],
]


def _des_subkeys(key: bytes) -> list[list[int]]:
    kbits = [(key[i // 8] >> (7 - i % 8)) & 1 for i in range(64)]
    cd = [kbits[i] for i in _PC1]
    subkeys = []
    c, d = cd[:28], cd[28:]
    for shift in _SHIFTS:
        c = c[shift:] + c[:shift]
        d = d[shift:] + d[:shift]
        merged = c + d
        subkeys.append([merged[i] for i in _PC2])
    return subkeys


def _des_block(block: bytes, subkeys: list[list[int]]) -> bytes:
    bits = [(block[i // 8] >> (7 - i % 8)) & 1 for i in range(64)]
    bits = [bits[i] for i in _IP]
    left, right = bits[:32], bits[32:]
    for sk in subkeys:
        expanded = [right[i] ^ sk[j] for j, i in enumerate(_E)]
        out = []
        for box in range(8):
            chunk = expanded[box * 6 : box * 6 + 6]
            row = chunk[0] * 2 + chunk[5]
            col = chunk[1] * 8 + chunk[2] * 4 + chunk[3] * 2 + chunk[4]
            val = _SBOX[box][row * 16 + col]
            out += [(val >> 3) & 1, (val >> 2) & 1, (val >> 1) & 1, val & 1]
        f = [out[i] for i in _P]
        left, right = right, [left[i] ^ f[i] for i in range(32)]
    combined = right + left
    combined = [combined[i] for i in _FP]
    return bytes(
        sum(combined[i * 8 + j] << (7 - j) for j in range(8)) for i in range(8)
    )


def vnc_auth_response(password: str, challenge: bytes) -> bytes:
    """DES-encrypt the challenge with the bit-reversed password as key."""
    key = password.encode("latin-1", "replace")[:8].ljust(8, b"\0")
    reversed_key = bytes(int(f"{b:08b}"[::-1], 2) for b in key)
    subkeys = _des_subkeys(reversed_key)
    return b"".join(
        _des_block(challenge[i : i + 8], subkeys) for i in range(0, 16, 8)
    )


# ---------------------------------------------------------------- RFB client


KEYSYMS = {
    Qt.Key.Key_Backspace: 0xFF08, Qt.Key.Key_Tab: 0xFF09,
    Qt.Key.Key_Return: 0xFF0D, Qt.Key.Key_Enter: 0xFF8D,
    Qt.Key.Key_Escape: 0xFF1B, Qt.Key.Key_Insert: 0xFF63,
    Qt.Key.Key_Delete: 0xFFFF, Qt.Key.Key_Home: 0xFF50,
    Qt.Key.Key_End: 0xFF57, Qt.Key.Key_PageUp: 0xFF55,
    Qt.Key.Key_PageDown: 0xFF56, Qt.Key.Key_Left: 0xFF51,
    Qt.Key.Key_Up: 0xFF52, Qt.Key.Key_Right: 0xFF53,
    Qt.Key.Key_Down: 0xFF54, Qt.Key.Key_F1: 0xFFBE,
    Qt.Key.Key_F2: 0xFFBF, Qt.Key.Key_F3: 0xFFC0, Qt.Key.Key_F4: 0xFFC1,
    Qt.Key.Key_F5: 0xFFC2, Qt.Key.Key_F6: 0xFFC3, Qt.Key.Key_F7: 0xFFC4,
    Qt.Key.Key_F8: 0xFFC5, Qt.Key.Key_F9: 0xFFC6, Qt.Key.Key_F10: 0xFFC7,
    Qt.Key.Key_F11: 0xFFC8, Qt.Key.Key_F12: 0xFFC9,
    Qt.Key.Key_Shift: 0xFFE1, Qt.Key.Key_Control: 0xFFE3,
    Qt.Key.Key_Alt: 0xFFE9, Qt.Key.Key_AltGr: 0xFFEA,
    Qt.Key.Key_Meta: 0xFFEB, Qt.Key.Key_Super_L: 0xFFEB,
    Qt.Key.Key_CapsLock: 0xFFE5, Qt.Key.Key_NumLock: 0xFF7F,
    Qt.Key.Key_Print: 0xFF61, Qt.Key.Key_Pause: 0xFF13,
    Qt.Key.Key_Menu: 0xFF67,
}


def qt_key_to_keysym(event) -> int:
    key = event.key()
    if key in KEYSYMS:
        return KEYSYMS[key]
    text = event.text()
    mods = event.modifiers()
    if text and not (mods & Qt.KeyboardModifier.ControlModifier):
        cp = ord(text[0])
        if 0x20 <= cp != 0x7F:
            return cp if cp < 0x100 else 0x01000000 + cp
    # ctrl-chords and anything without text: derive from the Qt key code
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        return key - Qt.Key.Key_A + ord("a")
    if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return key - Qt.Key.Key_0 + ord("0")
    if 0x20 <= key < 0x100:
        return key
    return 0


class VncClient(QWidget):
    """Interactive VNC display widget: connect(), then it just works.

    The widget is also the protocol client - socket callbacks, framebuffer
    and painting all live together, scaled to fit while keeping aspect.
    """

    state_changed = Signal(str)  # "connecting" | "connected" | "closed" | error text
    password_required = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._sock: QTcpSocket | QLocalSocket | None = None
        self._buf = bytearray()
        self._need = 0
        self._handler = None
        self._image: QImage | None = None
        self._zlib = None
        self._password = ""
        self._minor = 8
        self._buttons = 0
        self._active = False
        self._rects_left = 0
        self._rect_head: tuple[int, int, int, int, int] | None = None
        self._server_name = ""

    # -- lifecycle

    def open_tcp(self, host: str, port: int, password: str = "") -> None:
        self.close_connection()
        self._password = password
        sock = QTcpSocket(self)
        self._sock = sock
        sock.readyRead.connect(self._on_ready_read)
        sock.errorOccurred.connect(
            lambda _e: self._fail(sock.errorString())
        )
        sock.disconnected.connect(self._on_disconnected)
        self._expect(12, self._on_version)
        self.state_changed.emit("connecting")
        sock.connectToHost(host, port)

    def open_unix(self, path: str, password: str = "") -> None:
        self.close_connection()
        self._password = password
        sock = QLocalSocket(self)
        self._sock = sock
        sock.readyRead.connect(self._on_ready_read)
        sock.errorOccurred.connect(lambda _e: self._fail(sock.errorString()))
        sock.disconnected.connect(self._on_disconnected)
        self._expect(12, self._on_version)
        self.state_changed.emit("connecting")
        sock.connectToServer(path)

    def close_connection(self) -> None:
        if self._sock is not None:
            self._sock.blockSignals(True)
            self._sock.abort() if isinstance(self._sock, QTcpSocket) else self._sock.abort()
            self._sock.deleteLater()
            self._sock = None
        self._buf.clear()
        self._zlib = None
        self._active = False
        self._rects_left = 0
        self._rect_head = None
        self.update()

    @property
    def active(self) -> bool:
        return self._active

    def _fail(self, message: str) -> None:
        if self._sock is None:
            return
        self.close_connection()
        self.state_changed.emit(f"error: {message}")

    def _on_disconnected(self) -> None:
        if self._sock is None:
            return
        self.close_connection()
        self.state_changed.emit("closed")

    # -- byte pump

    def _expect(self, n: int, handler) -> None:
        self._need = n
        self._handler = handler

    def _on_ready_read(self) -> None:
        if self._sock is None:
            return
        self._buf += bytes(self._sock.readAll().data())
        while self._sock is not None and self._handler and len(self._buf) >= self._need:
            chunk = bytes(self._buf[: self._need])
            del self._buf[: self._need]
            self._handler(chunk)

    def _send(self, data: bytes) -> None:
        if self._sock is not None:
            self._sock.write(data)

    # -- handshake

    def _on_version(self, data: bytes) -> None:
        if not data.startswith(b"RFB "):
            self._fail("not an RFB server")
            return
        try:
            self._minor = int(data[8:11])
        except ValueError:
            self._minor = 8
        if self._minor >= 8:
            self._send(b"RFB 003.008\n")
            self._expect(1, self._on_sec_count)
        else:
            self._send(b"RFB 003.003\n")
            self._expect(4, self._on_sec33)

    def _on_sec33(self, data: bytes) -> None:
        (sectype,) = struct.unpack(">I", data)
        if sectype == 1:
            self._client_init()
        elif sectype == 2:
            self._expect(16, self._on_challenge_33)
        else:
            self._fail("server refused connection")

    def _on_challenge_33(self, data: bytes) -> None:
        self._answer_challenge(data, result_then_init=True)

    def _on_sec_count(self, data: bytes) -> None:
        count = data[0]
        if count == 0:
            self._expect(4, self._on_reason_len)
            return
        self._expect(count, self._on_sec_types)

    def _on_reason_len(self, data: bytes) -> None:
        (n,) = struct.unpack(">I", data)
        self._expect(n, lambda reason: self._fail(reason.decode("latin-1", "replace")))

    def _on_sec_types(self, data: bytes) -> None:
        types = set(data)
        if 1 in types:
            self._send(bytes([1]))
            self._expect(4, self._on_sec_result)
        elif 2 in types:
            if not self._password:
                self.close_connection()
                self.password_required.emit()
                return
            self._send(bytes([2]))
            self._expect(16, lambda ch: self._answer_challenge(ch))
        else:
            self._fail(
                "server offers no supported auth (needs None or VNC password)"
            )

    def _answer_challenge(self, challenge: bytes, result_then_init: bool = False) -> None:
        self._send(vnc_auth_response(self._password, challenge))
        self._expect(4, self._on_sec_result)

    def _on_sec_result(self, data: bytes) -> None:
        (result,) = struct.unpack(">I", data)
        if result != 0:
            if self._minor >= 8:
                self._expect(4, self._on_reason_len)
            else:
                self._fail("authentication failed")
            return
        self._client_init()

    def _client_init(self) -> None:
        self._send(bytes([1]))  # shared
        self._expect(24, self._on_server_init)

    def _on_server_init(self, data: bytes) -> None:
        w, h = struct.unpack(">HH", data[:4])
        (name_len,) = struct.unpack(">I", data[20:24])
        self._resize_fb(w, h)

        def got_name(name: bytes) -> None:
            self._server_name = name.decode("utf-8", "replace")
            self._start_session()

        if name_len:
            self._expect(name_len, got_name)
        else:
            self._start_session()

    def _start_session(self) -> None:
        # 32bpp little-endian BGRX == QImage.Format_RGB32 memory layout
        pixfmt = struct.pack(
            ">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0
        )
        self._send(struct.pack(">Bxxx", 0) + pixfmt)  # SetPixelFormat
        encodings = [ENC_ZLIB, ENC_COPYRECT, ENC_RAW, ENC_DESKTOP_SIZE]
        self._send(
            struct.pack(">BxH", 2, len(encodings))
            + b"".join(struct.pack(">i", e) for e in encodings)
        )
        self._zlib = zlib.decompressobj()
        self._active = True
        self.state_changed.emit("connected")
        self._request_update(incremental=False)
        self._expect(1, self._on_message_type)

    # -- server messages

    def _request_update(self, incremental: bool = True) -> None:
        if self._image is None:
            return
        self._send(
            struct.pack(
                ">BBHHHH", 3, 1 if incremental else 0,
                0, 0, self._image.width(), self._image.height(),
            )
        )

    def _on_message_type(self, data: bytes) -> None:
        mtype = data[0]
        if mtype == 0:
            self._expect(3, self._on_update_head)
        elif mtype == 1:  # SetColourMapEntries - read and drop
            self._expect(5, self._on_colormap_head)
        elif mtype == 2:  # Bell
            self._expect(1, self._on_message_type)
            QApplication.beep()
        elif mtype == 3:
            self._expect(7, self._on_cuttext_head)
        else:
            self._fail(f"unexpected server message {mtype}")

    def _on_colormap_head(self, data: bytes) -> None:
        (_first, n) = struct.unpack(">xHH", data)
        self._expect(n * 6, lambda _d: self._expect(1, self._on_message_type))

    def _on_cuttext_head(self, data: bytes) -> None:
        (n,) = struct.unpack(">3xI", data)

        def got(text: bytes) -> None:
            QApplication.clipboard().setText(text.decode("latin-1", "replace"))
            self._expect(1, self._on_message_type)

        self._expect(n, got)

    def _on_update_head(self, data: bytes) -> None:
        (self._rects_left,) = struct.unpack(">xH", data)
        self._next_rect()

    def _next_rect(self) -> None:
        if self._rects_left <= 0:
            self.update()
            self._request_update()
            self._expect(1, self._on_message_type)
            return
        self._rects_left -= 1
        self._expect(12, self._on_rect_head)

    def _on_rect_head(self, data: bytes) -> None:
        x, y, w, h, enc = struct.unpack(">HHHHi", data)
        self._rect_head = (x, y, w, h, enc)
        if enc == ENC_RAW:
            self._expect(w * h * 4, self._on_raw_rect)
        elif enc == ENC_COPYRECT:
            self._expect(4, self._on_copyrect)
        elif enc == ENC_ZLIB:
            self._expect(4, self._on_zlib_len)
        elif enc == ENC_DESKTOP_SIZE:
            self._resize_fb(w, h)
            self._request_update(incremental=False)
            self._next_rect()
        else:
            self._fail(f"server sent unrequested encoding {enc}")

    def _resize_fb(self, w: int, h: int) -> None:
        old = self._image
        self._image = QImage(max(w, 1), max(h, 1), QImage.Format.Format_RGB32)
        self._image.fill(0xFF000000)
        if old is not None:
            p = QPainter(self._image)
            p.drawImage(0, 0, old)
            p.end()
        self.updateGeometry()
        self.update()

    def _blit(self, x: int, y: int, w: int, h: int, pixels: bytes) -> None:
        img = self._image
        if img is None:
            return
        stride = img.bytesPerLine()
        mv = img.bits()
        row_bytes = w * 4
        for row in range(h):
            if y + row >= img.height():
                break
            start = (y + row) * stride + x * 4
            end = min(start + row_bytes, (y + row) * stride + img.width() * 4)
            mv[start:end] = pixels[row * row_bytes : row * row_bytes + (end - start)]

    def _on_raw_rect(self, data: bytes) -> None:
        x, y, w, h, _ = self._rect_head
        self._blit(x, y, w, h, data)
        self._next_rect()

    def _on_copyrect(self, data: bytes) -> None:
        sx, sy = struct.unpack(">HH", data)
        x, y, w, h, _ = self._rect_head
        if self._image is not None:
            region = self._image.copy(QRect(sx, sy, w, h))
            p = QPainter(self._image)
            p.drawImage(x, y, region)
            p.end()
        self._next_rect()

    def _on_zlib_len(self, data: bytes) -> None:
        (n,) = struct.unpack(">I", data)
        self._expect(n, self._on_zlib_data)

    def _on_zlib_data(self, data: bytes) -> None:
        x, y, w, h, _ = self._rect_head
        try:
            pixels = self._zlib.decompress(data)
        except zlib.error as e:
            self._fail(f"zlib stream broke: {e}")
            return
        self._blit(x, y, w, h, pixels)
        self._next_rect()

    # -- painting

    def sizeHint(self):  # noqa: N802 - Qt override
        from PySide6.QtCore import QSize

        if self._image is not None:
            return QSize(self._image.width(), self._image.height())
        return QSize(640, 480)

    def _scaling_mode(self) -> str:
        """Preference: always scale, never, or only when fullscreen."""
        try:
            from ..pages.settings import console_scaling

            return console_scaling()
        except Exception:  # noqa: BLE001 - preferences are optional
            return "always"

    def _display_rect(self) -> QRect:
        """Where the framebuffer is painted, honouring the scaling preference."""
        if self._image is None:
            return QRect()
        iw, ih = self._image.width(), self._image.height()
        mode = self._scaling_mode()
        window = self.window()
        fullscreen = bool(window and window.isFullScreen())
        allow = mode == "always" or (mode == "fullscreen" and fullscreen)
        scale = min(self.width() / iw, self.height() / ih) if allow else 1.0
        w, h = max(1, int(iw * scale)), max(1, int(ih * scale))
        return QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._image is not None and self._active:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(self._display_rect(), self._image)
        else:
            p.setPen(Qt.GlobalColor.darkGray)
            p.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "no display connected"
            )
        if self._active and self.hasFocus():
            pen = p.pen()
            pen.setColor(theme.ACCENT)
            p.setPen(pen)
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    # -- input

    def _fb_pos(self, pos) -> QPoint | None:
        rect = self._display_rect()
        if self._image is None or rect.width() == 0 or rect.height() == 0:
            return None
        fx = (pos.x() - rect.x()) * self._image.width() / rect.width()
        fy = (pos.y() - rect.y()) * self._image.height() / rect.height()
        return QPoint(
            max(0, min(self._image.width() - 1, int(fx))),
            max(0, min(self._image.height() - 1, int(fy))),
        )

    def _send_pointer(self, pos) -> None:
        fb = self._fb_pos(pos)
        if fb is None or not self._active:
            return
        self._send(struct.pack(">BBHH", 5, self._buttons, fb.x(), fb.y()))

    _BUTTON_BITS = {
        Qt.MouseButton.LeftButton: 1,
        Qt.MouseButton.MiddleButton: 2,
        Qt.MouseButton.RightButton: 4,
    }

    def mousePressEvent(self, event) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._buttons |= self._BUTTON_BITS.get(event.button(), 0)
        self._send_pointer(event.position())

    def mouseReleaseEvent(self, event) -> None:
        self._buttons &= ~self._BUTTON_BITS.get(event.button(), 0)
        self._send_pointer(event.position())

    def mouseMoveEvent(self, event) -> None:
        self._send_pointer(event.position())

    def mouseDoubleClickEvent(self, event) -> None:
        self.mousePressEvent(event)

    def wheelEvent(self, event) -> None:
        fb = self._fb_pos(event.position())
        if fb is None or not self._active:
            return
        delta = event.angleDelta().y() or event.angleDelta().x()
        bit = 8 if delta > 0 else 16
        self._send(struct.pack(">BBHH", 5, self._buttons | bit, fb.x(), fb.y()))
        self._send(struct.pack(">BBHH", 5, self._buttons, fb.x(), fb.y()))

    def _send_key(self, keysym: int, down: bool) -> None:
        if keysym and self._active:
            self._send(struct.pack(">BBxxI", 4, 1 if down else 0, keysym))

    def keyPressEvent(self, event) -> None:
        self._send_key(qt_key_to_keysym(event), True)

    def keyReleaseEvent(self, event) -> None:
        self._send_key(qt_key_to_keysym(event), False)

    def send_combo(self, keysyms: list[int]) -> None:
        for ks in keysyms:
            self._send_key(ks, True)
        for ks in reversed(keysyms):
            self._send_key(ks, False)

    def focusNextPrevChild(self, _next: bool) -> bool:
        return False  # keep Tab for the guest

    def set_password(self, password: str) -> None:
        self._password = password

    # -- clipboard / typing

    def send_clipboard(self, text: str) -> None:
        """ClientCutText - lands in the guest clipboard if it runs an agent."""
        if not self._active:
            return
        data = text.encode("latin-1", "replace")
        self._send(struct.pack(">BxxxI", 6, len(data)) + data)

    def type_text(self, text: str) -> None:
        """Send text as keystrokes - works everywhere, agents or not."""
        if not self._active:
            return
        for ch in text:
            if ch == "\n":
                keysym = 0xFF0D
            elif ch == "\t":
                keysym = 0xFF09
            else:
                cp = ord(ch)
                if cp < 0x20:
                    continue
                keysym = cp if cp < 0x100 else 0x01000000 + cp
            self._send_key(keysym, True)
            self._send_key(keysym, False)
