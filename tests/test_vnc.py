"""The VNC client: DES auth, the RFB handshake, and the rect decoders.

This is a from-scratch RFB 3.8 implementation, which makes it the most
error-prone code here and also the easiest to test: the state machine is fed
bytes, so a fake socket is enough to drive a whole session.

The DES vectors were generated with openssl's own implementation
(`openssl enc -des-ecb -nopad -provider legacy -K <bit-reversed password>`) and
checked against ours before being written down here.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from vmmanager.console.vnc import (
    ENC_COPYRECT,
    ENC_DESKTOP_SIZE,
    ENC_EXTENDED_DESKTOP_SIZE,
    ENC_RAW,
    ENC_ZLIB,
    VncClient,
    vnc_auth_response,
)

# (password, challenge hex, expected response hex) - independently verified
DES_VECTORS = [
    ("secret", "000102030405060708090a0b0c0d0e0f",
     "ee22539f33a5983ec12f9c2edbc995dd"),
    ("", "000102030405060708090a0b0c0d0e0f",
     "491e890de9ace932838a49792f2213f3"),
    ("longerthan8chars", "ffffffffffffffffffffffffffffffff",
     "5fa201caecb512385fa201caecb51238"),
    ("pa55w0rd", "00112233445566778899aabbccddeeff",
     "a1db04377261f77d5507f1896efb2ae3"),
]


@pytest.mark.parametrize("password,challenge,expected", DES_VECTORS)
def test_vnc_auth_matches_openssl(password, challenge, expected):
    assert vnc_auth_response(password, bytes.fromhex(challenge)).hex() == expected


def test_auth_response_is_always_16_bytes():
    """Two DES blocks, whatever the password."""
    for password in ("", "a", "12345678", "far longer than the eight used"):
        assert len(vnc_auth_response(password, bytes(16))) == 16


def test_only_the_first_eight_characters_of_the_password_count():
    """A VNC password is truncated to 8 bytes, which surprises people."""
    challenge = bytes(range(16))
    assert vnc_auth_response("12345678", challenge) == vnc_auth_response(
        "12345678ignored", challenge
    )


# ---------------------------------------------------------------- fake socket


class FakeSocket:
    """Enough QAbstractSocket surface for the client's state machine."""

    def __init__(self) -> None:
        self.sent = bytearray()
        self._incoming = bytearray()

    def blockSignals(self, _on) -> None:  # noqa: N802 - Qt naming
        pass

    # what the client calls
    def write(self, data) -> int:
        self.sent += bytes(data)
        return len(data)

    def readAll(self):  # noqa: N802 - Qt naming
        data, self._incoming = bytes(self._incoming), bytearray()
        return _Bytes(data)

    def close(self) -> None:
        pass

    def abort(self) -> None:
        pass

    def deleteLater(self) -> None:
        pass

    def state(self):
        return None


class _Bytes:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def data(self) -> bytes:
        return self._data


@pytest.fixture
def client(qapp):
    """A client wired to a fake socket, parked at the version handshake."""
    c = VncClient()
    sock = FakeSocket()
    c._sock = sock
    c._buf = bytearray()
    c._expect(12, c._on_version)
    return c, sock


def feed(client, sock, data: bytes) -> None:
    """Deliver bytes as if the socket had received them."""
    sock._incoming += data
    client._on_ready_read()


def handshake(client, sock, width=64, height=32, password="") -> None:
    """Drive a full RFB 3.8 no-auth (or VNC-auth) handshake."""
    client._password = password
    feed(client, sock, b"RFB 003.008\n")
    if password:
        feed(client, sock, bytes([1, 2]))            # one type: VNC auth
        feed(client, sock, bytes(range(16)))         # challenge
        feed(client, sock, struct.pack(">I", 0))     # auth OK
    else:
        feed(client, sock, bytes([1, 1]))            # one type: None
        feed(client, sock, struct.pack(">I", 0))     # auth OK
    name = b"probe"
    feed(client, sock, struct.pack(">HH", width, height) + bytes(16)
         + struct.pack(">I", len(name)) + name)


def collect_errors(client) -> list[str]:
    """Failures come through state_changed as "error: ..." rather than a signal
    of their own."""
    seen: list[str] = []
    client.state_changed.connect(
        lambda text: seen.append(text) if text.startswith("error:") else None
    )
    return seen


def test_version_reply_is_the_protocol_we_speak(client):
    c, sock = client
    feed(c, sock, b"RFB 003.008\n")
    assert bytes(sock.sent).startswith(b"RFB 003.008\n")


def test_an_old_server_gets_the_older_version(client):
    c, sock = client
    feed(c, sock, b"RFB 003.003\n")
    assert bytes(sock.sent).startswith(b"RFB 003.003\n")


def test_a_non_rfb_greeting_fails_rather_than_continuing(client):
    c, sock = client
    errors = collect_errors(c)
    feed(c, sock, b"HTTP/1.1 200\n")
    assert errors


def test_handshake_reaches_connected_and_sizes_the_framebuffer(client):
    c, sock = client
    states = []
    c.state_changed.connect(states.append)
    handshake(c, sock, width=80, height=25)
    assert "connected" in states
    assert c.active
    assert (c._image.width(), c._image.height()) == (80, 25)


def test_password_auth_sends_the_des_response(client):
    c, sock = client
    handshake(c, sock, password="secret")
    expected = vnc_auth_response("secret", bytes(range(16)))
    assert expected in bytes(sock.sent)
    assert c.active


def test_a_rejected_password_fails_with_the_servers_reason(client):
    c, sock = client
    errors = collect_errors(c)
    c._password = "wrong"
    feed(c, sock, b"RFB 003.008\n")
    feed(c, sock, bytes([1, 2]))
    feed(c, sock, bytes(range(16)))
    feed(c, sock, struct.pack(">I", 1))          # auth failed
    reason = b"bad password"
    feed(c, sock, struct.pack(">I", len(reason)) + reason)
    assert errors and "bad password" in errors[0]
    assert not c.active


def test_client_asks_for_the_encodings_it_can_decode(client):
    c, sock = client
    handshake(c, sock)
    sent = bytes(sock.sent)
    # SetEncodings: type 2, then a count, then that many 32-bit encodings
    start = sent.index(struct.pack(">BxH", 2, 5))
    body = sent[start + 4 : start + 4 + 20]
    offered = [struct.unpack(">i", body[i : i + 4])[0] for i in range(0, 20, 4)]
    assert set(offered) == {
        ENC_ZLIB, ENC_COPYRECT, ENC_RAW, ENC_DESKTOP_SIZE,
        ENC_EXTENDED_DESKTOP_SIZE,
    }


# ---------------------------------------------------------------- decoders


def framebuffer_update(rects: list[bytes]) -> bytes:
    return struct.pack(">BxH", 0, len(rects)) + b"".join(rects)


def raw_rect(x, y, w, h, colour=(0x20, 0x40, 0x60)) -> bytes:
    b, g, r = colour
    pixel = bytes([b, g, r, 0xFF])
    return struct.pack(">HHHHi", x, y, w, h, ENC_RAW) + pixel * (w * h)


def test_raw_rect_lands_in_the_framebuffer(client):
    c, sock = client
    handshake(c, sock, width=8, height=4)
    feed(c, sock, framebuffer_update([raw_rect(0, 0, 8, 4, (0x20, 0x40, 0x60))]))
    # BGRX in, so read back as RGB
    assert c._image.pixelColor(0, 0).getRgb()[:3] == (0x60, 0x40, 0x20)
    assert c._image.pixelColor(7, 3).getRgb()[:3] == (0x60, 0x40, 0x20)


def test_a_rect_only_paints_its_own_area(client):
    c, sock = client
    handshake(c, sock, width=8, height=4)
    feed(c, sock, framebuffer_update([raw_rect(0, 0, 8, 4, (0, 0, 0))]))
    feed(c, sock, framebuffer_update([raw_rect(2, 1, 2, 2, (0xFF, 0xFF, 0xFF))]))
    assert c._image.pixelColor(2, 1).getRgb()[:3] == (0xFF, 0xFF, 0xFF)
    assert c._image.pixelColor(0, 0).getRgb()[:3] == (0, 0, 0)
    assert c._image.pixelColor(4, 1).getRgb()[:3] == (0, 0, 0)


def test_zlib_rect_is_inflated(client):
    c, sock = client
    handshake(c, sock, width=4, height=2)
    pixels = bytes([0x11, 0x22, 0x33, 0xFF]) * 8
    body = zlib.compress(pixels)
    rect = (struct.pack(">HHHHi", 0, 0, 4, 2, ENC_ZLIB)
            + struct.pack(">I", len(body)) + body)
    feed(c, sock, framebuffer_update([rect]))
    assert c._image.pixelColor(0, 0).getRgb()[:3] == (0x33, 0x22, 0x11)
    assert c._image.pixelColor(3, 1).getRgb()[:3] == (0x33, 0x22, 0x11)


def test_copyrect_moves_pixels_within_the_framebuffer(client):
    """Source coordinates are deliberately non-zero and unequal, so a byte
    order or axis mix-up shows up instead of cancelling out."""
    c, sock = client
    handshake(c, sock, width=8, height=4)
    feed(c, sock, framebuffer_update([raw_rect(0, 0, 8, 4, (0, 0, 0))]))
    feed(c, sock, framebuffer_update([raw_rect(2, 1, 2, 2, (0x10, 0x20, 0x30))]))
    copy = struct.pack(">HHHHi", 5, 0, 2, 2, ENC_COPYRECT) + struct.pack(">HH", 2, 1)
    feed(c, sock, framebuffer_update([copy]))
    assert c._image.pixelColor(5, 0).getRgb()[:3] == (0x30, 0x20, 0x10)
    assert c._image.pixelColor(6, 1).getRgb()[:3] == (0x30, 0x20, 0x10)
    assert c._image.pixelColor(2, 1).getRgb()[:3] == (0x30, 0x20, 0x10)  # source kept
    assert c._image.pixelColor(0, 0).getRgb()[:3] == (0, 0, 0)           # untouched


def test_desktop_size_resizes_and_asks_for_a_full_update(client):
    c, sock = client
    handshake(c, sock, width=8, height=4)
    sock.sent.clear()
    resize = struct.pack(">HHHHi", 0, 0, 32, 16, ENC_DESKTOP_SIZE)
    feed(c, sock, framebuffer_update([resize]))
    assert (c._image.width(), c._image.height()) == (32, 16)
    # a non-incremental FramebufferUpdateRequest for the new size
    assert struct.pack(">BBHHHH", 3, 0, 0, 0, 32, 16) in bytes(sock.sent)


def test_an_unrequested_encoding_fails_loudly(client):
    """Better to say so than to silently misread the stream."""
    c, sock = client
    errors = collect_errors(c)
    handshake(c, sock, width=8, height=4)
    feed(c, sock, framebuffer_update([struct.pack(">HHHHi", 0, 0, 2, 2, 99)]))
    assert errors and "99" in errors[0]


def test_a_rect_wider_than_the_framebuffer_does_not_corrupt_memory(client):
    """A server can send a rect that overhangs; clip rather than overrun."""
    c, sock = client
    handshake(c, sock, width=4, height=2)
    feed(c, sock, framebuffer_update([raw_rect(2, 1, 4, 2, (0x99, 0x99, 0x99))]))
    assert c._image.pixelColor(3, 1).getRgb()[:3] == (0x99, 0x99, 0x99)


def test_partial_delivery_is_buffered_until_complete(client):
    """TCP splits messages anywhere; nothing may be decoded early."""
    c, sock = client
    handshake(c, sock, width=4, height=1)
    rect = framebuffer_update([raw_rect(0, 0, 4, 1, (0x77, 0x55, 0x33))])
    for i in range(0, len(rect), 3):
        feed(c, sock, rect[i : i + 3])
    assert c._image.pixelColor(0, 0).getRgb()[:3] == (0x33, 0x55, 0x77)


# ------------------------------------------------- guest resolution (ext size)
#
# Checked against QEMU 11 as well as here: it answers a SetDesktopSize with
# reason 1, result 4 - "forwarded to the guest" - and only sends the new size
# once the guest's driver has actually changed mode.


def ext_size_rect(reason, result, w, h, screens=((0, 0, 0, 0, 0, 0),)) -> bytes:
    """An ExtendedDesktopSize pseudo-rect. x/y carry the reason and result."""
    body = struct.pack(">Bxxx", len(screens))
    for ident, sx, sy, sw, sh, flags in screens:
        body += struct.pack(">IHHHHI", ident, sx, sy, sw or w, sh or h, flags)
    return struct.pack(">HHHHi", reason, result, w, h,
                       ENC_EXTENDED_DESKTOP_SIZE) + body


def test_the_server_offering_ext_size_is_what_allows_a_resize(client):
    """Before that rect arrives there is nothing to say the server takes one."""
    c, sock = client
    handshake(c, sock, width=8, height=4)
    assert not c.can_resize_guest
    assert c.request_guest_resolution(64, 48) is False

    feed(c, sock, framebuffer_update([ext_size_rect(0, 0, 8, 4)]))
    assert c.can_resize_guest
    assert c.request_guest_resolution(64, 48) is True


def test_a_resolution_request_is_a_well_formed_set_desktop_size(client):
    c, sock = client
    handshake(c, sock, width=8, height=4)
    feed(c, sock, framebuffer_update([
        ext_size_rect(0, 0, 8, 4, screens=((7, 0, 0, 8, 4, 0xABCD),))
    ]))
    sock.sent.clear()
    c.request_guest_resolution(640, 480)

    sent = bytes(sock.sent)
    kind, width, height, count = struct.unpack(">BxHHBx", sent[:8])
    ident, x, y, sw, sh, flags = struct.unpack(">IHHHHI", sent[8:24])
    assert (kind, width, height, count) == (251, 640, 480, 1)
    assert (x, y, sw, sh) == (0, 0, 640, 480)
    assert (ident, flags) == (7, 0xABCD), (
        "the screen's id and flags have to be echoed back, or the server has "
        "no idea which screen is being resized"
    )


def test_a_size_the_guest_took_resizes_the_framebuffer(client):
    c, sock = client
    handshake(c, sock, width=8, height=4)
    feed(c, sock, framebuffer_update([ext_size_rect(0, 0, 8, 4)]))
    sock.sent.clear()

    # the guest changed mode: reason 0, and the new size
    feed(c, sock, framebuffer_update([ext_size_rect(0, 0, 64, 48)]))
    assert (c._image.width(), c._image.height()) == (64, 48)
    assert struct.pack(">BBHHHH", 3, 0, 0, 0, 64, 48) in bytes(sock.sent)


def test_a_refused_request_leaves_the_framebuffer_alone(client):
    """Result 3 is "invalid screen layout": nothing moved, so nothing here may."""
    c, sock = client
    handshake(c, sock, width=8, height=4)
    feed(c, sock, framebuffer_update([ext_size_rect(0, 0, 8, 4)]))
    feed(c, sock, framebuffer_update([ext_size_rect(1, 3, 64, 48)]))
    assert (c._image.width(), c._image.height()) == (8, 4)


def test_the_same_size_again_does_not_ask_for_another_full_update(client):
    """QEMU repeats this rect on every full update.

    Answering each one with another full-update request is a loop that never
    stops asking - it saturated the connection and the CPU with it.
    """
    c, sock = client
    handshake(c, sock, width=8, height=4)
    feed(c, sock, framebuffer_update([ext_size_rect(0, 0, 8, 4)]))
    sock.sent.clear()
    feed(c, sock, framebuffer_update([ext_size_rect(0, 0, 8, 4)]))

    full = struct.pack(">BBHHHH", 3, 0, 0, 0, 8, 4)
    assert full not in bytes(sock.sent)
    incremental = struct.pack(">BBHHHH", 3, 1, 0, 0, 8, 4)
    assert incremental in bytes(sock.sent), "it should still ask for more of it"
