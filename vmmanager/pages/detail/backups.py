"""Backups tab: checkpoints and full/incremental backups."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports for the tab modules


class BackupsMixin:
    """Mixed into DetailPage; expects its attributes."""
    def _build_backups(self) -> QWidget:
        from PySide6.QtWidgets import QTreeWidget

        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 0)
        box.setSpacing(10)
        note = QLabel(
            "Backups copy disk contents out of a running machine. A full "
            "backup writes everything and starts a chain; an incremental one "
            "writes only the blocks changed since the last backup."
        )
        note.setWordWrap(True)
        note.setObjectName("ConsoleHint")
        box.addWidget(note)
        row = QHBoxLayout()
        full = QPushButton("Full backup…")
        full.setProperty("class", "PrimaryButton")
        full.clicked.connect(lambda: self._run_backup(False))
        incr = QPushButton("Incremental backup…")
        incr.setProperty("class", "GhostButton")
        incr.clicked.connect(lambda: self._run_backup(True))
        row.addWidget(full)
        row.addWidget(incr)
        row.addWidget(_ghost("Delete selected point", self._delete_checkpoint))
        row.addWidget(_ghost("Refresh", self._load_checkpoints))
        row.addStretch(1)
        box.addLayout(row)
        self.backup_status = QLabel("")
        self.backup_status.setObjectName("ConsoleHint")
        self.backup_status.setProperty("class", "Accent")
        self.backup_status.setWordWrap(True)
        box.addWidget(self.backup_status)
        self.chk_tree = QTreeWidget()
        self.chk_tree.setHeaderLabels(["Restore point", "Created", "Disks"])
        self.chk_tree.setColumnWidth(0, 240)
        self.chk_tree.setColumnWidth(1, 170)
        box.addWidget(self.chk_tree, 1)
        return page

    def _load_checkpoints(self) -> None:
        uuid = self.uuid
        if not uuid:
            return

        def apply(points) -> None:
            if self.uuid != uuid:
                return
            from PySide6.QtWidgets import QTreeWidgetItem

            self.chk_tree.clear()
            items: dict[str, QTreeWidgetItem] = {}
            for c in points:  # oldest first, so parents exist before children
                item = QTreeWidgetItem([
                    c.name,
                    datetime.datetime.fromtimestamp(c.created).strftime("%Y-%m-%d %H:%M:%S"),
                    ", ".join(c.disks),
                ])
                if c.parent and c.parent in items:
                    items[c.parent].addChild(item)
                else:
                    self.chk_tree.addTopLevelItem(item)
                items[c.name] = item
            self.chk_tree.expandAll()
            if not points:
                self.backup_status.setText(
                    "No restore points yet. A full backup creates the first one."
                )

        run_task(lambda: svc_list_checkpoints(uuid), done=apply, failed=self._show_error)

    def _run_backup(self, incremental: bool) -> None:
        uuid = self.uuid
        if not uuid:
            return
        dest = QFileDialog.getExistingDirectory(self, "Back up into folder")
        if not dest:
            return
        kind = "incremental" if incremental else "full"
        self.backup_status.setText(f"{kind} backup running…")
        run_task(
            lambda: svc_backup(uuid, dest, incremental),
            done=lambda msg: (
                self.backup_status.setText(str(msg)),
                self._load_checkpoints(),
            ),
            failed=lambda m: (
                self.backup_status.setText(""),
                ErrorDialog(self, "Backup failed", m).exec(),
            ),
        )

    def _delete_checkpoint(self) -> None:
        items = self.chk_tree.selectedItems()
        if not items or not self.uuid:
            return
        name = items[0].text(0)
        uuid = self.uuid
        confirm = ConfirmDialog(
            self, "Delete restore point",
            f"Delete '{name}'? Backups already written stay on disk, but "
            "incrementals can no longer be based on this point.",
            "Delete",
        )
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        run_task(
            lambda: svc_delete_checkpoint(uuid, name),
            done=lambda _: self._load_checkpoints(),
            failed=lambda m: ErrorDialog(self, "Delete failed", m).exec(),
        )
