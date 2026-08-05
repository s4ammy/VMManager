"""History tab: config versions with diffs."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports for the tab modules


class HistoryMixin:
    """Mixed into DetailPage; expects its attributes."""
    def _build_history(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 0)
        box.setSpacing(10)
        hint = QLabel(
            "Every persistent-config change is recorded. Select a version to "
            "see what changed compared to the current definition."
        )
        hint.setObjectName("ConsoleHint")
        hint.setWordWrap(True)
        box.addWidget(hint)
        split = QHBoxLayout()
        from PySide6.QtWidgets import QListWidget

        self.hist_list = QListWidget()
        self.hist_list.setFixedWidth(210)
        self.hist_list.currentRowChanged.connect(self._show_diff)
        split.addWidget(self.hist_list)
        self.hist_diff = QPlainTextEdit()
        self.hist_diff.setReadOnly(True)
        self.hist_diff.setPlaceholderText("Select a version on the left.")
        from ...syntax import DiffHighlighter

        self._diff_highlighter = DiffHighlighter(self.hist_diff.document())
        split.addWidget(self.hist_diff, 1)
        box.addLayout(split, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        restore = QPushButton("Restore selected version")
        restore.setProperty("class", "PrimaryButton")
        restore.clicked.connect(self._restore_version)
        row.addWidget(restore)
        box.addLayout(row)
        self._versions: list[tuple[int, str]] = []
        return page

    def _load_history_tab(self) -> None:
        uuid = self.uuid
        from ...data.history import xml_versions

        def apply(versions) -> None:
            if self.uuid != uuid:
                return
            self._versions = versions
            self.hist_list.clear()
            for ts, _xml in versions:
                self.hist_list.addItem(
                    datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                )
            self.hist_diff.clear()

        run_task(lambda: xml_versions(uuid), done=apply, failed=self._show_error)

    def _show_diff(self, row: int) -> None:
        if row < 0 or row >= len(self._versions):
            return
        uuid = self.uuid
        _ts, old_xml = self._versions[row]

        def apply(current: str) -> None:
            if self.uuid != uuid:
                return
            import difflib

            diff = "\n".join(
                difflib.unified_diff(
                    old_xml.splitlines(),
                    current.splitlines(),
                    fromfile="selected version",
                    tofile="current",
                    lineterm="",
                )
            )
            self.hist_diff.setPlainText(diff or "identical to the current definition")

        run_task(lambda: svc_get_xml(uuid), done=apply, failed=self._show_error)

    def _restore_version(self) -> None:
        row = self.hist_list.currentRow()
        if row < 0 or row >= len(self._versions):
            return
        ts, xml = self._versions[row]
        when = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        confirm = ConfirmDialog(
            self,
            "Restore configuration",
            f"Replace the current definition with the version from {when}? "
            "Takes effect on next start.",
            "Restore",
        )
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        run_task(
            lambda: svc_define_xml(xml),
            done=lambda _: (self._load_xml(), self._load_history_tab()),
            failed=lambda m: ErrorDialog(self, "Restore failed", m).exec(),
        )
