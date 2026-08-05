"""Console dialogs."""

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


class VncPasswordDialog(SizedDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("VNC password")
        self.setMinimumWidth(380)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("This display wants a password"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        box.addWidget(self.password)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Connect"))
