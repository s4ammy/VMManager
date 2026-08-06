"""Painted primitives: state LED, rail, sparkline, usage bar."""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QSizePolicy,
    QWidget,
)

from .. import theme


class Led(QWidget):
    """Small status dot; softly pulses while the machine is running."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(10, 10)
        self._state = ""
        self._color = QColor(theme.TEXT_FAINT)
        self._glow = 0.0
        self._anim = QPropertyAnimation(self, b"glow", self)
        self._anim.setDuration(2400)
        self._anim.setStartValue(0.25)
        self._anim.setKeyValueAt(0.5, 1.0)
        self._anim.setEndValue(0.25)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.setLoopCount(-1)

    def _get_glow(self) -> float:
        return self._glow

    def _set_glow(self, v: float) -> None:
        self._glow = v
        self.update()

    glow = Property(float, _get_glow, _set_glow)

    def restyle(self) -> None:
        """The colour is kept as a QColor, so re-read it on a theme change."""
        self._color = QColor(theme.state_color(self._state))

    def set_state(self, state: str) -> None:
        self._state = state
        self._color = QColor(theme.state_color(state))
        if state == "running":
            if self._anim.state() != QPropertyAnimation.State.Running:
                self._anim.start()
        else:
            self._anim.stop()
            self._glow = 0.0
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if self._glow > 0:
            halo = QColor(self._color)
            halo.setAlphaF(0.35 * self._glow)
            p.setBrush(halo)
            p.drawEllipse(self.rect())
        p.setBrush(self._color)
        p.drawEllipse(self.rect().adjusted(2, 2, -2, -2))
        p.end()

class Rail(QFrame):
    """The colored strip on a card's left edge, its state at a glance."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedWidth(4)
        self._state = ""

    def restyle(self) -> None:
        self.set_state(self._state)

    def set_state(self, state: str) -> None:
        self._state = state
        color = theme.state_color(state)
        self.setStyleSheet(
            f"background: {color}; border-top-left-radius: 12px;"
            f"border-bottom-left-radius: 12px;"
        )

class Sparkline(QWidget):
    """Single-series line chart: 2px accent stroke over a soft fill.

    Scales to a fixed maximum when given (percentages) or to the data's own
    peak (rates), and always draws from a zero baseline.
    """

    def __init__(self, max_value: float | None = None,
                 color: str | None = None) -> None:
        super().__init__()
        self._values: list[float] = []
        self._max = max_value
        # Resolved at paint, not here: see the note in UsageBar.
        self._color = color
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_values(self, values: list[float]) -> None:
        self._values = values
        self.update()

    def set_max(self, max_value: float | None) -> None:
        self._max = max_value

    def paintEvent(self, _event) -> None:
        if len(self._values) < 2:
            return
        w, h = self.width(), self.height()
        top_pad = 3
        peak = self._max if self._max else max(max(self._values), 1e-9)
        peak = max(peak, 1e-9)
        n = len(self._values)
        step = w / (n - 1)
        points = [
            QPointF(i * step, top_pad + (h - top_pad) * (1 - min(v / peak, 1.0)))
            for i, v in enumerate(self._values)
        ]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath(points[0])
        for pt in points[1:]:
            path.lineTo(pt)
        fill = QPainterPath(path)
        fill.lineTo(points[-1].x(), h)
        fill.lineTo(points[0].x(), h)
        fill.closeSubpath()
        line = QColor(self._color or theme.ACCENT)
        fill_color = QColor(line)
        fill_color.setAlphaF(0.13)
        p.fillPath(fill, fill_color)
        pen = QPen(line, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPath(path)
        p.end()

class CoreBars(QWidget):
    """One bar per vCPU, drawn in place of the overall line.

    The overall figure is the guest's total divided by its vCPU count, so a
    machine running one thread flat out reads 8% on twelve cores and looks
    idle. This is the view that says otherwise: which cores are working and
    which are not, at a glance, without reading twelve numbers.

    Current values rather than history - the line chart already carries the
    history, and twelve overlapping traces in a card this size is a mess
    rather than a chart.
    """

    def __init__(self) -> None:
        super().__init__()
        self._values: list[float] = []
        self.setMinimumHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_values(self, values) -> None:
        self._values = [max(0.0, min(100.0, float(v))) for v in values]
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        if not self._values:
            return
        from PySide6.QtGui import QFont, QFontMetrics

        w, h = self.width(), self.height()
        n = len(self._values)
        gap = 3 if n <= 16 else 2
        label_h = 12 if n <= 24 else 0
        bar_w = max(2.0, (w - gap * (n - 1)) / n)
        track = QColor(theme.BG_INSET)
        accent = QColor(theme.ACCENT)
        hot = QColor(theme.WARN)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        top = 2
        height = max(4, h - top - label_h)
        font = QFont(theme.MONO, 7)
        p.setFont(font)
        metrics = QFontMetrics(font)
        for i, value in enumerate(self._values):
            x = i * (bar_w + gap)
            p.fillRect(QRectF(x, top, bar_w, height), track)
            filled = height * (value / 100.0)
            if filled > 0:
                # A core sitting at the top is the thing worth spotting.
                colour = hot if value >= 90 else accent
                p.fillRect(
                    QRectF(x, top + height - filled, bar_w, filled), colour
                )
            if label_h and bar_w >= metrics.horizontalAdvance("88"):
                p.setPen(QColor(theme.TEXT_FAINT))
                p.drawText(
                    QRectF(x, top + height, bar_w, label_h),
                    Qt.AlignmentFlag.AlignCenter, str(i),
                )
        p.end()


class UsageBar(QWidget):
    """Thin capacity bar: accent fill on an inset track."""

    def __init__(self, color: str | None = None) -> None:
        super().__init__()
        self._fraction = 0.0
        # Not `color: str = theme.ACCENT`: a default argument is evaluated once,
        # when this module is imported, which would pin the bar to whatever the
        # accent was then and leave it behind on a theme change.
        self._color = color
        self.setFixedHeight(5)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_fraction(self, f: float) -> None:
        self._fraction = max(0.0, min(1.0, f))
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.BG_INSET))
        p.drawRoundedRect(self.rect(), theme.RADIUS_SM, theme.RADIUS_SM)
        if self._fraction > 0:
            fill = self.rect().adjusted(0, 0, -int(self.width() * (1 - self._fraction)), 0)
            p.setBrush(QColor(self._color or theme.ACCENT))
            p.drawRoundedRect(fill, theme.RADIUS_SM, theme.RADIUS_SM)
        p.end()
