"""Byte and memory formatting shared by every readout."""

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


def fmt_bytes(bps: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if bps < 1024:
            return f"{bps:.0f}{unit}"
        bps /= 1024
    return f"{bps:.1f}T"

def fmt_size(n: float) -> str:
    """Bytes as a size. Keeps a decimal from gigabytes up, where whole units
    are too coarse: a 1.5G image should not read as 2G."""
    for unit in ("B", "K", "M"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}G" if n < 1024 else f"{n / 1024:.1f}T"

def fmt_mem(mb: float) -> str:
    return f"{mb / 1024:.1f}G" if mb >= 1024 else f"{mb:.0f}M"
