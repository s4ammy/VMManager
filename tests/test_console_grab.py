"""Every key to the guest, until the release combination.

A console that only receives the keys the desktop did not want is not a console
you can work in: Alt+Tab, Super and this application's own shortcuts are taken
before the guest hears about them. Grabbing takes them back, and one
combination - Ctrl+Alt by default - gives them all up again.

The system-wide half of the grab (an X11 keyboard grab, or Wayland's
shortcut-inhibit protocol) is the compositor's decision and cannot be asserted
on from here; what is tested is everything this side of it, including the part
that stops Qt turning a key into a shortcut.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QWidget

from vmmanager.console.grab import COMBO_KEYS, InputGrab, release_combo
from vmmanager.console.vnc import VncClient


@pytest.fixture
def grabby(monkeypatch):
    """Auto-grab on, releasing with Ctrl+Alt, whatever the real settings say."""
    import vmmanager.console.grab as grab

    monkeypatch.setattr(grab, "grab_on_click", lambda: True)
    monkeypatch.setattr(grab, "release_combo_name", lambda: "Ctrl+Alt")
    import vmmanager.console.vnc as vnc

    monkeypatch.setattr(vnc, "grab_on_click", lambda: True)
    monkeypatch.setattr(vnc, "release_combo", lambda: COMBO_KEYS["Ctrl+Alt"])


@pytest.fixture
def connected(qapp, grabby):
    """A VNC client that believes it is connected, with a socket that records."""
    sent = []

    client = VncClient()
    client._active = True
    client._send = sent.append
    client.resize(200, 100)
    client.show()
    qapp.processEvents()
    yield client, sent
    client.release_input()
    client.close()


def press(widget, key: int, text: str = "") -> None:
    widget.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, text)
    )


def click(widget) -> None:
    widget.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(10, 10),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def test_clicking_the_display_takes_the_keyboard(connected):
    client, _sent = connected
    assert not client.grab.held
    click(client)
    assert client.grab.held


def test_the_release_combination_gives_it_back(connected):
    client, _sent = connected
    click(client)
    press(client, Qt.Key.Key_Control)
    press(client, Qt.Key.Key_Alt)
    assert not client.grab.held


def test_the_release_combination_is_not_sent_to_the_guest(connected):
    """It is the one thing the guest must not see, or it acts on half of it."""
    client, sent = connected
    click(client)
    sent.clear()
    press(client, Qt.Key.Key_Control)
    press(client, Qt.Key.Key_Alt)
    # Ctrl went down before the combination was complete, so it is sent and then
    # released; Alt, which completed it, is never pressed in the guest.
    downs = [m for m in sent if m[1] == 1]
    assert len(downs) == 1, "only the first half of the combination reaches it"


def test_letting_go_releases_the_keys_it_was_holding(connected):
    """Otherwise Ctrl stays down in the guest for good: its release event goes
    to whoever has the keyboard next, which is no longer us."""
    client, sent = connected
    click(client)
    press(client, Qt.Key.Key_Control)
    sent.clear()
    press(client, Qt.Key.Key_Alt)  # completes the combination

    ups = [m for m in sent if m[1] == 0]
    assert ups, "the held keys should be released as the grab ends"
    assert client._held == {}


def test_keys_still_reach_the_guest_without_a_grab(connected):
    """Not grabbing is not the same as not typing."""
    client, sent = connected
    press(client, Qt.Key.Key_A, "a")
    assert sent, "an ungrabbed console still sends what it is given"


def test_losing_focus_lets_go(connected, qapp):
    """A grab that outlives the window having focus is a locked-up desktop."""
    client, _sent = connected
    click(client)
    assert client.grab.held
    client.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert not client.grab.held


def test_closing_the_connection_lets_go(connected):
    client, _sent = connected
    click(client)
    client.close_connection()
    assert not client.grab.held


def test_a_grab_eats_the_shortcuts_that_would_have_taken_the_key(qapp, grabby):
    """The only thing that stops our own Ctrl+N, F5 and the rest.

    Qt asks the focus widget first, with a ShortcutOverride event, whether it
    wants the key itself. Accepting is what turns it back into a key press.
    """
    widget = QWidget()
    widget.show()
    qapp.processEvents()
    grab = InputGrab(widget)
    event = QKeyEvent(
        QEvent.Type.ShortcutOverride, Qt.Key.Key_N,
        Qt.KeyboardModifier.ControlModifier,
    )

    assert not grab.eventFilter(widget, event), "nothing is taken while released"
    assert not event.isAccepted()

    grab.take()
    try:
        assert grab.eventFilter(widget, event)
        assert event.isAccepted(), (
            "an ignored ShortcutOverride lets the shortcut fire anyway"
        )
    finally:
        grab.release()
        widget.close()


def test_the_release_combination_comes_from_settings(monkeypatch):
    import vmmanager.pages.settings as settings

    monkeypatch.setattr(settings, "console_release_keys", lambda: "Alt+Shift")
    assert release_combo() == COMBO_KEYS["Alt+Shift"]

    monkeypatch.setattr(settings, "console_release_keys", lambda: "nonsense")
    assert release_combo() == COMBO_KEYS["Ctrl+Alt"], "fall back, never to nothing"


# -- the SPICE client, which has a pointer capture as well
#
# spice-glib may not be installed, and none of this needs it: the grab is the
# widget's own, and a client with no channels simply sends nothing.


@pytest.fixture
def spice(qapp, grabby, monkeypatch):
    from vmmanager.console.spice import SpiceClient
    import vmmanager.console.spice as spice_module

    monkeypatch.setattr(spice_module, "grab_on_click", lambda: True)
    monkeypatch.setattr(
        spice_module, "release_combo", lambda: COMBO_KEYS["Ctrl+Alt"]
    )
    client = SpiceClient()
    client._active = True
    client.resize(200, 100)
    client.show()
    qapp.processEvents()
    yield client
    client.release_all()
    client.close()


def test_an_absolute_pointer_still_grabs_the_keyboard(spice):
    """A tablet means no mouse capture, which used to mean no grab either - so
    Alt+Tab never reached a guest that had its pointer working properly."""
    spice._mouse_mode = spice.MOUSE_CLIENT
    click(spice)
    assert spice.grab.held
    assert not spice.captured, "the pointer needs no capturing in absolute mode"


def test_capturing_the_pointer_takes_the_keyboard_with_it(spice):
    spice._mouse_mode = spice.MOUSE_SERVER
    click(spice)
    assert spice.captured
    assert spice.grab.held


def test_the_combination_releases_both(spice):
    spice._mouse_mode = spice.MOUSE_SERVER
    click(spice)
    press(spice, Qt.Key.Key_Control)
    press(spice, Qt.Key.Key_Alt)
    assert not spice.captured
    assert not spice.grab.held


# ----------------------------------------------------- what the desktop allows

def test_the_hint_says_which_keys_wayland_is_keeping(qapp, monkeypatch):
    """Not "except the ones your desktop keeps": which ones, and the fix.

    Qt's Wayland plugin never asks for shortcut inhibition, so the grab is
    always partial there and saying so vaguely leaves people thinking the
    console is broken.
    """
    from PySide6.QtWidgets import QLabel

    from vmmanager.console.grab import InputGrab

    monkeypatch.setattr(type(qapp), "platformName", lambda _self: "wayland")
    grab = InputGrab(QLabel())
    grab._system = False
    hint = grab.hint()
    assert "Super and Alt+Tab" in hint
    assert "QT_QPA_PLATFORM=xcb" in hint, "say what actually fixes it"


def test_a_granted_grab_does_not_apologise(qapp, monkeypatch):
    from PySide6.QtWidgets import QLabel

    from vmmanager.console.grab import InputGrab

    monkeypatch.setattr(type(qapp), "platformName", lambda _self: "wayland")
    grab = InputGrab(QLabel())
    grab._system = True
    assert grab.hint().startswith("every key goes to the guest")


def test_xwayland_is_only_forced_when_it_was_asked_for(monkeypatch, scratch_settings):
    """The platform is chosen once, before QApplication, so this runs early
    and has to be sure about it."""
    from vmmanager.__main__ import _honour_xwayland_preference
    from vmmanager.pages.settings import save_console_force_xwayland

    env = {"XDG_SESSION_TYPE": "wayland"}
    monkeypatch.setattr("os.environ", env)
    _honour_xwayland_preference()
    assert "QT_QPA_PLATFORM" not in env, "off by default"

    save_console_force_xwayland(True)
    _honour_xwayland_preference()
    assert env["QT_QPA_PLATFORM"] == "xcb"


def test_an_explicit_platform_in_the_environment_wins(monkeypatch, scratch_settings):
    from vmmanager.__main__ import _honour_xwayland_preference
    from vmmanager.pages.settings import save_console_force_xwayland

    save_console_force_xwayland(True)
    env = {"XDG_SESSION_TYPE": "wayland", "QT_QPA_PLATFORM": "offscreen"}
    monkeypatch.setattr("os.environ", env)
    _honour_xwayland_preference()
    assert env["QT_QPA_PLATFORM"] == "offscreen", "someone who set it meant it"


def test_an_x11_session_is_left_alone(monkeypatch, scratch_settings):
    from vmmanager.__main__ import _honour_xwayland_preference
    from vmmanager.pages.settings import save_console_force_xwayland

    save_console_force_xwayland(True)
    env = {"XDG_SESSION_TYPE": "x11"}
    monkeypatch.setattr("os.environ", env)
    _honour_xwayland_preference()
    assert "QT_QPA_PLATFORM" not in env, "the X11 grab already works"
