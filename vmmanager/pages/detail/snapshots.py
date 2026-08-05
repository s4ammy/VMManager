"""Snapshots tab."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports for the tab modules


class SnapshotsMixin:
    """Mixed into DetailPage; expects its attributes."""
    def _build_snapshots(self) -> QWidget:
        from PySide6.QtWidgets import QTreeWidget

        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 0)
        box.setSpacing(10)
        row = QHBoxLayout()
        take = QPushButton("Take snapshot")
        take.setProperty("class", "PrimaryButton")
        take.clicked.connect(self._take_snapshot)
        revert = _ghost("Revert to selected", self._revert_snapshot)
        delete = _ghost("Delete selected", self._delete_snapshot)
        row.addWidget(take)
        row.addWidget(revert)
        row.addWidget(delete)
        row.addStretch(1)
        box.addLayout(row)
        self.snap_tree = QTreeWidget()
        self.snap_tree.setHeaderLabels(
            ["Name", "Created", "State", "Type", "Current", "Description"]
        )
        self.snap_tree.setColumnWidth(0, 220)
        self.snap_tree.setColumnWidth(1, 140)
        box.addWidget(self.snap_tree, 1)
        return page

    def _load_snapshots(self) -> None:
        uuid = self.uuid

        def apply(snaps: list[SnapshotInfo]) -> None:
            if self.uuid != uuid:
                return
            from PySide6.QtWidgets import QTreeWidgetItem

            self.snap_tree.clear()
            items: dict[str, QTreeWidgetItem] = {}
            for s in snaps:  # sorted oldest-first, so parents come first
                item = QTreeWidgetItem(
                    [
                        s.name,
                        datetime.datetime.fromtimestamp(s.created).strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                        s.state,
                        "external" if s.external else "internal",
                        "●" if s.current else "",
                        s.description,
                    ]
                )
                if s.parent and s.parent in items:
                    items[s.parent].addChild(item)
                else:
                    self.snap_tree.addTopLevelItem(item)
                items[s.name] = item
            self.snap_tree.expandAll()

        run_task(lambda: svc_list_snapshots(uuid), done=apply, failed=self._show_error)

    def _selected_snapshot(self) -> str | None:
        items = self.snap_tree.selectedItems()
        return items[0].text(0) if items else None

    def _take_snapshot(self) -> None:
        if not self._snap:
            return
        force_external = bool(
            self._hw and self._hw.firmware == "UEFI" and self._snap.state == "running"
        )
        dialog = SnapshotDialog(self, self._snap.name, force_external=force_external)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        uuid, name = self.uuid, dialog.name.text().strip()
        desc = dialog.description.text().strip()
        external = dialog.is_external()
        run_task(
            lambda: svc_create_snapshot(uuid, name, desc, external),
            done=lambda _: self._load_snapshots(),
            failed=lambda m: ErrorDialog(self, "Snapshot failed", m).exec(),
        )

    def _revert_snapshot(self) -> None:
        name = self._selected_snapshot()
        if not name or not self._snap:
            return
        confirm = ConfirmDialog(
            self,
            "Revert to snapshot",
            f"Revert {self._snap.name} to '{name}'? Changes made since the "
            "snapshot will be lost.",
            "Revert",
        )
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        uuid = self.uuid
        run_task(
            lambda: svc_revert_snapshot(uuid, name),
            done=lambda _: self._load_snapshots(),
            failed=lambda m: ErrorDialog(self, "Revert failed", m).exec(),
        )

    def _delete_snapshot(self) -> None:
        name = self._selected_snapshot()
        if not name:
            return
        confirm = ConfirmDialog(
            self,
            "Delete snapshot",
            f"Delete snapshot '{name}'? This can't be undone.",
            "Delete",
        )
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        uuid = self.uuid
        run_task(
            lambda: svc_delete_snapshot(uuid, name),
            done=lambda _: self._load_snapshots(),
            failed=lambda m: ErrorDialog(self, "Delete failed", m).exec(),
        )
