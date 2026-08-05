"""Command palette: Ctrl+K fuzzy launcher over machines, actions and pages."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QVBoxLayout


def _score(query: str, text: str) -> int | None:
    """Subsequence match; lower is better, None is no match."""
    q, t = query.lower(), text.lower()
    if not q:
        return 100
    pos, score, last = 0, 0, -1
    for ch in q:
        found = t.find(ch, pos)
        if found < 0:
            return None
        if last >= 0:
            score += found - last - 1  # penalize gaps
        last = found
        pos = found + 1
    return score + t.find(q[0])  # earlier starts rank higher


class CommandPalette(QDialog):
    def __init__(self, parent, entries: list[tuple[str, object]]) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("Palette")
        self._entries = entries
        self.setFixedWidth(520)

        box = QVBoxLayout(self)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a machine or action…")
        self.input.textChanged.connect(self._refilter)
        box.addWidget(self.input)
        self.list = QListWidget()
        self.list.setMaximumHeight(320)
        self.list.itemActivated.connect(lambda _i: self._run())
        box.addWidget(self.list)
        self._refilter("")
        self.input.setFocus()

        # center over the parent window
        if parent is not None:
            geo = parent.geometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2, geo.y() + 120
            )

    def _refilter(self, query: str) -> None:
        scored = []
        for i, (label, _cb) in enumerate(self._entries):
            s = _score(query, label)
            if s is not None:
                scored.append((s, i, label))
        scored.sort()
        self.list.clear()
        for _s, i, label in scored[:12]:
            self.list.addItem(label)
            self.list.item(self.list.count() - 1).setData(
                Qt.ItemDataRole.UserRole, i
            )
        if self.list.count():
            self.list.setCurrentRow(0)

    def _run(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        _label, callback = self._entries[item.data(Qt.ItemDataRole.UserRole)]
        self.accept()
        callback()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._run()
            return
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            row = self.list.currentRow() + (1 if key == Qt.Key.Key_Down else -1)
            if 0 <= row < self.list.count():
                self.list.setCurrentRow(row)
            return
        super().keyPressEvent(event)
