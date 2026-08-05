"""The machine detail page: a shell that composes one mixin per tab."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports
from .backups import BackupsMixin
from .console import ConsoleMixin
from .hardware import HardwareMixin
from .history import HistoryMixin
from .overview import OverviewMixin
from .serial import SerialMixin
from .snapshots import SnapshotsMixin
from .ssh import SshMixin
from .timeline import TimelineMixin
from .toolbox import ToolboxMixin
from .xml_tab import XmlMixin


class DetailPage(
    OverviewMixin,
    ConsoleMixin,
    SerialMixin,
    SshMixin,
    HardwareMixin,
    SnapshotsMixin,
    BackupsMixin,
    XmlMixin,
    HistoryMixin,
    TimelineMixin,
    ToolboxMixin,
    QWidget,
):
    back = Signal()

    action = Signal(str, str)  # uuid, op

    menu_requested = Signal(str, object)  # uuid, QPoint

    pop_out = Signal(str)  # uuid - open this machine in a window of its own

    (
        TAB_OVERVIEW, TAB_CONSOLE, TAB_HARDWARE, TAB_SNAPSHOTS,
        TAB_BACKUPS, TAB_XML, TAB_HISTORY, TAB_TOOLBOX,
    ) = range(8)
    # inner segment indexes within the CONSOLE tab
    VIEW_GRAPHICAL, VIEW_SERIAL, VIEW_SSH = 0, 1, 2

    def __init__(self) -> None:
        super().__init__()
        self.uuid: str | None = None
        self.host: HostSnapshot | None = None
        self._snap: DomainSnapshot | None = None
        self._hw: Hardware | None = None
        self._serial: SerialSession | None = None
        self._vnc_target: GraphicsInfo | None = None
        self._vnc_password = ""
        self._detached: DetachedConsoleWindow | None = None
        self._detached_client = None
        self._hw_view_mode = "details"
        self._tuning = None
        self._topology = None
        self._features = None
        self._modes: list = []
        self._feature_support = None
        self._evdev_devices = []
        self._error_open = False

        content = QVBoxLayout(self)
        content.setContentsMargins(36, 24, 36, 24)
        content.setSpacing(0)

        # -- header
        head = QHBoxLayout()
        head.setSpacing(12)
        self._back_btn = QPushButton("← machines")
        self._back_btn.setObjectName("BackButton")
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.clicked.connect(self.back.emit)
        head.addWidget(self._back_btn)
        self._led = Led()
        head.addWidget(self._led)
        self._name = QLabel()
        self._name.setObjectName("DetailName")
        head.addWidget(self._name)
        self._state = QLabel()
        self._state.setObjectName("DetailState")
        head.addWidget(self._state)
        self._ip = QLabel()
        self._ip.setProperty("class", "StatVal")
        head.addWidget(self._ip)
        head.addStretch(1)

        # Modes sit next to Start because switching one is usually the thing
        # you do just before starting, and hunting for a right-click menu to do
        # it is a poor trade.
        self._mode_btn = QPushButton("Modes")
        self._mode_btn.setProperty("class", "GhostButton")
        self._mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mode_btn.clicked.connect(self._show_mode_menu)
        self._mode_btn.hide()

        self._pop_btn = QPushButton("Pop out")
        self._pop_btn.setProperty("class", "GhostButton")
        self._pop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pop_btn.setToolTip(
            "Open this machine in a window of its own, so you can work on "
            "another one beside it"
        )
        self._pop_btn.clicked.connect(
            lambda: self.uuid and self.pop_out.emit(self.uuid)
        )

        self._primary_op = "start"
        self._start = QPushButton("Start")
        self._start.setProperty("class", "PrimaryButton")
        self._start.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start.clicked.connect(
            lambda: self.uuid and self.action.emit(self.uuid, self._primary_op)
        )
        self._stop = QPushButton("Shut down")
        self._stop.setProperty("class", "GhostButton")
        self._stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop.clicked.connect(
            lambda: self.uuid and self.action.emit(self.uuid, "shutdown")
        )
        more = QPushButton("⋯")
        more.setProperty("class", "IconButton")
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.clicked.connect(
            lambda: self.uuid
            and self.menu_requested.emit(
                self.uuid, more.mapToGlobal(more.rect().bottomLeft())
            )
        )
        head.addWidget(self._pop_btn)
        head.addWidget(self._mode_btn)
        head.addWidget(self._start)
        head.addWidget(self._stop)
        head.addWidget(more)
        content.addLayout(head)
        content.addSpacing(14)

        # -- tabs
        #
        # Eight tabs, not eleven: the three ways of reaching a machine share
        # one CONSOLE tab, and the two records of what happened to it share
        # HISTORY. Each group gets an inner switcher, which keeps the tab
        # strip narrow enough that it never needs scroll arrows.
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_overview(), "OVERVIEW")
        self.tabs.addTab(
            self._grouped_tab(
                "console_view",
                [("GRAPHICAL", self._build_console()),
                 ("SERIAL", self._build_serial()),
                 ("SSH", self._build_ssh())],
                self._console_view_changed,
            ),
            "CONSOLE",
        )
        self.tabs.addTab(self._build_hardware(), "HARDWARE")
        self.tabs.addTab(self._build_snapshots(), "SNAPSHOTS")
        self.tabs.addTab(self._build_backups(), "BACKUPS")
        self.tabs.addTab(self._build_xml(), "XML")
        self.tabs.addTab(
            self._grouped_tab(
                "history_view",
                [("TIMELINE", self._build_timeline()),
                 ("CONFIG VERSIONS", self._build_history())],
            ),
            "HISTORY",
        )
        self.tabs.addTab(self._build_toolbox(), "TOOLBOX")
        self.tabs.currentChanged.connect(self._tab_changed)
        content.addWidget(self.tabs, 1)

        self._scrub_timer = QTimer(self)
        self._scrub_timer.setSingleShot(True)
        self._scrub_timer.setInterval(200)
        self._scrub_timer.timeout.connect(self._load_history)

    def show_domain(self, snap: DomainSnapshot) -> None:
        """Called when navigating to a machine."""
        if self.uuid != snap.uuid:
            self._stop_serial()
            if self._detached is not None:
                self._detached.close()
            self._close_console()
            self._vnc_password = ""
        self.uuid = snap.uuid
        self.update_from(snap)
        self.tabs.setCurrentIndex(0)
        self.range_combo.setCurrentIndex(0)
        self.hw_status.setText("")
        self._load_hardware()
        self._load_tuning()
        self._load_features()
        self._load_xml()
        self._load_snapshots()
        self._load_history_tab()
        self._load_timeline()
        self._load_checkpoints()
        self.backup_status.setText("")
        self._stop_ssh()
        self.agent_info.setText(
            "Machine must be running, with qemu-guest-agent installed."
        )
        self.cmdline.clear()

    def update_from(self, snap: DomainSnapshot) -> None:
        """Called on every poll tick while this page is visible."""
        was_state = self._snap.state if self._snap else None
        self._snap = snap
        self._name.setText(snap.name)
        state_text = snap.state.upper()
        if snap.has_managed_save:
            state_text += " · SAVED"
        self._state.setText(state_text)
        theme.set_class(self._state, theme.state_class(snap.state))
        self._ip.setText(snap.ip or "")
        self._led.set_state(snap.state)
        running = snap.state == "running"
        paused = snap.state in ("paused", "suspended")
        self._primary_op = "resume" if paused else "start"
        self._start.setText(
            "Resume" if paused else ("Restore" if snap.has_managed_save else "Start")
        )
        self._start.setVisible(not running)
        self._stop.setVisible(running or paused)

        if self._range_secs() == 0:
            self._apply_live_charts(snap)

        if was_state != snap.state and self.tabs.currentIndex() == self.TAB_CONSOLE:
            if running:
                self._connect_console()
            else:
                self._close_console()
                self.console_hint.setText("machine is not running")
        if was_state == "running" and not running:
            self._stop_serial()

    def set_windowed(self, windowed: bool) -> None:
        """Drop the framing that only makes sense inside the main window.

        There is no list to go back to, and popping out what is already popped
        out would just raise the window you are looking at.
        """
        self._back_btn.setVisible(not windowed)
        self._pop_btn.setVisible(not windowed)

    def set_visible_page(self, visible: bool) -> None:
        if not visible:
            if self._detached is None:
                self._close_console()
            self._stop_serial()
            self._stop_ssh()
        elif self.tabs.currentIndex() == self.TAB_CONSOLE and self._active_client() is None:
            self._connect_console()

    def shutdown(self) -> None:
        """App is closing - tear down live connections."""
        if self._detached is not None:
            self._detached.close()
        self._close_console()
        self._stop_serial()
        self._stop_ssh()

    # -- modes

    mode_switch = Signal(str, str)  # uuid, mode name
    modes_requested = Signal(str)  # uuid

    def set_modes(self, modes) -> None:
        """Called with this machine's saved modes, or an empty list."""
        self._modes = list(modes)
        active = next((m.name for m in self._modes if m.active), "")
        drifted = any(m.active and not m.matches for m in self._modes)
        if not self._modes:
            self._mode_btn.hide()
            return
        label = active or "Modes"
        if drifted:
            label += " *"
        self._mode_btn.setText(f"{label} ▾")
        self._mode_btn.setToolTip(
            "the definition has changed since this mode was saved"
            if drifted else "switch this machine to another saved configuration"
        )
        self._mode_btn.show()

    def _show_mode_menu(self) -> None:
        menu = self._build_mode_menu()
        if menu is not None:
            menu.exec(
                self._mode_btn.mapToGlobal(self._mode_btn.rect().bottomLeft())
            )

    def _build_mode_menu(self):
        """Built separately from being shown, so its contents can be checked."""
        if not self.uuid:
            return None
        menu = QMenu(self)
        running = self._snap is not None and self._snap.state != "shutoff"
        for mode in self._modes:
            action = menu.addAction(
                f"{mode.name}   (current)" if mode.active else mode.name
            )
            action.setEnabled(not mode.active and not running)
            action.triggered.connect(
                lambda _c=False, name=mode.name: self.mode_switch.emit(self.uuid, name)
            )
        if running:
            menu.addSeparator()
            note = menu.addAction("shut the machine down to switch")
            note.setEnabled(False)
        menu.addSeparator()
        menu.addAction(
            "Manage modes…", lambda: self.modes_requested.emit(self.uuid)
        )
        return menu

    def _grouped_tab(self, attr: str, views, on_change=None) -> QWidget:
        """A tab holding several views behind a small segmented switcher.

        `attr` names the QStackedWidget so tabs can address their own views;
        `views` is [(label, widget)]; `on_change` fires with the new index.
        """
        from PySide6.QtWidgets import QStackedWidget

        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 10, 0, 0)
        box.setSpacing(8)

        stack = QStackedWidget()
        setattr(self, attr, stack)
        row = QHBoxLayout()
        row.setSpacing(2)
        buttons: list[QPushButton] = []

        def select(index: int) -> None:
            stack.setCurrentIndex(index)
            for i, btn in enumerate(buttons):
                btn.setProperty("active", "true" if i == index else "false")
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            if on_change is not None:
                on_change(index)

        for i, (label, widget) in enumerate(views):
            stack.addWidget(widget)
            btn = QPushButton(label)
            btn.setProperty("class", "SwitchTab")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, idx=i: select(idx))
            buttons.append(btn)
            row.addWidget(btn)
        row.addStretch(1)
        box.addLayout(row)
        box.addWidget(stack, 1)
        select(0)
        return page

    def _console_view_changed(self, index: int) -> None:
        """Only the visible way in stays connected."""
        if index == self.VIEW_GRAPHICAL:
            self._stop_serial()
            self._stop_ssh()
            if (
                self.tabs.currentIndex() == self.TAB_CONSOLE
                and self._active_client() is None
            ):
                self._connect_console()
            return
        if self._detached is None:
            self._close_console()
        if index == self.VIEW_SERIAL:
            self._stop_ssh()
        else:
            self._stop_serial()

    def _console_view(self) -> int:
        return self.console_view.currentIndex()

    def _tab_changed(self, index: int) -> None:
        if index == self.TAB_CONSOLE:
            if (
                self._console_view() == self.VIEW_GRAPHICAL
                and self._active_client() is None
            ):
                self._connect_console()
            return
        if self._detached is None:  # a detached console keeps running
            self._close_console()
        self._stop_serial()
        self._stop_ssh()

    def _show_error(self, message: str) -> None:
        """Report a failed read, once.

        Opening a machine starts nine reads at once, and a machine libvirt
        cannot answer for fails all nine. exec() runs a nested event loop, which
        delivers the next failure, which opens another dialog on top - so one
        unreadable machine used to bury the window in modal dialogs, each
        waiting on the one above it. The first message is the useful one; the
        rest go to the log.
        """
        if self._error_open:
            log.warning("%s (another error dialog is already open)", message)
            return
        self._error_open = True
        try:
            ErrorDialog(self, "libvirt error", message).exec()
        finally:
            self._error_open = False
