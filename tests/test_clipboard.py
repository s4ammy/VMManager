"""Host clipboard reaching the guest without anyone touching a menu.

The gap this covers: copying on the host used to do nothing until "Paste →
Into guest clipboard" was clicked, because nothing watched the host
clipboard. Ctrl+C here then Ctrl+V in the guest silently pasted whatever
was there before, which reads as "clipboard sharing is broken".
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from vmmanager.console.spice import SPICE_AVAILABLE, SpiceClient

pytestmark = pytest.mark.skipif(
    not SPICE_AVAILABLE, reason="needs spice-glib"
)


class _FakeMain:
    """The agent's main channel: records what was offered to the guest."""

    def __init__(self) -> None:
        self.grabs: list[list[int]] = []

    def clipboard_selection_grab(self, selection, types):
        self.grabs.append(list(types))


@pytest.fixture
def connected(qapp):
    """A client that believes it has an agent, without a guest behind it."""
    client = SpiceClient()
    client._main = _FakeMain()
    client._active = True
    yield client, client._main
    client._main = None
    client._active = False


def test_copying_on_the_host_offers_it_to_the_guest(connected, qapp):
    client, main = connected
    QApplication.clipboard().setText("from the host")
    qapp.processEvents()
    assert main.grabs, "the guest was never offered the host's clipboard"
    assert client._clipboard_out == b"from the host"


def test_the_guests_own_clipboard_is_not_sent_back_to_it(connected, qapp):
    """Guest copies, we put it on the host clipboard, that raises
    dataChanged - and offering it back would be a loop."""
    client, main = connected
    data = "from the guest".encode("utf-8")
    client._on_guest_clipboard(None, 0, 1, data, len(data))
    qapp.processEvents()
    assert QApplication.clipboard().text() == "from the guest"
    assert main.grabs == [], "the guest's own text was offered back to it"


def test_the_same_text_is_not_offered_twice(connected, qapp):
    client, main = connected
    QApplication.clipboard().setText("once")
    qapp.processEvents()
    QApplication.clipboard().dataChanged.emit()
    qapp.processEvents()
    assert len(main.grabs) == 1


def test_nothing_is_offered_while_disconnected(qapp):
    client = SpiceClient()
    client._main = _FakeMain()
    client._active = False          # console closed
    QApplication.clipboard().setText("while disconnected")
    qapp.processEvents()
    assert client._main.grabs == []


# -- the VNC client does the same, where the server will carry it


class _FakeVncSocket:
    def __init__(self) -> None:
        self.sent = bytearray()

    def write(self, data) -> int:
        self.sent += bytes(data)
        return len(bytes(data))

    def blockSignals(self, _on): pass
    def abort(self): pass
    def deleteLater(self): pass


def test_vnc_also_offers_the_host_clipboard_automatically(qapp):
    import struct

    from vmmanager.console.vnc import VncClient

    client = VncClient()
    sock = _FakeVncSocket()
    client._sock = sock
    client._active = True
    QApplication.clipboard().setText("typed on the host")
    qapp.processEvents()
    # ClientCutText is message type 6, then a 4-byte length
    assert bytes(sock.sent).startswith(struct.pack(">BxxxI", 6, 17))
    assert b"typed on the host" in bytes(sock.sent)


def test_vnc_does_not_send_the_guests_clipboard_back(qapp):
    from vmmanager.console.vnc import VncClient

    client = VncClient()
    sock = _FakeVncSocket()
    client._sock = sock
    client._active = True
    client._clipboard_in = "from the guest"
    QApplication.clipboard().setText("from the guest")
    qapp.processEvents()
    assert bytes(sock.sent) == b""
