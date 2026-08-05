"""Snapshot dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
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
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from .base import SizedDialog, _buttons, _field_label, _title


class SnapshotDialog(SizedDialog):
    def __init__(self, parent, vm_name: str, force_external: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Take snapshot")
        self.setMinimumWidth(440)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Snapshot of {vm_name}"))
        box.addWidget(_field_label("name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("before-update")
        box.addWidget(self.name)
        box.addWidget(_field_label("description (optional)"))
        self.description = QLineEdit()
        box.addWidget(self.description)
        box.addWidget(_field_label("type"))
        self.snap_type = QComboBox()
        self.snap_type.addItems(
            ["internal - stored inside the qcow2",
             "external - overlay files, works with UEFI"]
        )
        if force_external:
            self.snap_type.setCurrentIndex(1)
        box.addWidget(self.snap_type)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Take snapshot"))
        self._ok_button.setEnabled(False)
        self.name.textChanged.connect(
            lambda t: self._ok_button.setEnabled(bool(t.strip()))
        )

    def is_external(self) -> bool:
        return self.snap_type.currentIndex() == 1
