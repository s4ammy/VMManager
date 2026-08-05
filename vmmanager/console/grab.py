"""Hand the whole keyboard to the guest, until the release combination.

A console that only gets the keys the desktop does not want is a console you
cannot use: Alt+Tab switches your windows rather than the guest's, Super opens
your launcher, and the application's own shortcuts - Ctrl+N, Ctrl+K, F5 - are
swallowed before the guest ever hears about them. Anything that is a shortcut
somewhere between the compositor and this window never arrives.

Grabbing is three things, because a key can be taken at three levels:

- Qt shortcuts. Qt asks the focus widget first, with a ShortcutOverride event,
  whether it would rather have the key itself. Accepting it turns the shortcut
  back into an ordinary key press. That is the only thing that stops our own
  QShortcuts and menu accelerators.
- Other widgets. `grabKeyboard()` routes every key in this application to the
  display, wherever the focus happens to be.
- The desktop. `setKeyboardGrabEnabled()` asks the windowing system for the
  rest: an X11 keyboard grab, or Wayland's shortcut-inhibit protocol. A
  compositor is free to refuse - and some do - which is why the other two are
  not skipped when it works.

Held until the release combination (Ctrl+Alt by default, Settings changes it),
which is the one thing the guest never receives.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

# The combinations offered in Settings, as the keys that have to be down
# together. Held keys are tracked by the display widgets rather than read from
# event.modifiers(): the modifier bits for the key currently being pressed are
# not reported the same way on every platform.
COMBO_KEYS = {
    "Ctrl+Alt": frozenset({Qt.Key.Key_Control, Qt.Key.Key_Alt}),
    "Ctrl+Shift": frozenset({Qt.Key.Key_Control, Qt.Key.Key_Shift}),
    "Alt+Shift": frozenset({Qt.Key.Key_Alt, Qt.Key.Key_Shift}),
    "Super": frozenset({Qt.Key.Key_Meta}),
}
DEFAULT_COMBO = "Ctrl+Alt"


def release_combo() -> frozenset:
    """The keys that give the keyboard back, from Settings."""
    return COMBO_KEYS.get(release_combo_name(), COMBO_KEYS[DEFAULT_COMBO])


def release_combo_name() -> str:
    try:
        from ..pages.settings import console_release_keys

        choice = console_release_keys()
    except Exception:  # noqa: BLE001 - preferences are optional
        return DEFAULT_COMBO
    return choice if choice in COMBO_KEYS else DEFAULT_COMBO


def grab_on_click() -> bool:
    """Whether clicking the display should take the keyboard with it."""
    try:
        from ..pages.settings import console_grab_keyboard

        return console_grab_keyboard()
    except Exception:  # noqa: BLE001 - preferences are optional
        return True


class InputGrab(QObject):
    """The keyboard grab for one display widget.

    Both console clients own one. It knows nothing about either protocol - the
    widget still sends the keys - it only decides who gets to see them first.
    """

    changed = Signal(bool)

    def __init__(self, widget) -> None:
        super().__init__(widget)
        self._widget = widget
        self._held = False
        self._system = False  # did the windowing system agree to grab?

    @property
    def held(self) -> bool:
        return self._held

    @property
    def system_wide(self) -> bool:
        """True when the desktop's own shortcuts are ours too."""
        return self._system

    def hint(self) -> str:
        """What to tell the user while this is on.

        Worth being exact about: under Wayland the compositor decides, and KDE
        and GNOME both keep Alt+Tab and Super for themselves whatever a client
        asks for. Claiming to send every key when the guest will never see
        Alt+Tab is how someone concludes the guest is broken.
        """
        if self._system:
            return f"every key goes to the guest · {release_combo_name()} to release"
        return (
            "keys go to the guest, except the ones your desktop keeps for "
            f"itself · {release_combo_name()} to release"
        )

    def take(self) -> None:
        if self._held:
            return
        self._held = True
        self._widget.grabKeyboard()
        self._system = self._ask_the_desktop(True)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.changed.emit(True)

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self._system:
            self._ask_the_desktop(False)
            self._system = False
        self._widget.releaseKeyboard()
        self.changed.emit(False)

    def _ask_the_desktop(self, on: bool) -> bool:
        """X11 keyboard grab, or Wayland shortcut inhibition. May be refused."""
        window = self._widget.window()
        handle = window.windowHandle() if window is not None else None
        if handle is None:
            return False
        try:
            granted = handle.setKeyboardGrabEnabled(on)
        except Exception:  # noqa: BLE001 - platform plugin may not implement it
            return False
        return bool(granted) and on

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt's name
        """Take the keys Qt was about to turn into shortcuts.

        Qt sends this before every key press that matches a shortcut anywhere in
        the application. Accepting it says "the focus widget wants this key
        itself", which is exactly true: it belongs to the guest.
        """
        if self._held and event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        return False
