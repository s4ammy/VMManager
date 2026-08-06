"""The machine card - one rack bay per domain."""

from __future__ import annotations

from PySide6.QtCore import (
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .. import theme
from ..libvirt_service import DomainSnapshot
from .format import fmt_mem, fmt_size
from .indicators import Led, Rail, Sparkline


def _stat(key: str) -> tuple[QVBoxLayout, QLabel]:
    box = QVBoxLayout()
    box.setSpacing(1)
    k = QLabel(key.upper())
    k.setProperty("class", "StatKey")
    v = QLabel(" - ")
    v.setProperty("class", "StatVal")
    box.addWidget(k)
    box.addWidget(v)
    return box, v

class VmCard(QFrame):
    """One machine, presented like a rack bay: rail, LED, readouts, controls.

    Click anywhere on the card to open the machine's detail page.
    """

    action = Signal(str, str)  # uuid, op
    open_detail = Signal(str)  # uuid
    context = Signal(str, object)  # uuid, QPoint (global)
    toggle_select = Signal(str)  # uuid (ctrl+click)

    def __init__(self, snap: DomainSnapshot) -> None:
        super().__init__()
        self.setObjectName("VmCard")
        self.uuid = snap.uuid
        self.setMinimumHeight(72)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._rail = Rail()
        outer.addWidget(self._rail)

        body = QHBoxLayout()
        body.setContentsMargins(18, 14, 18, 14)
        body.setSpacing(16)
        outer.addLayout(body, 1)

        self._led = Led()
        body.addWidget(self._led)

        self._os_icon = QLabel()
        self._os_icon.setFixedSize(24, 24)
        self._os_icon.setToolTip("")
        self._os_icon.hide()
        body.addWidget(self._os_icon)

        self._thumb = QLabel()
        self._thumb.setObjectName("VmThumb")
        self._thumb.setFixedSize(104, 58)
        self._thumb.setScaledContents(True)
        self._thumb.hide()
        body.addWidget(self._thumb)

        name_box = QVBoxLayout()
        name_box.setSpacing(2)
        self._name = QLabel(snap.name)
        self._name.setObjectName("VmName")
        state_row = QHBoxLayout()
        state_row.setSpacing(10)
        self._state = QLabel()
        self._state.setObjectName("VmState")
        self._ip = QLabel()
        self._ip.setProperty("class", "StatVal")
        self._tags = QLabel()
        self._tags.setProperty("class", "StatVal Faint")
        self._derived = QLabel()
        self._derived.setObjectName("ConsoleHint")
        self._health = QLabel()
        self._health.setObjectName("VmState")
        self._health.hide()
        state_row.addWidget(self._state)
        state_row.addWidget(self._ip)
        state_row.addWidget(self._derived)
        state_row.addWidget(self._tags)
        state_row.addWidget(self._health)
        state_row.addStretch(1)
        name_box.addWidget(self._name)
        name_box.addLayout(state_row)
        body.addLayout(name_box, 2)

        spark_box = QVBoxLayout()
        spark_box.setSpacing(1)
        spark_key = QLabel("CPU")
        spark_key.setProperty("class", "StatKey")
        self._spark = Sparkline(max_value=100.0)
        self._spark.setFixedSize(120, 30)
        spark_box.addWidget(spark_key)
        spark_box.addWidget(self._spark)
        self._spark_key = spark_key
        body.addLayout(spark_box)

        stats = QHBoxLayout()
        stats.setSpacing(20)
        cpu_box, self._cpu = _stat("cpu")
        mem_box, self._mem = _stat("mem")
        vcpu_box, self._vcpu = _stat("vcpu")
        stats.addLayout(cpu_box)
        stats.addLayout(mem_box)
        stats.addLayout(vcpu_box)
        body.addLayout(stats)

        self._primary_op = "start"
        self._start = QPushButton("Start")
        self._start.setProperty("class", "PrimaryButton")
        self._start.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start.clicked.connect(
            lambda: self.action.emit(self.uuid, self._primary_op)
        )

        self._stop = QPushButton("Shut down")
        self._stop.setProperty("class", "GhostButton")
        self._stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop.clicked.connect(lambda: self.action.emit(self.uuid, "shutdown"))

        body.addWidget(self._start)
        body.addWidget(self._stop)

        self._snap = None
        self._mode_name = ""
        self.update_from(snap)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.toggle_select.emit(self.uuid)
            else:
                self.open_detail.emit(self.uuid)
        elif event.button() == Qt.MouseButton.RightButton:
            self.context.emit(self.uuid, event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_thumbnail(self, pixmap) -> None:
        if pixmap is None:
            self._thumb.hide()
            self._thumb.clear()
        else:
            self._thumb.setPixmap(pixmap)
            self._thumb.show()

    def set_mode(self, name: str) -> None:
        """Which named configuration this machine is defined as.

        Shown on the state line, the way managed save already is, so it reads
        as another fact about the machine rather than a stray label.
        """
        self._mode_name = name
        if self._snap is not None:
            self.update_from(self._snap)

    def set_template_use(self, clones: int, shared_bytes: int) -> None:
        """How much this template is earning its keep."""
        if clones < 0:
            self._derived.hide()
            return
        parts = ["no clones yet" if not clones else
                 f"{clones} clone{'s' if clones != 1 else ''}"]
        if shared_bytes:
            parts.append(f"{fmt_size(shared_bytes)} shared")
        self._derived.setText(" · ".join(parts))
        self._derived.show()

    def set_health(self, worst) -> None:
        """worst: (mountpoint, used %) or None."""
        if worst is None or worst[1] < 85:
            self._health.hide()
            return
        mount, pct = worst
        color = theme.DANGER if pct >= 95 else theme.WARN
        self._health.setText(f"⚠ {mount} {pct:.0f}%")
        self._health.setStyleSheet(f"color: {color};")
        self._health.show()

    def update_from(self, snap: DomainSnapshot) -> None:
        self._snap = snap
        self._name.setText(snap.name)
        state_text = snap.state.upper()
        if snap.has_managed_save:
            state_text += " · SAVED"
        if snap.is_template:
            state_text = "TEMPLATE"
        mode = getattr(self, "_mode_name", "")
        if mode:
            state_text += f" · {mode.upper()}"
        self._state.setText(state_text)
        self._state.setStyleSheet(
            f"color: {theme.ACCENT if snap.is_template else theme.state_color(snap.state)};"
        )
        self._ip.setText(snap.ip or "")
        if not snap.is_template:
            self._derived.hide()
        self._tags.setText(" ".join(f"#{t}" for t in snap.tags))
        self._update_os_icon(snap)
        self._led.set_state(snap.state)
        self._rail.set_state(snap.state)
        running = snap.state == "running"
        if not running:
            self.set_thumbnail(None)
            self.set_health(None)
        self._spark.setVisible(running)
        self._spark_key.setVisible(running)
        if running:
            self._spark.set_values([u.cpu_pct for u in snap.history])
            self._cpu.setText(f"{snap.usage.cpu_pct:.0f}%")
            self._mem.setText(fmt_mem(snap.usage.mem_mb))
        else:
            self._cpu.setText(" - ")
            self._mem.setText(fmt_mem(snap.memory_mb))
        self._vcpu.setText(str(snap.vcpus))
        paused = snap.state in ("paused", "suspended")
        transitional = snap.state in ("shutting-down",)
        self._primary_op = "resume" if paused else "start"
        self._start.setText(
            "Resume" if paused else ("Restore" if snap.has_managed_save else "Start")
        )
        self._start.setVisible(not running and not transitional and not snap.is_template)
        self._stop.setVisible(running or paused or transitional)
        self._stop.setEnabled(not transitional)
        self._stop.setText("Shutting down…" if transitional else "Shut down")


    def _update_os_icon(self, snap: DomainSnapshot) -> None:
        """Show the guest's OS, when the user wants it and we know it."""
        from ..pages.settings import os_icons_enabled

        if not os_icons_enabled() or not snap.os_key or snap.os_key == "unknown":
            self._os_icon.hide()
            return
        from ..core.osident import display_name
        from ..data.oslogos import logo_pixmap

        self._os_icon.setPixmap(logo_pixmap(snap.os_key, 22))
        label = display_name(snap.os_key)
        self._os_icon.setToolTip(
            f"{label} (pinned)" if snap.os_icon_override else f"{label} (detected)"
        )
        self._os_icon.show()
