"""Dialog building blocks: titles, field labels, button rows, confirm/error."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


def fit_to_content(dialog: QDialog, floor: int = 0) -> None:
    """Stop the dialog being sized shorter than what it draws.

    Qt asks every child how short it is willing to be, and a word-wrapped
    QLabel answers with about one line: it would rather shrink and let its text
    spill than refuse. Nothing else in a column layout objects either, so the
    minimum height that reaches the window manager can be well under what the
    content occupies, and the body text lands on top of the buttons below it.

    Measuring the layout at the width the dialog will actually get gives the
    real figure. Wrapped text only needs less height as it widens, so the height
    at the narrowest allowed width is a floor that holds at every wider one -
    which is why the width is pinned here too. `floor` is the minimum height the
    dialog asked for itself, which is never lowered.
    """
    layout = dialog.layout()
    if layout is None:
        return
    dialog.ensurePolished()
    width = max(dialog.minimumWidth(), layout.minimumSize().width())
    needed = layout.heightForWidth(width) if layout.hasHeightForWidth() else -1
    if needed < 0:
        needed = layout.sizeHint().height()
    height = max(needed, floor)

    # Never demand more than the screen can show, or the dialog cannot be
    # placed at all. Content this tall wants a scroll area, not a taller floor.
    screen = dialog.screen() or QGuiApplication.primaryScreen()
    if screen is not None:
        room = screen.availableGeometry()
        width = min(width, room.width())
        height = min(height, room.height() - 60)  # leave the title bar somewhere
    dialog.setMinimumSize(width, height)


class SizedDialog(QDialog):
    """A dialog that measures itself, and measures itself again when it changes.

    Everything here inherits from this rather than QDialog, so a new dialog gets
    the sizing right without having to know about it.
    """

    _fitted = False
    _refitting = False
    _asked_for = 0  # the minimum height the dialog set for itself, if any

    def showEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if not self._fitted:
            self._asked_for = self.minimumHeight()
            self._fitted = True
            self._refit()
        super().showEvent(event)

    def event(self, event) -> bool:
        # Several dialogs fill in a note once they know something - the result of
        # a connection probe, why a device cannot be shared. A longer message
        # than the one measured at startup needs the floor raised again, and Qt
        # posts a layout request whenever a child's needs change.
        if event.type() == QEvent.Type.LayoutRequest and self._fitted:
            self._refit()
        return super().event(event)

    def _refit(self) -> None:
        if self._refitting:
            return  # setMinimumSize posts another layout request
        self._refitting = True
        try:
            fit_to_content(self, floor=self._asked_for)
        finally:
            self._refitting = False


def _title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("DialogTitle")
    return label

def _field_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setProperty("class", "FieldLabel")
    return label

def _buttons(dialog: QDialog, ok_text: str, danger: bool = False) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addStretch(1)
    cancel = QPushButton("Cancel")
    cancel.setProperty("class", "GhostButton")
    cancel.clicked.connect(dialog.reject)
    ok = QPushButton(ok_text)
    ok.setProperty("class", "DangerButton" if danger else "PrimaryButton")
    ok.clicked.connect(dialog.accept)
    ok.setDefault(True)
    row.addWidget(cancel)
    row.addWidget(ok)
    dialog._ok_button = ok
    return row

class ConfirmDialog(SizedDialog):
    """Generic confirmation for destructive actions."""

    def __init__(self, parent, title: str, body: str, ok_text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(12)
        box.addWidget(_title(title))
        body_label = QLabel(body)
        body_label.setWordWrap(True)
        body_label.setProperty("class", "Dim")
        box.addWidget(body_label)
        box.addSpacing(6)
        box.addLayout(_buttons(self, ok_text, danger=True))

class ErrorDialog(SizedDialog):
    def __init__(self, parent, title: str, message: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(12)
        box.addWidget(_title(title))
        body = QLabel(message)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setProperty("class", "Dim")
        box.addWidget(body)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("Close")
        ok.setProperty("class", "GhostButton")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        box.addLayout(row)


class NameDialog(SizedDialog):
    """Ask for one line of text. Refuses to hand back an empty one."""

    def __init__(self, parent, title: str, label: str, value: str = "",
                 ok_text: str = "Save") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(title))
        box.addWidget(_field_label(label))
        self.field = QLineEdit(value)
        self.field.selectAll()
        self.field.textChanged.connect(
            lambda text: self._ok_button.setEnabled(bool(text.strip()))
        )
        box.addWidget(self.field)
        box.addSpacing(6)
        box.addLayout(_buttons(self, ok_text))
        self._ok_button.setEnabled(bool(value.strip()))

    def value(self) -> str:
        return self.field.text().strip()


class DiffDialog(SizedDialog):
    """A unified diff, coloured the same way the history tab colours them.

    With `confirm` set it becomes a gate: the diff is what is about to
    happen, and the named button applies it - accept means go ahead.
    """

    def __init__(self, parent, title: str, diff: str,
                 confirm: str | None = None, note: str = "") -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QPlainTextEdit

        from ..syntax import DiffHighlighter

        self.setWindowTitle(title)
        self.setMinimumSize(720, 520)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(title))
        if note:
            note_label = QLabel(note)
            note_label.setWordWrap(True)
            note_label.setProperty("class", "Dim")
            box.addWidget(note_label)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(diff)
        self._highlighter = DiffHighlighter(view.document())
        box.addWidget(view, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        if confirm is None:
            close = QPushButton("Close")
            close.setProperty("class", "GhostButton")
            close.clicked.connect(self.accept)
            row.addWidget(close)
        else:
            cancel = QPushButton("Cancel")
            cancel.setProperty("class", "GhostButton")
            cancel.clicked.connect(self.reject)
            row.addWidget(cancel)
            go = QPushButton(confirm)
            go.setProperty("class", "PrimaryButton")
            go.clicked.connect(self.accept)
            row.addWidget(go)
        box.addLayout(row)
