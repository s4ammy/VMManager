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
        box.addWidget(self.xml_edit, 1)
        row = QHBoxLayout()
        self.xml_status = QLabel("")
        self.xml_status.setObjectName("ConsoleHint")
        row.addWidget(self.xml_status)
        row.addStretch(1)
        reload_btn = _ghost("Reload", self._load_xml)
        save_btn = QPushButton("Save definition")
        save_btn.setProperty("class", "PrimaryButton")
        save_btn.clicked.connect(self._save_xml)
        row.addWidget(reload_btn)
        row.addWidget(save_btn)
        box.addLayout(row)
        return page

    def _load_xml(self) -> None:
        uuid = self.uuid
        run_task(
            lambda: svc_get_xml(uuid),
            done=lambda xml: self.uuid == uuid
            and (self.xml_edit.setPlainText(xml), self.xml_status.setText("")),
            failed=self._show_error,
        )

    def _save_xml(self) -> None:
        xml = self.xml_edit.toPlainText()
        run_task(
            lambda: svc_define_xml(xml),
            done=lambda _: self.xml_status.setText(
                "saved - applies on next start"
            ),
            failed=lambda m: ErrorDialog(self, "Invalid definition", m).exec(),
        )
