"""What this app did, and how it went.

The timeline on a machine shows what libvirt reported about it. This shows
the other side: which of those the app asked for, everything else it asked
for, and what came back. It is the page you want after "I'm sure I set that
yesterday" or "the scheduled snapshot did not happen".

Failures are one click away because they are the reason anyone comes here.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtGui import QColor

from .. import theme
from ..data.history import query_activity
from ..tasks import run_task

# svc_set_boot_menu is what the code calls it; "boot menu" is what a person
# calls it. Only the ones whose bare name reads badly are listed - the rest
# come out fine with the prefix stripped and the underscores replaced.
FRIENDLY = {
    "svc_domain_action": "machine action",
    "svc_pool_action": "storage pool action",
    "svc_network_action": "network action",
    "svc_agent_action": "guest agent action",
    "svc_define_xml": "edit definition",
    "svc_set_device_xml": "edit device XML",
    "svc_guest_exec": "run command in guest",
    "svc_delete": "delete machine",
    "svc_clone": "clone machine",
    "svc_linked_clone": "linked clone",
    "svc_switch_mode": "switch mode",
    "svc_deploy_stack": "deploy stack",
    "svc_teardown_stack": "tear down stack",
    "svc_repair_image": "repair disk image",
    "svc_convert_image": "convert disk image",
}


def readable(action: str) -> str:
    """The service function's name, as something to read in a list."""
    if action in FRIENDLY:
        return FRIENDLY[action]
    return action.removeprefix("svc_").replace("_", " ")


def ago(stamp: int, now: float | None = None) -> str:
    """How long ago, in the roughest unit that still says something."""
    delta = max(0, int((now if now is not None else time.time()) - stamp))
    if delta < 60:
        return "just now"
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if delta >= size:
            return f"{delta // size}{unit} ago"
    return "just now"


class ActivityPage(QWidget):
    """Every write this app made, newest first."""

    def __init__(self) -> None:
        super().__init__()
        self._names: dict[str, str] = {}

        box = QVBoxLayout(self)
        box.setContentsMargins(28, 24, 28, 24)
        box.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("Activity")
        title.setProperty("class", "PageTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter by machine or what was done")
        self.search.setFixedWidth(260)
        self.search.textChanged.connect(self._redraw)
        head.addWidget(self.search)
        self.failures = QCheckBox("Only what failed")
        self.failures.toggled.connect(lambda _on: self.refresh())
        head.addWidget(self.failures)
        refresh = QPushButton("Refresh")
        refresh.setProperty("class", "GhostButton")
        refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh.clicked.connect(self.refresh)
        head.addWidget(refresh)
        box.addLayout(head)

        self.subtitle = QLabel(
            "Everything this app changed, with what libvirt said back. Reads "
            "are not recorded. Kept for 90 days."
        )
        self.subtitle.setObjectName("ConsoleHint")
        self.subtitle.setWordWrap(True)
        box.addWidget(self.subtitle)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["when", "machine", "did", "result"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 220)
        self.table.horizontalHeader().setStretchLastSection(True)
        box.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setObjectName("ConsoleHint")
        box.addWidget(self.status)
        self._rows: list = []

    def set_machine_names(self, domains) -> None:
        """So a row can say "Builder" rather than a uuid."""
        self._names = {d.uuid: d.name for d in domains}
        if self._rows:
            self._redraw()

    def refresh(self) -> None:
        failures_only = self.failures.isChecked()
        run_task(
            lambda: query_activity(limit=1000, failures_only=failures_only),
            done=self._arrived,
            failed=lambda _m: None,
        )

    def _arrived(self, rows) -> None:
        self._rows = list(rows)
        self._redraw()

    def _redraw(self) -> None:
        needle = self.search.text().strip().lower()
        now = time.time()
        shown = []
        for ts, uuid, action, detail, ok in self._rows:
            name = self._names.get(uuid, uuid[:8] if uuid else "-")
            what = readable(action)
            if needle and needle not in f"{name} {what} {detail}".lower():
                continue
            shown.append((ts, name, what, detail, ok, now))

        self.table.setRowCount(len(shown))
        for i, (ts, name, what, detail, ok, when) in enumerate(shown):
            for column, text in enumerate(
                (ago(ts, when), name, what, detail or ("done" if ok else "failed"))
            ):
                item = QTableWidgetItem(text)
                if not ok:
                    item.setForeground(QColor(theme.DANGER))
                if column == 0:
                    item.setToolTip(
                        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                    )
                self.table.setItem(i, column, item)

        failed = sum(1 for r in self._rows if not r[4])
        if not self._rows:
            self.status.setText(
                "Nothing recorded yet. Anything you change from here will "
                "appear as you do it."
            )
        elif needle:
            self.status.setText(f"{len(shown)} of {len(self._rows)} shown")
        else:
            self.status.setText(
                f"{len(self._rows)} recorded"
                + (f" · {failed} failed" if failed else "")
            )
