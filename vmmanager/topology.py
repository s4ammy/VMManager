"""Network topology map: networks up top, machines below, edges between."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme


class TopologyView(QWidget):
    """Painted graph of virtual networks and the machines attached to them."""

    open_detail = Signal(str)  # uuid

    NET_W, NET_H = 170, 44
    VM_W, VM_H = 150, 40

    def __init__(self) -> None:
        super().__init__()
        self._networks: list = []  # NetworkInfo
        self._domains: list = []  # DomainSnapshot
        self._vm_rects: list[tuple[QRectF, str]] = []
        self.setMinimumHeight(320)

    def set_data(self, networks: list, domains: list) -> None:
        self._networks = networks
        self._domains = domains
        self.update()

    def _positions(self):
        w = max(self.width(), 400)
        nets = [n.name for n in self._networks]
        net_pos: dict[str, QRectF] = {}
        n_count = max(len(nets), 1)
        for i, name in enumerate(nets):
            cx = w * (i + 1) / (n_count + 1)
            net_pos[name] = QRectF(cx - self.NET_W / 2, 30, self.NET_W, self.NET_H)
        vm_pos: dict[str, QRectF] = {}
        vms = self._domains
        per_row = max(1, (w - 40) // (self.VM_W + 24))
        for i, d in enumerate(vms):
            row, col = divmod(i, per_row)
            row_count = min(per_row, len(vms) - row * per_row)
            cx = w * (col + 1) / (row_count + 1)
            vm_pos[d.uuid] = QRectF(
                cx - self.VM_W / 2, 170 + row * (self.VM_H + 46), self.VM_W, self.VM_H
            )
        return net_pos, vm_pos

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.BG))
        net_pos, vm_pos = self._positions()
        self._vm_rects = []

        # edges first
        pen = QPen(QColor(theme.BORDER_BRIGHT), 1.4)
        p.setPen(pen)
        for d in self._domains:
            vrect = vm_pos.get(d.uuid)
            if vrect is None:
                continue
            for net in d.networks:
                nrect = net_pos.get(net)
                if nrect is None:
                    continue
                color = QColor(
                    theme.OK if d.state == "running" else theme.BORDER_BRIGHT
                )
                p.setPen(QPen(color, 1.4))
                p.drawLine(
                    int(vrect.center().x()), int(vrect.top()),
                    int(nrect.center().x()), int(nrect.bottom()),
                )

        label_font = QFont(theme.BODY, 10)
        mono = QFont(theme.MONO, 8)

        for name, rect in net_pos.items():
            net = next((n for n in self._networks if n.name == name), None)
            active = bool(net and net.active)
            p.setPen(QPen(QColor(theme.ACCENT if active else theme.BORDER), 1.5))
            p.setBrush(QColor(theme.ACCENT_DIM if active else theme.BG_RAISED))
            p.drawRoundedRect(rect, theme.RADIUS, theme.RADIUS)
            p.setPen(QColor(theme.TEXT))
            p.setFont(label_font)
            p.drawText(
                rect.adjusted(10, 4, -10, -18), Qt.AlignmentFlag.AlignLeft, name
            )
            p.setPen(QColor(theme.TEXT_FAINT))
            p.setFont(mono)
            mode = net.mode if net else "?"
            p.drawText(
                rect.adjusted(10, 20, -10, -4), Qt.AlignmentFlag.AlignLeft,
                f"{mode} · {'up' if active else 'down'}",
            )

        for d in self._domains:
            rect = vm_pos.get(d.uuid)
            if rect is None:
                continue
            self._vm_rects.append((rect, d.uuid))
            p.setPen(QPen(QColor(theme.BORDER_BRIGHT), 1.2))
            p.setBrush(QColor(theme.BG_RAISED))
            p.drawRoundedRect(rect, theme.RADIUS, theme.RADIUS)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.state_color(d.state)))
            p.drawEllipse(int(rect.left() + 10), int(rect.center().y() - 4), 8, 8)
            p.setPen(QColor(theme.TEXT))
            p.setFont(label_font)
            p.drawText(
                rect.adjusted(26, 0, -8, 0),
                Qt.AlignmentFlag.AlignVCenter,
                d.name,
            )
        p.end()

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        for rect, uuid in self._vm_rects:
            if rect.contains(pos):
                self.open_detail.emit(uuid)
                return
        super().mousePressEvent(event)
