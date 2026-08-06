"""XML tab: the whole domain definition."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports for the tab modules


class XmlMixin:
    """Mixed into DetailPage; expects its attributes."""
    def _build_xml(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 0)
        box.setSpacing(10)
        self.xml_edit = QPlainTextEdit()
        from ...syntax import XmlHighlighter

        self._xml_highlighter = XmlHighlighter(self.xml_edit.document())
        self._xml_loaded = ""
        self.xml_edit.textChanged.connect(self._xml_edited)
        box.addWidget(self.xml_edit, 1)
        row = QHBoxLayout()
        self.xml_status = QLabel("")
        self.xml_status.setObjectName("ConsoleHint")
        row.addWidget(self.xml_status)
        row.addStretch(1)
        reload_btn = _ghost("Reload", self._load_xml)
        # Looking at what an edit does, without doing it. The confirmation
        # on save shows the same diff, but only when confirmations are
        # turned on - and wanting to check before committing is not the
        # same as wanting to be asked every time.
        self.xml_preview_btn = _ghost("Preview changes…", self._preview_xml)
        self.xml_preview_btn.setEnabled(False)
        save_btn = QPushButton("Save definition")
        save_btn.setProperty("class", "PrimaryButton")
        save_btn.clicked.connect(self._save_xml)
        row.addWidget(reload_btn)
        row.addWidget(self.xml_preview_btn)
        row.addWidget(save_btn)
        box.addLayout(row)
        return page

    def _xml_edited(self) -> None:
        """Say whether what is on screen still matches the machine.

        Compared against the text that was loaded rather than asking
        libvirt: this runs on every keystroke.
        """
        changed = self.xml_edit.toPlainText() != self._xml_loaded
        self.xml_preview_btn.setEnabled(changed)
        if changed:
            self.xml_status.setText("edited · not saved")
        elif self.xml_status.text().startswith("edited"):
            self.xml_status.setText("")

    def _preview_xml(self) -> None:
        """The diff, with nothing riding on it."""
        uuid, xml = self.uuid, self.xml_edit.toPlainText()
        if not uuid:
            return

        def show(diff: str) -> None:
            from ...dialogs import DiffDialog

            if not diff:
                self.xml_status.setText(
                    "no change - this reads the same as the machine's own "
                    "definition, whatever the formatting says"
                )
                return
            DiffDialog(
                self, "What saving would change", diff, confirm=None,
                note="Nothing has been saved. Close this and press Save "
                     "definition to apply it.",
            ).exec()

        self.xml_status.setText("comparing…")
        run_task(
            lambda: svc_definition_diff(uuid, xml),
            done=show,
            failed=lambda m: ErrorDialog(self, "Invalid definition", m).exec(),
        )

    def _load_xml(self) -> None:
        uuid = self.uuid
        run_task(
            lambda: svc_get_xml(uuid),
            done=lambda xml: self.uuid == uuid and self._xml_arrived(xml),
            failed=self._show_error,
        )

    def _xml_arrived(self, xml: str) -> None:
        self._xml_loaded = xml
        self.xml_edit.setPlainText(xml)
        self.xml_status.setText("")
        self.xml_preview_btn.setEnabled(False)

    def _save_xml(self) -> None:
        xml = self.xml_edit.toPlainText()
        uuid = self.uuid
        from ..settings import confirmations_enabled

        if not uuid or not confirmations_enabled():
            self._define_xml(xml)
            return

        def show(diff: str) -> None:
            if not diff:
                self.xml_status.setText(
                    "nothing to save - the definition already reads like this"
                )
                return
            from ...dialogs import DiffDialog

            dialog = DiffDialog(
                self, "What saving changes", diff, confirm="Save definition",
                note="Applies on the machine's next start.",
            )
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._define_xml(xml)

        run_task(
            lambda: svc_definition_diff(uuid, xml),
            done=show,
            failed=lambda m: ErrorDialog(self, "Invalid definition", m).exec(),
        )

    def _define_xml(self, xml: str) -> None:
        run_task(
            lambda: svc_define_xml(xml),
            done=lambda _: (
                setattr(self, "_xml_loaded", xml),
                self.xml_status.setText("saved - applies on next start"),
                self.xml_preview_btn.setEnabled(False),
            ),
            failed=lambda m: ErrorDialog(self, "Invalid definition", m).exec(),
        )
