"""Settings page: connections, polling, confirmations, ISO directory."""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..libvirt_service import DEFAULT_URI, current_uri

_SETTINGS = ("vmmanager", "vmmanager")


def saved_connections() -> list[str]:
    settings = QSettings(*_SETTINGS)
    uris = settings.value("connections", [DEFAULT_URI])
    if isinstance(uris, str):
        uris = [uris]
    if DEFAULT_URI not in uris:
        uris.insert(0, DEFAULT_URI)
    return list(uris)


def save_connections(uris: list[str]) -> None:
    QSettings(*_SETTINGS).setValue("connections", uris)


def saved_poll_seconds() -> float:
    try:
        return float(QSettings(*_SETTINGS).value("poll_seconds", 2.0))
    except (TypeError, ValueError):
        return 2.0


def hook_script_path() -> str:
    """The script checked for marker awareness when switching modes.

    A mode can name a file to write its own name into, for something outside
    libvirt that needs to know which mode is in use. This is what we check to
    see whether anything actually reads it. Empty turns the check off.
    """
    from vmmanager.core.modes import DEFAULT_HOOK_SCRIPT

    value = QSettings(*_SETTINGS).value("hook_script", DEFAULT_HOOK_SCRIPT)
    return str(value if value is not None else "")


def save_hook_script_path(path: str) -> None:
    QSettings(*_SETTINGS).setValue("hook_script", path.strip())


def saved_theme_name() -> str:
    """The theme to start in. Empty means the one vmmanager ships with."""
    return str(QSettings(*_SETTINGS).value("theme", "") or "")


def save_theme_name(name: str) -> None:
    QSettings(*_SETTINGS).setValue("theme", name)


def confirmations_enabled() -> bool:
    return QSettings(*_SETTINGS).value("confirmations", "true") in ("true", True)


def close_to_tray() -> bool:
    return QSettings(*_SETTINGS).value("close_to_tray", "false") in ("true", True)


def console_scaling() -> str:
    """never | always | fullscreen, how the console fits its widget."""
    return QSettings(*_SETTINGS).value("console_scaling", "always")


def console_resize_guest() -> bool:
    """Ask the guest to match the window size (needs its agent)."""
    return QSettings(*_SETTINGS).value("console_resize_guest", "false") in ("true", True)


def save_console_resize_guest(on: bool) -> None:
    QSettings(*_SETTINGS).setValue("console_resize_guest", "true" if on else "false")


def console_autoconnect() -> bool:
    return QSettings(*_SETTINGS).value("console_autoconnect", "true") in ("true", True)


def console_release_keys() -> str:
    """Key combination that gives the pointer and keyboard back."""
    return QSettings(*_SETTINGS).value("console_release_keys", "Ctrl+Alt")


def console_grab_keyboard() -> bool:
    """Whether clicking the console hands the whole keyboard to the guest.

    On means Alt+Tab, Super and this app's own shortcuts go to the guest while
    you are working in it, which is what makes a console usable for anything
    that uses those keys. The release combination always comes back here.
    """
    return QSettings(*_SETTINGS).value("console_grab_keyboard", "true") in (
        "true", True
    )


def os_icons_enabled() -> bool:
    """Show an operating-system icon on each machine card."""
    return QSettings(*_SETTINGS).value("os_icons", "true") in ("true", True)


def stat_polling() -> dict:
    s = QSettings(*_SETTINGS)
    return {
        k: s.value(f"poll_{k}", "true") in ("true", True)
        for k in ("cpu", "memory", "disk", "network")
    }


def default_storage_format() -> str:
    return QSettings(*_SETTINGS).value("default_storage_format", "qcow2")


def default_firmware() -> str:
    return QSettings(*_SETTINGS).value("default_firmware", "UEFI")


def default_graphics() -> str:
    return QSettings(*_SETTINGS).value("default_graphics", "vnc")


def default_cpu_model() -> str:
    return QSettings(*_SETTINGS).value("default_cpu_model", "host-passthrough")


def virtio_win_iso() -> str:
    """Where this host keeps the virtio-win driver disc, if it has been said.

    One disc serves every Windows guest, and it is a 700 MB download otherwise,
    so the path picked once is offered again for the next machine. Empty means
    nothing has been remembered and the dialog starts from what it can find.
    """
    return str(QSettings(*_SETTINGS).value("virtio_win_iso", "") or "")


def save_virtio_win_iso(path: str) -> None:
    QSettings(*_SETTINGS).setValue("virtio_win_iso", path.strip())


def active_uri() -> str:
    uri = QSettings(*_SETTINGS).value("active_uri", DEFAULT_URI)
    return uri if uri in saved_connections() else DEFAULT_URI


class SettingsPage(QWidget):
    connection_changed = Signal(str)  # new active URI
    poll_changed = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self._settings = QSettings(*_SETTINGS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 0)
        outer.setSpacing(0)
        title = QLabel("Settings")
        title.setObjectName("PageTitle")
        outer.addWidget(title)
        outer.addSpacing(16)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        content = QVBoxLayout(inner)
        content.setContentsMargins(0, 0, 6, 30)
        content.setSpacing(10)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        conn_title = QLabel("Connections")
        conn_title.setProperty("class", "SectionTitle")
        content.addWidget(conn_title)
        conn_hint = QLabel(
            "Manage local and remote libvirt hosts. Remote URIs look like "
            "qemu+ssh://user@host/system (key-based SSH login required)."
        )
        conn_hint.setObjectName("ConsoleHint")
        conn_hint.setWordWrap(True)
        content.addWidget(conn_hint)
        self.conn_list = QListWidget()
        self.conn_list.setMaximumHeight(140)
        content.addWidget(self.conn_list)
        conn_row = QHBoxLayout()
        connect_btn = QPushButton("Switch to selected")
        connect_btn.setProperty("class", "PrimaryButton")
        connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        connect_btn.clicked.connect(self._switch)
        remove_btn = QPushButton("Remove selected")
        remove_btn.setProperty("class", "GhostButton")
        remove_btn.clicked.connect(self._remove)
        conn_row.addWidget(connect_btn)
        conn_row.addWidget(remove_btn)
        conn_row.addStretch(1)
        content.addLayout(conn_row)
        add_row = QHBoxLayout()
        self.new_uri = QLineEdit()
        self.new_uri.setPlaceholderText("qemu+ssh://user@host/system")
        add_btn = QPushButton("Add connection")
        add_btn.setProperty("class", "GhostButton")
        add_btn.clicked.connect(self._add)
        guided_btn = QPushButton("Add…")
        guided_btn.setProperty("class", "PrimaryButton")
        guided_btn.setToolTip("Pick a hypervisor and host, and test it first")
        guided_btn.clicked.connect(self._add_guided)
        add_row.addWidget(self.new_uri, 1)
        add_row.addWidget(add_btn)
        add_row.addWidget(guided_btn)
        content.addLayout(add_row)
        content.addSpacing(12)

        poll_title = QLabel("Usage sampling")
        poll_title.setProperty("class", "SectionTitle")
        content.addWidget(poll_title)
        poll_note = QLabel(
            "How often CPU, memory, disk and network figures are read. These "
            "are rates, so they have to be measured over an interval. A longer "
            "one costs the host less and gives coarser graphs."
        )
        poll_note.setWordWrap(True)
        poll_note.setObjectName("ConsoleHint")
        content.addWidget(poll_note)
        poll_row = QHBoxLayout()
        poll_label = QLabel("Sample every")
        poll_label.setProperty("class", "StatVal")
        self.poll = QDoubleSpinBox()
        self.poll.setRange(0.5, 60.0)
        self.poll.setDecimals(1)
        self.poll.setSingleStep(0.5)
        self.poll.setSuffix(" s")
        self.poll.setValue(saved_poll_seconds())
        self.poll.valueChanged.connect(self._poll_changed)
        poll_row.addWidget(poll_label)
        poll_row.addWidget(self.poll)
        poll_row.addStretch(1)
        content.addLayout(poll_row)
        # Whether this interval also governs how quickly a machine's state
        # appears depends on the host: with events it does not, without them it
        # does. Say which, since it changes what a sensible value is.
        self.event_status = QLabel("")
        self.event_status.setWordWrap(True)
        self.event_status.setObjectName("ConsoleHint")
        content.addWidget(self.event_status)
        content.addSpacing(12)

        thumbs_title = QLabel("Machine cards")
        thumbs_title.setProperty("class", "SectionTitle")
        content.addWidget(thumbs_title)
        self.thumbnails = QCheckBox(
            "Live console thumbnails on the cards (refreshes every 5 s)"
        )
        self.thumbnails.setChecked(
            self._settings.value("thumbnails", "false") in ("true", True)
        )
        self.thumbnails.toggled.connect(
            lambda on: self._settings.setValue("thumbnails", "true" if on else "false")
        )
        content.addWidget(self.thumbnails)
        self.os_icons = QCheckBox(
            "Operating-system icon on each card (logos are fetched once and cached)"
        )
        self.os_icons.setChecked(os_icons_enabled())
        self.os_icons.toggled.connect(
            lambda on: self._settings.setValue("os_icons", "true" if on else "false")
        )
        content.addWidget(self.os_icons)
        self.tray_check = QCheckBox(
            "Keep running in the system tray when the window closes"
        )
        self.tray_check.setChecked(close_to_tray())
        self.tray_check.toggled.connect(
            lambda on: self._settings.setValue("close_to_tray", "true" if on else "false")
        )
        content.addWidget(self.tray_check)
        content.addSpacing(12)

        console_title = QLabel("Console")
        console_title.setProperty("class", "SectionTitle")
        content.addWidget(console_title)
        scale_row = QHBoxLayout()
        scale_row.setSpacing(10)
        scale_label = QLabel("Scale the display")
        scale_label.setProperty("class", "StatVal")
        self.scaling = QComboBox()
        self.scaling.addItems(["always", "never", "fullscreen only"])
        self.scaling.setCurrentText(
            {"always": "always", "never": "never", "fullscreen": "fullscreen only"}
            .get(console_scaling(), "always")
        )
        self.scaling.currentTextChanged.connect(
            lambda t: self._settings.setValue(
                "console_scaling",
                {"always": "always", "never": "never",
                 "fullscreen only": "fullscreen"}[t],
            )
        )
        scale_row.addWidget(scale_label)
        scale_row.addWidget(self.scaling)
        release_label = QLabel("Release input with")
        release_label.setProperty("class", "StatVal")
        self.release_keys = QComboBox()
        self.release_keys.addItems(["Ctrl+Alt", "Ctrl+Shift", "Alt+Shift", "Super"])
        self.release_keys.setCurrentText(console_release_keys())
        self.release_keys.currentTextChanged.connect(
            lambda t: self._settings.setValue("console_release_keys", t)
        )
        scale_row.addSpacing(12)
        scale_row.addWidget(release_label)
        scale_row.addWidget(self.release_keys)
        scale_row.addStretch(1)
        content.addLayout(scale_row)
        self.grab_keyboard = QCheckBox(
            "Send every key to the guest while you are working in the console"
        )
        self.grab_keyboard.setToolTip(
            "Clicking the display takes the keyboard: Alt+Tab, Super and this "
            "app's own shortcuts go to the guest until you press the release "
            "combination."
        )
        self.grab_keyboard.setChecked(console_grab_keyboard())
        self.grab_keyboard.toggled.connect(
            lambda on: self._settings.setValue(
                "console_grab_keyboard", "true" if on else "false")
        )
        content.addWidget(self.grab_keyboard)
        self.resize_guest = QCheckBox(
            "Resize the guest's resolution to match the window (needs a "
            "retargetable display: virtio or QXL, with the guest's driver)"
        )
        self.resize_guest.setChecked(console_resize_guest())
        self.resize_guest.toggled.connect(
            lambda on: self._settings.setValue(
                "console_resize_guest", "true" if on else "false")
        )
        content.addWidget(self.resize_guest)
        self.autoconnect = QCheckBox("Connect the console automatically")
        self.autoconnect.setChecked(console_autoconnect())
        self.autoconnect.toggled.connect(
            lambda on: self._settings.setValue(
                "console_autoconnect", "true" if on else "false")
        )
        content.addWidget(self.autoconnect)
        content.addSpacing(12)

        stats_title = QLabel("Statistics collected")
        stats_title.setProperty("class", "SectionTitle")
        content.addWidget(stats_title)
        stats_hint = QLabel(
            "Turning a series off stops polling it, which is worth doing on a "
            "busy host with many machines."
        )
        stats_hint.setObjectName("ConsoleHint")
        stats_hint.setWordWrap(True)
        content.addWidget(stats_hint)
        stats_row = QHBoxLayout()
        stats_row.setSpacing(14)
        self.stat_boxes = {}
        for key, label in (("cpu", "CPU"), ("memory", "Memory"),
                           ("disk", "Disk I/O"), ("network", "Network I/O")):
            check = QCheckBox(label)
            check.setChecked(stat_polling()[key])
            check.toggled.connect(
                lambda on, k=key: self._settings.setValue(
                    f"poll_{k}", "true" if on else "false")
            )
            self.stat_boxes[key] = check
            stats_row.addWidget(check)
        stats_row.addStretch(1)
        content.addLayout(stats_row)
        content.addSpacing(12)

        defaults_title = QLabel("Defaults for new machines")
        defaults_title.setProperty("class", "SectionTitle")
        content.addWidget(defaults_title)
        defaults_row = QHBoxLayout()
        defaults_row.setSpacing(14)
        for label, key, options, getter in (
            ("storage format", "default_storage_format", ["qcow2", "raw"],
             default_storage_format),
            ("firmware", "default_firmware", ["UEFI", "BIOS"], default_firmware),
            ("display", "default_graphics", ["vnc", "spice"], default_graphics),
            ("cpu model", "default_cpu_model",
             ["host-passthrough", "host-model", "custom"], default_cpu_model),
        ):
            col = QVBoxLayout()
            col.setSpacing(2)
            tag = QLabel(label.upper())
            tag.setProperty("class", "StatKey")
            combo = QComboBox()
            combo.addItems(options)
            combo.setCurrentText(getter())
            combo.currentTextChanged.connect(
                lambda t, k=key: self._settings.setValue(k, t)
            )
            col.addWidget(tag)
            col.addWidget(combo)
            defaults_row.addLayout(col)
        defaults_row.addStretch(1)
        content.addLayout(defaults_row)
        content.addSpacing(12)

        confirm_title = QLabel("Confirmations")
        confirm_title.setProperty("class", "SectionTitle")
        content.addWidget(confirm_title)
        self.confirm = QCheckBox(
            "Ask before destructive actions (force off, delete, revert)"
        )
        self.confirm.setChecked(confirmations_enabled())
        self.confirm.toggled.connect(
            lambda on: self._settings.setValue("confirmations", "true" if on else "false")
        )
        content.addWidget(self.confirm)
        content.addSpacing(12)

        modes_title = QLabel("Modes")
        modes_title.setProperty("class", "SectionTitle")
        content.addWidget(modes_title)
        modes_hint = QLabel(
            "A mode can name a file to write its own name into, for something "
            "outside libvirt that needs to know which one is in use - typically "
            "a libvirt hook deciding whether to hand a graphics card over. "
            "Before a switch, this script is checked for any mention of that "
            "file, so a marker nothing reads gets pointed out rather than "
            "quietly having no effect. Clear it to skip the check."
        )
        modes_hint.setWordWrap(True)
        modes_hint.setObjectName("ConsoleHint")
        content.addWidget(modes_hint)
        hook_row = QHBoxLayout()
        self.hook_script = QLineEdit(hook_script_path())
        self.hook_script.setPlaceholderText("/etc/libvirt/hooks/qemu")
        self.hook_script.editingFinished.connect(self._hook_changed)
        hook_browse = QPushButton("Browse…")
        hook_browse.setProperty("class", "GhostButton")
        hook_browse.clicked.connect(self._pick_hook)
        hook_row.addWidget(self.hook_script, 1)
        hook_row.addWidget(hook_browse)
        content.addLayout(hook_row)
        content.addSpacing(12)

        iso_title = QLabel("ISO directory")
        iso_title.setProperty("class", "SectionTitle")
        content.addWidget(iso_title)
        iso_hint = QLabel("Where the new-machine dialog starts browsing for ISOs.")
        iso_hint.setObjectName("ConsoleHint")
        content.addWidget(iso_hint)
        row = QHBoxLayout()
        self.iso_dir = QLineEdit(
            self._settings.value("iso_dir", os.path.expanduser("~"))
        )
        self.iso_dir.editingFinished.connect(
            lambda: self._settings.setValue("iso_dir", self.iso_dir.text())
        )
        browse = QPushButton("Browse…")
        browse.setProperty("class", "GhostButton")
        browse.clicked.connect(self._pick_dir)
        row.addWidget(self.iso_dir, 1)
        row.addWidget(browse)
        content.addLayout(row)
        content.addSpacing(12)

        virtio_title = QLabel("virtio-win driver disc")
        virtio_title.setProperty("class", "SectionTitle")
        content.addWidget(virtio_title)
        virtio_hint = QLabel(
            "The disc Windows guests install their virtio drivers from. Set "
            "here, or by ticking 'remember this disc' when a machine attaches "
            "one; every machine after that is offered the same copy. Clear it "
            "to be asked again."
        )
        virtio_hint.setObjectName("ConsoleHint")
        virtio_hint.setWordWrap(True)
        content.addWidget(virtio_hint)
        virtio_row = QHBoxLayout()
        self.virtio_iso = QLineEdit(virtio_win_iso())
        self.virtio_iso.setPlaceholderText("/usr/share/virtio-win/virtio-win.iso")
        self.virtio_iso.editingFinished.connect(
            lambda: save_virtio_win_iso(self.virtio_iso.text())
        )
        virtio_browse = QPushButton("Browse…")
        virtio_browse.setProperty("class", "GhostButton")
        virtio_browse.clicked.connect(self._pick_virtio_iso)
        virtio_row.addWidget(self.virtio_iso, 1)
        virtio_row.addWidget(virtio_browse)
        content.addLayout(virtio_row)
        content.addSpacing(12)

        about_title = QLabel("About")
        about_title.setProperty("class", "SectionTitle")
        content.addWidget(about_title)
        about = QLabel("vmmanager, a native, friendly face for libvirt/QEMU.")
        about.setProperty("class", "StatVal")
        content.addWidget(about)
        content.addStretch(1)

        self._reload_connections()

    # -- connections

    def _reload_connections(self) -> None:
        self.conn_list.clear()
        active = current_uri()
        for uri in saved_connections():
            marker = "●  " if uri == active else "    "
            self.conn_list.addItem(f"{marker}{uri}")

    def _selected_uri(self) -> str | None:
        item = self.conn_list.currentItem()
        return item.text().strip().lstrip("● ").strip() if item else None

    def _switch(self) -> None:
        uri = self._selected_uri()
        if not uri or uri == current_uri():
            return
        self._settings.setValue("active_uri", uri)
        self.connection_changed.emit(uri)
        self._reload_connections()

    def _add_guided(self) -> None:
        """Build a URI with the connection dialog, then store it."""
        from ..dialogs import ConnectionDialog

        dialog = ConnectionDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        uri = dialog.chosen_uri()
        if not uri:
            return
        uris = saved_connections()
        if uri not in uris:
            uris.append(uri)
            save_connections(uris)
        self._reload_connections()

    def _add(self) -> None:
        uri = self.new_uri.text().strip()
        if not uri:
            return
        uris = saved_connections()
        if uri not in uris:
            uris.append(uri)
            save_connections(uris)
        self.new_uri.clear()
        self._reload_connections()

    def _remove(self) -> None:
        uri = self._selected_uri()
        if not uri or uri == DEFAULT_URI:
            return
        uris = [u for u in saved_connections() if u != uri]
        save_connections(uris)
        if uri == current_uri():
            self._settings.setValue("active_uri", DEFAULT_URI)
            self.connection_changed.emit(DEFAULT_URI)
        self._reload_connections()

    # -- misc

    def _hook_changed(self) -> None:
        from vmmanager.core.modes import set_hook_script

        path = self.hook_script.text().strip()
        save_hook_script_path(path)
        set_hook_script(path)

    def _pick_hook(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Pick the libvirt hook script", "/etc/libvirt/hooks",
        )
        if chosen:
            self.hook_script.setText(chosen)
            self._hook_changed()

    def _poll_changed(self, value: float) -> None:
        self._settings.setValue("poll_seconds", value)
        self.poll_changed.emit(value)

    def set_event_status(self, event_driven: bool) -> None:
        """Called once the poll worker knows what this host supports."""
        if event_driven:
            self.event_status.setText(
                "This host reports changes as they happen, so starting, "
                "stopping and editing a machine shows up immediately whatever "
                "this is set to."
            )
        else:
            self.event_status.setText(
                "This host reports no events, so machine state only refreshes "
                "on the interval above. Keep it short here."
            )

    def _pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Choose ISO directory", self.iso_dir.text()
        )
        if path:
            self.iso_dir.setText(path)
            self._settings.setValue("iso_dir", path)

    def _pick_virtio_iso(self) -> None:
        from ..data.catalog import virtio_win_candidates

        start = self.virtio_iso.text().strip() or next(
            iter(virtio_win_candidates()), self.iso_dir.text()
        )
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the virtio-win disc", start,
            "Disc images (*.iso);;All files (*)",
        )
        if path:
            self.virtio_iso.setText(path)
            save_virtio_win_iso(path)
