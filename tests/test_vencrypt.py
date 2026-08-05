"""VeNCrypt: the TLS path through the VNC handshake.

The negotiation is driven byte by byte like the rest of the RFB tests; the
socket is a real QSslSocket with its I/O overridden, so the client's
isinstance gate and signal wiring are the real thing. The TLS handshake
itself is OpenSSL's business - what is tested here is everything around
it: subtype choice, when TLS is preferred, and what happens on either
side of the tunnel coming up.
"""

from __future__ import annotations

import struct

import pytest
from PySide6.QtNetwork import QSslSocket

from vmmanager.console.vnc import VncClient, vnc_auth_response


class FakeSslSocket(QSslSocket):
    """Real QSslSocket type, fake wire."""

    def __init__(self) -> None:
        super().__init__()
        self.sent = bytearray()
        self._incoming = bytearray()
        self.encryption_started = False

    def write(self, data) -> int:  # noqa: A003
        self.sent += bytes(data)
        return len(bytes(data))

    def readAll(self):  # noqa: N802 - Qt naming
        data, self._incoming = bytes(self._incoming), bytearray()

        class _Bytes:
            def __init__(self, d): self._d = d
            def data(self): return self._d

        return _Bytes(data)

    def startClientEncryption(self) -> None:  # noqa: N802 - Qt naming
        self.encryption_started = True

    def abort(self) -> None:
        pass


@pytest.fixture
def client(qapp):
    c = VncClient()
    sock = FakeSslSocket()
    c._sock = sock
    c._buf = bytearray()
    c._expect(12, c._on_version)
    yield c, sock
    sock.deleteLater()


def feed(client, sock, data: bytes) -> None:
    sock._incoming += data
    client._on_ready_read()


def to_subtypes(client, sock, offered: list[int]) -> None:
    """Drive the handshake to the point of choosing a VeNCrypt subtype."""
    feed(client, sock, b"RFB 003.008\n")
    feed(client, sock, bytes([1, 19]))         # only VeNCrypt on offer
    assert bytes(sock.sent).endswith(bytes([19]))
    feed(client, sock, bytes([0, 2]))          # server: VeNCrypt 0.2
    assert bytes(sock.sent).endswith(bytes([0, 2]))
    feed(client, sock, bytes([0]))             # version accepted
    feed(client, sock, bytes([len(offered)])
         + b"".join(struct.pack(">I", s) for s in offered))


def test_tls_only_server_negotiates_vencrypt_even_with_the_option_off(client):
    c, sock = client
    to_subtypes(c, sock, [260])
    assert bytes(sock.sent).endswith(struct.pack(">I", 260))
    feed(c, sock, bytes([1]))                  # server: go ahead
    assert sock.encryption_started


def test_x509_none_wins_over_the_other_subtypes(client):
    c, sock = client
    to_subtypes(c, sock, [258, 261, 260, 257])
    assert bytes(sock.sent).endswith(struct.pack(">I", 260))


def test_plain_none_wins_while_the_tls_option_is_off(client, monkeypatch):
    import vmmanager.pages.settings as settings

    monkeypatch.setattr(settings, "console_tls", lambda: False)
    c, sock = client
    feed(c, sock, b"RFB 003.008\n")
    feed(c, sock, bytes([2, 1, 19]))           # None and VeNCrypt
    assert bytes(sock.sent).endswith(bytes([1]))


def test_the_tls_option_prefers_vencrypt_over_plain(client, monkeypatch):
    import vmmanager.pages.settings as settings

    monkeypatch.setattr(settings, "console_tls", lambda: True)
    c, sock = client
    feed(c, sock, b"RFB 003.008\n")
    feed(c, sock, bytes([2, 1, 19]))
    assert bytes(sock.sent).endswith(bytes([19]))


def test_auth_continues_inside_the_tunnel_for_the_vnc_subtype(client):
    c, sock = client
    c._password = "secret"
    to_subtypes(c, sock, [261])                # x509 + VNC password
    feed(c, sock, bytes([1]))
    assert sock.encryption_started
    c._on_tls_established()                    # as the encrypted signal would
    feed(c, sock, bytes(range(16)))            # challenge, now encrypted
    assert vnc_auth_response("secret", bytes(range(16))) in bytes(sock.sent)


def test_the_none_subtype_goes_straight_to_the_result(client):
    c, sock = client
    states = []
    c.state_changed.connect(states.append)
    to_subtypes(c, sock, [260])
    feed(c, sock, bytes([1]))
    c._on_tls_established()
    feed(c, sock, struct.pack(">I", 0))        # SecurityResult: ok
    name = b"tls-guest"
    feed(c, sock, struct.pack(">HH", 64, 32) + bytes(16)
         + struct.pack(">I", len(name)) + name)
    assert "connected" in states
    assert c.active


def test_a_missing_password_asks_rather_than_hanging(client):
    c, sock = client
    asked = []
    c.password_required.connect(lambda: asked.append(True))
    to_subtypes(c, sock, [258])                # tls + VNC password
    feed(c, sock, bytes([1]))
    c._on_tls_established()
    assert asked


def test_plaintext_only_subtypes_fail_with_a_sentence(client):
    c, sock = client
    errors = []
    c.state_changed.connect(
        lambda t: errors.append(t) if t.startswith("error:") else None
    )
    to_subtypes(c, sock, [256])                # Plain: password in the clear
    assert errors and "subtype" in errors[0]
