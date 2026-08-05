"""Timeline tab: state changes, snapshots and config edits."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports for the tab modules


class TimelineMixin:
    """Mixed into DetailPage; expects its attributes."""
    def _build_timeline(self) -> QWidget:
        from PySide6.QtWidgets import QTreeWidget

        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 0)
        box.setSpacing(10)
        row = QHBoxLayout()
        hint = QLabel("state changes, snapshots and config edits, newest first")
        hint.setObjectName("ConsoleHint")
        row.addWidget(hint)
        row.addStretch(1)
        row.addWidget(_ghost("Refresh", self._load_timeline))
        box.addLayout(row)
        self.timeline = QTreeWidget()
        self.timeline.setHeaderLabels(["When", "What", "Detail"])
        self.timeline.setColumnWidth(0, 170)
        self.timeline.setColumnWidth(1, 110)
        box.addWidget(self.timeline, 1)
        return page

    def _load_timeline(self) -> None:
        uuid = self.uuid
        if not uuid:
            return
        from ...data.history import query_events, xml_versions

        def work():
            entries: list[tuple[int, str, str]] = []
            for ts, kind, detail in query_events(uuid):
                entries.append((ts, kind, detail))
            for ts, _xml in xml_versions(uuid):
                entries.append((ts, "config", "definition changed"))
            for snap in svc_list_snapshots(uuid):
                kind = "snapshot"
                entries.append(
                    (snap.created, kind,
                     f"{snap.name} ({'external' if snap.external else 'internal'})")
                )
            entries.sort(key=lambda e: -e[0])
            return entries[:400]

        def apply(entries) -> None:
            if self.uuid != uuid:
                return
            from PySide6.QtWidgets import QTreeWidgetItem

            colors = {
                "state": theme.OK, "snapshot": theme.ACCENT,
                "config": theme.WARN,
            }
            self.timeline.clear()
            day_items: dict[str, QTreeWidgetItem] = {}
            for ts, kind, detail in entries:
                dt = datetime.datetime.fromtimestamp(ts)
                day = dt.strftime("%Y-%m-%d")
                parent = day_items.get(day)
                if parent is None:
                    parent = QTreeWidgetItem([day, "", ""])
                    self.timeline.addTopLevelItem(parent)
                    day_items[day] = parent
                item = QTreeWidgetItem([dt.strftime("%H:%M:%S"), kind, detail])
                item.setForeground(1, QColor(colors.get(kind, theme.TEXT_DIM)))
                parent.addChild(item)
            self.timeline.expandAll()

        run_task(work, done=apply, failed=self._show_error)
