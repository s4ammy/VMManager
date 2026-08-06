"""The guest's pointer, and asking the guest to resize.

Both were broken by trusting what PyGObject hands back. The cursor shape
struct marshals wrongly - the same trap already documented for
display_get_primary() - and reported a 32x32 Windows cursor as 8x9, which
is invisible. The signals carry the same values as plain ints, so they are
what this uses.
"""

from __future__ import annotations

import ctypes

import pytest
from PySide6.QtCore import Qt

from vmmanager.console.spice import SPICE_AVAILABLE, SpiceClient

pytestmark = pytest.mark.skipif(not SPICE_AVAILABLE, reason="needs spice-glib")


def _rgba(width: int, height: int):
    buf = (ctypes.c_ubyte * (width * height * 4))()
    for i in range(0, len(buf), 4):
        buf[i], buf[i + 1], buf[i + 2], buf[i + 3] = 0, 0, 255, 255  # opaque
    return buf


def test_a_guest_cursor_becomes_a_real_cursor_of_that_size(qapp):
    client = SpiceClient()
    buf = _rgba(32, 32)
    client._on_cursor_set(None, 32, 32, 4, 6, ctypes.addressof(buf))
    cursor = client.cursor()
    assert cursor.shape() == Qt.CursorShape.BitmapCursor
    assert cursor.pixmap().size().toTuple() == (32, 32), (
        "the size came from the mis-marshalled struct again"
    )
    assert (cursor.hotSpot().x(), cursor.hotSpot().y()) == (4, 6)


def test_no_shape_leaves_an_arrow_rather_than_nothing(qapp):
    """Blanking the pointer when we cannot draw the guest's own leaves the
    window with no cursor at all - which is what 'I cannot see my mouse'
    was."""
    client = SpiceClient()
    client._on_cursor_set(None, 0, 0, 0, 0, 0)
    assert client.cursor().shape() != Qt.CursorShape.BlankCursor
    client._on_cursor_set(None, 32, 32, 0, 0, None)
    assert client.cursor().shape() != Qt.CursorShape.BlankCursor


def test_the_guest_can_still_ask_for_no_pointer(qapp):
    """A full-screen game hides it deliberately; that must still work."""
    client = SpiceClient()
    client._on_cursor_hide(None)
    assert client.cursor().shape() == Qt.CursorShape.BlankCursor
    client._on_cursor_reset(None)
    assert client.cursor().shape() != Qt.CursorShape.BlankCursor


def test_a_captured_pointer_is_left_hidden(qapp):
    client = SpiceClient()
    client._captured = True
    client.setCursor(Qt.CursorShape.BlankCursor)
    buf = _rgba(32, 32)
    client._on_cursor_set(None, 32, 32, 0, 0, ctypes.addressof(buf))
    assert client.cursor().shape() == Qt.CursorShape.BlankCursor


# -- asking the guest to resize


class _FakeMain:
    class _Props:
        agent_connected = True

    def __init__(self) -> None:
        self.props = self._Props()
        self.calls: list[str] = []

    def update_display_enabled(self, *_a):
        self.calls.append("enable")

    def update_display(self, *_a):
        self.calls.append("size")

    def send_monitor_config(self):
        self.calls.append("send")


def test_the_display_is_enabled_before_the_config_is_sent(qapp):
    """Without the enable the agent takes the config and does nothing -
    checked against a live guest, which stayed at its old resolution."""
    client = SpiceClient()
    client._main = _FakeMain()
    client._active = True
    assert client.request_guest_resolution(1280, 800) is True
    assert client._main.calls == ["enable", "size", "send"]


def test_no_agent_means_no_resize(qapp):
    client = SpiceClient()
    main = _FakeMain()
    main.props.agent_connected = False
    client._main = main
    client._active = True
    assert client.request_guest_resolution(1280, 800) is False
    assert main.calls == []
