"""Application shell: sidebar navigation and the host readout panel."""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..libvirt_service import DomainSnapshot, HostSnapshot
from .format import fmt_mem
from .indicators import UsageBar


class Sidebar(QFrame):
    NAV = ["Machines", "Templates", "Stacks", "Storage", "Networks",
           "Themes", "Settings"]

    navigate = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(220)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 22, 14, 14)
        root.setSpacing(0)

        brand = QHBoxLayout()
        name = QLabel()
        name.setObjectName("BrandName")
        name.setTextFormat(Qt.TextFormat.RichText)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._brand = name
        self.restyle()
        brand.addWidget(name, 1)
        root.addLayout(brand)
        root.addSpacing(24)

        self._buttons: dict[str, QPushButton] = {}
        for label in self.NAV:
            btn = QPushButton(label)
            btn.setProperty("class", "NavButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, l=label: self.navigate.emit(l))
            root.addWidget(btn)
            root.addSpacing(2)
            self._buttons[label] = btn
        self.set_active("Machines")

        root.addStretch(1)
        self.host_panel = HostPanel()
        root.addWidget(self.host_panel)

    def restyle(self) -> None:
        """The accent is in the markup, so the markup has to be rewritten."""
        self._brand.setText(
            f'VM<span style="color:{theme.ACCENT}">Manager</span>'
        )

    def set_active(self, label: str) -> None:
        for name, btn in self._buttons.items():
            btn.setProperty("active", "true" if name == label else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

class HostPanel(QFrame):
    ROWS = ["node", "hyp", "vms"]

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("HostPanel")
        grid = QGridLayout(self)
        grid.setContentsMargins(13, 11, 13, 13)
        grid.setVerticalSpacing(3)

        label = QLabel("HOST")
        label.setObjectName("HostPanelLabel")
        grid.addWidget(label, 0, 0, 1, 2)

        self._vals: dict[str, QLabel] = {}
        row = 1
        for key in self.ROWS:
            k = QLabel(key)
            k.setProperty("class", "HostKey")
            v = QLabel(" - ")
            v.setProperty("class", "HostVal")
            v.setAlignment(Qt.AlignmentFlag.AlignRight)
            v.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            grid.addWidget(k, row, 0)
            grid.addWidget(v, row, 1)
            self._vals[key] = v
            row += 1

        cpu_key = QLabel("cpu")
        cpu_key.setProperty("class", "HostKey")
        self._cpu_val = QLabel(" - ")
        self._cpu_val.setProperty("class", "HostVal")
        self._cpu_val.setAlignment(Qt.AlignmentFlag.AlignRight)
        grid.addWidget(cpu_key, row, 0)
        grid.addWidget(self._cpu_val, row, 1)
        row += 1
        self._cpu_bar = UsageBar()
        grid.addWidget(self._cpu_bar, row, 0, 1, 2)
        row += 1

        mem_key = QLabel("mem")
        mem_key.setProperty("class", "HostKey")
        self._mem_val = QLabel(" - ")
        self._mem_val.setProperty("class", "HostVal")
        self._mem_val.setAlignment(Qt.AlignmentFlag.AlignRight)
        grid.addWidget(mem_key, row, 0)
        grid.addWidget(self._mem_val, row, 1)
        row += 1
        self._mem_bar = UsageBar()
        grid.addWidget(self._mem_bar, row, 0, 1, 2)

    def update_from(self, host: HostSnapshot) -> None:
        self._vals["node"].setText(host.hostname)
        self._vals["hyp"].setText(f"{host.hypervisor} {host.hypervisor_version}")
        self._vals["vms"].setText(f"{host.running}/{host.total} up")
        self._cpu_val.setText(f"{host.cpu_pct:.0f}% of {host.cpus}t")
        self._cpu_bar.set_fraction(host.cpu_pct / 100)
        self._mem_val.setText(f"{fmt_mem(host.mem_used_mb)}/{fmt_mem(host.memory_mb)}")
        self._mem_bar.set_fraction(
            host.mem_used_mb / host.memory_mb if host.memory_mb else 0
        )

    def set_offline(self) -> None:
        for v in self._vals.values():
            v.setText(" - ")
        self._cpu_val.setText(" - ")
        self._mem_val.setText(" - ")
        self._cpu_bar.set_fraction(0)
        self._mem_bar.set_fraction(0)
