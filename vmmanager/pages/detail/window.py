"""One machine in a window of its own.

The detail page holds no shared state - every console client, timer and cached
lookup belongs to the instance - so a second one in a top-level window works
without the page knowing anything about it. What differs is the framing: there is
no list to go back to, and the machine's name belongs in the title bar.

Only one window per machine. Two live consoles onto the same guest would fight
over a single-connection VNC server, and two views of one machine editing its
definition is a way to lose an edit.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ... import APP_NAME
from ...libvirt_service import DomainSnapshot, HostSnapshot
from .page import DetailPage


class MachineWindow(QWidget):
    """A top-level window showing one machine."""

    closed = Signal(str)  # uuid

    def __init__(self, snap: DomainSnapshot, host: HostSnapshot | None) -> None:
        super().__init__()  # no parent: this is a window, not a panel
        self.uuid = snap.uuid
        self.setObjectName("Root")  # the stylesheet paints the app background here

        self.page = DetailPage()
        self.page.set_windowed(True)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.page)

        self.page.host = host
        self.page.show_domain(snap)
        self.page.set_visible_page(True)
        self._retitle(snap.name)
        self.resize(1180, 820)
        QShortcut(QKeySequence.StandardKey.Close, self, activated=self.close)

    def _retitle(self, name: str) -> None:
        self.setWindowTitle(f"{name} - {APP_NAME}")

    def update_from(self, snap: DomainSnapshot) -> None:
        self._retitle(snap.name)
        self.page.update_from(snap)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self.page.set_visible_page(False)
        self.page.shutdown()
        self.closed.emit(self.uuid)
        super().closeEvent(event)
