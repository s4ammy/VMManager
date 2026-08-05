"""Themes page: pick a theme, make new ones, and change what they look like.

Edits land on the running application straight away rather than in a preview
pane, because the application is the preview - the sidebar, this page's own
fields and every open dialog change under the cursor as you drag a colour. The
file is written a moment later, once the typing stops.

The theme vmmanager ships with is never written to. Selecting it puts the editor
in read-only mode and points at Duplicate, so however far a theme gets from
usable there is always a way back.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QColorDialog,
    QFontComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import theme as active_theme
from ..core import themes
from ..core.restyle import apply_theme
from ..dialogs import ConfirmDialog, ErrorDialog, NameDialog
from ..logs import log
from .settings import save_theme_name

# Restyling the whole window costs about a third of a second, so an edit waits
# for a pause rather than firing per keystroke: holding a spin box arrow from 0
# to 24 then costs one restyle instead of twenty-four. Saving waits longer again,
# so typing a colour by hand is one write to the file.
APPLY_MS = 350
SAVE_MS = 900


class Swatch(QPushButton):
    """A block of colour that opens a colour picker when you press it."""

    picked = Signal(str)

    def __init__(self, note: str) -> None:
        super().__init__()
        self.setObjectName("Swatch")
        self.setFixedSize(34, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{note}\nClick to pick a colour")
        self._color = "#000000"
        self.clicked.connect(self._choose)

    def set_color(self, color: str) -> None:
        self._color = color
        # Inline, because this is the value being edited rather than part of the
        # theme: it has to show the colour it holds, whatever the theme is.
        self.setStyleSheet(
            f"#Swatch {{ background: {color}; border: 1px solid "
            f"{active_theme.BORDER_BRIGHT}; border-radius: "
            f"{active_theme.RADIUS_SM}px; }}"
        )

    def restyle(self) -> None:
        self.set_color(self._color)

    def _choose(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, "Pick a colour")
        if chosen.isValid():
            self.picked.emit(chosen.name())


class ColorRow(QWidget):
    """Swatch, hex field, label and note for one colour."""

    changed = Signal(str, str)  # field, value

    def __init__(self, token: themes.Token) -> None:
        super().__init__()
        self._token = token
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.swatch = Swatch(token.note)
        self.swatch.picked.connect(self._from_picker)
        row.addWidget(self.swatch)

        self.hex = QLineEdit()
        self.hex.setFixedWidth(92)
        self.hex.setMaxLength(7)
        self.hex.setPlaceholderText("#rrggbb")
        self.hex.textEdited.connect(self._from_text)
        row.addWidget(self.hex)

        text = QVBoxLayout()
        text.setSpacing(0)
        label = QLabel(token.label)
        note = QLabel(token.note)
        note.setProperty("class", "Faint")
        note.setWordWrap(True)
        text.addWidget(label)
        text.addWidget(note)
        row.addLayout(text, 1)

    def set_value(self, value: str) -> None:
        self.swatch.set_color(value)
        if self.hex.text().lower() != value.lower():
            self.hex.setText(value)

    def set_editable(self, editable: bool) -> None:
        self.hex.setReadOnly(not editable)
        self.swatch.setEnabled(editable)

    def _from_picker(self, value: str) -> None:
        self.hex.setText(value)
        self.swatch.set_color(value)
        self.changed.emit(self._token.field, value)

    def _from_text(self, text: str) -> None:
        """Only report a colour once it is one. Half a hex code is not."""
        value = text.strip()
        if not value.startswith("#"):
            value = "#" + value.lstrip("#")
        if themes.HEX.match(value):
            self.swatch.set_color(value)
            self.changed.emit(self._token.field, value)


class ThemesPage(QWidget):
    """The list of themes on the left, what the selected one looks like on the
    right."""

    def __init__(self) -> None:
        super().__init__()
        self._themes: list[themes.Theme] = []
        self._current: themes.Theme | None = None
        self._loading = False

        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(APPLY_MS)
        self._apply_timer.timeout.connect(self._apply_now)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_MS)
        self._save_timer.timeout.connect(self._save_now)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 0)
        outer.setSpacing(0)
        title = QLabel("Themes")
        title.setObjectName("PageTitle")
        outer.addWidget(title)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("PageSub")
        outer.addWidget(self.subtitle)
        outer.addSpacing(16)

        body = QHBoxLayout()
        body.setSpacing(18)
        body.addWidget(self._build_list(), 0)
        body.addWidget(self._build_editor(), 1)
        outer.addLayout(body, 1)

        self.refresh()

    # ---------------------------------------------------------------- build

    def _build_list(self) -> QWidget:
        holder = QWidget()
        holder.setFixedWidth(232)
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 30)
        box.setSpacing(8)

        self.list = QListWidget()
        self.list.setMaximumHeight(320)
        self.list.currentRowChanged.connect(self._select_row)
        box.addWidget(self.list)

        first = QHBoxLayout()
        first.setSpacing(8)
        self._new_btn = self._button(
            "New", "PrimaryButton", self._new,
            "Start from the theme vmmanager ships with",
        )
        self._copy_btn = self._button("Duplicate", "GhostButton", self._duplicate,
                                     "Copy the selected theme under a new name")
        first.addWidget(self._new_btn)
        first.addWidget(self._copy_btn)
        box.addLayout(first)

        second = QHBoxLayout()
        second.setSpacing(8)
        self._rename_btn = self._button("Rename", "GhostButton", self._rename)
        self._delete_btn = self._button("Delete", "GhostButton", self._delete)
        second.addWidget(self._rename_btn)
        second.addWidget(self._delete_btn)
        box.addLayout(second)

        self.where = QLabel()
        self.where.setProperty("class", "Faint")
        self.where.setWordWrap(True)
        box.addWidget(self.where)
        box.addStretch(1)
        return holder

    def _button(self, text: str, cls: str, slot, tip: str = "") -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("class", cls)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        if tip:
            btn.setToolTip(tip)
        return btn

    def _build_editor(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        box = QVBoxLayout(inner)
        box.setContentsMargins(0, 0, 8, 30)
        box.setSpacing(8)
        scroll.setWidget(inner)

        self.read_only = QLabel(
            "This is the theme vmmanager ships with, so it stays as it is. "
            "Duplicate it and change the copy."
        )
        self.read_only.setObjectName("ConsoleHint")
        self.read_only.setWordWrap(True)
        box.addWidget(self.read_only)

        self._rows: dict[str, ColorRow] = {}
        self._numbers: dict[str, QSpinBox] = {}
        self._fonts: dict[str, QFontComboBox] = {}

        for group in themes.GROUPS:
            heading = QLabel(group.upper())
            heading.setProperty("class", "FieldLabel")
            box.addSpacing(6)
            box.addWidget(heading)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(6)
            line = 0
            for token in themes.TOKENS:
                if token.group != group:
                    continue
                if token.kind == "color":
                    row = ColorRow(token)
                    row.changed.connect(self._edited)
                    self._rows[token.field] = row
                    grid.addWidget(row, line, 0, 1, 2)
                elif token.kind == "radius":
                    spin = QSpinBox()
                    spin.setRange(0, themes.MAX_RADIUS)
                    spin.setSuffix(" px")
                    spin.setFixedWidth(92)
                    spin.valueChanged.connect(
                        lambda value, f=token.field: self._edited(f, value)
                    )
                    self._numbers[token.field] = spin
                    grid.addWidget(spin, line, 0)
                    grid.addWidget(self._caption(token), line, 1)
                else:
                    picker = QFontComboBox()
                    picker.setFixedWidth(220)
                    picker.currentFontChanged.connect(
                        lambda font, f=token.field:
                        self._edited(f, font.family())
                    )
                    self._fonts[token.field] = picker
                    grid.addWidget(picker, line, 0)
                    grid.addWidget(self._caption(token), line, 1)
                line += 1
            grid.setColumnStretch(1, 1)
            box.addLayout(grid)

        box.addSpacing(10)
        box.addWidget(self._build_preview())
        box.addStretch(1)
        return scroll

    def _caption(self, token: themes.Token) -> QWidget:
        holder = QWidget()
        text = QVBoxLayout(holder)
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        label = QLabel(token.label)
        note = QLabel(token.note)
        note.setProperty("class", "Faint")
        note.setWordWrap(True)
        text.addWidget(label)
        text.addWidget(note)
        return holder

    def _build_preview(self) -> QWidget:
        """The pieces a theme is easy to get wrong, in one place.

        Everything else on this page is already a live preview. What is missing
        from it is the state colours and a filled button next to a destructive
        one, which is exactly where an unreadable pairing shows up.
        """
        card = QFrame()
        card.setObjectName("VmCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(10)
        for text, cls in (("Running", "Ok"), ("Paused", "Warn"),
                          ("Crashed", "Bad"), ("Off", "Faint")):
            dot = QLabel(f"● {text}")
            dot.setProperty("class", cls)
            row.addWidget(dot)
        row.addStretch(1)
        field = QLineEdit("editable text")
        field.setFixedWidth(140)
        row.addWidget(field)
        primary = QPushButton("Start")
        primary.setProperty("class", "PrimaryButton")
        danger = QPushButton("Delete")
        danger.setProperty("class", "DangerButton")
        row.addWidget(primary)
        row.addWidget(danger)
        return card

    # ------------------------------------------------------------- loading

    def refresh(self) -> None:
        """Re-read the theme directory, keeping the selection if it survived."""
        wanted = self._current.name if self._current else active_theme.active.name
        self._themes = themes.available()
        self._loading = True
        self.list.clear()
        for candidate in self._themes:
            item = QListWidgetItem(candidate.name)
            if candidate.builtin:
                item.setToolTip("The theme vmmanager ships with. Read-only.")
            self.list.addItem(item)
        self._loading = False
        index = next((i for i, t in enumerate(self._themes) if t.name == wanted), 0)
        self.list.setCurrentRow(index)

    def _select_row(self, index: int) -> None:
        if self._loading or not 0 <= index < len(self._themes):
            return
        chosen = self._themes[index]
        self._current = chosen
        self._load_values(chosen)
        if active_theme.active.name != chosen.name:
            # Selecting the theme already in use should cost nothing: opening
            # this page would otherwise restyle the whole window and rewrite
            # the setting for no reason.
            self._apply_now()
            save_theme_name("" if chosen.builtin else chosen.name)

    def _load_values(self, chosen: themes.Theme) -> None:
        """Put a theme's values in the editors without treating that as edits."""
        self._loading = True
        try:
            for field, row in self._rows.items():
                row.set_value(chosen.values[field])
                row.set_editable(not chosen.builtin)
            for field, spin in self._numbers.items():
                spin.setValue(int(chosen.values[field]))
                spin.setReadOnly(chosen.builtin)
            for field, picker in self._fonts.items():
                picker.setCurrentFont(QFont(str(chosen.values[field])))
                picker.setEnabled(not chosen.builtin)
        finally:
            self._loading = False
        self.read_only.setVisible(chosen.builtin)
        self._rename_btn.setEnabled(not chosen.builtin)
        self._delete_btn.setEnabled(not chosen.builtin)
        self.subtitle.setText(
            "The theme vmmanager ships with" if chosen.builtin
            else f"{len(self._themes) - 1} of your own"
        )
        self.where.setText(
            f"Read from {chosen.path}" if chosen.path else ""
        )

    # -------------------------------------------------------------- editing

    def _edited(self, field: str, value) -> None:
        if self._loading or self._current is None or self._current.builtin:
            return
        self._current.values[field] = value
        self._apply_timer.start()
        self._save_timer.start()

    def _apply_now(self) -> None:
        if self._current is None:
            return
        problems = themes.validate(self._current.values)
        if problems:
            return  # mid-edit; the next keystroke will finish the value
        apply_theme(self._current)

    def _save_now(self) -> None:
        if self._current is None or self._current.builtin:
            return
        try:
            self._current = themes.save(self._current)
        except (OSError, ValueError) as exc:
            log.warning("could not save theme %s: %s", self._current.name, exc)
            ErrorDialog(self, "Could not save the theme", str(exc)).exec()

    # -------------------------------------------------------------- actions

    def _unique(self, base: str) -> str:
        taken = {t.name for t in self._themes}
        if base not in taken:
            return base
        n = 2
        while f"{base} {n}" in taken:
            n += 1
        return f"{base} {n}"

    def _new(self) -> None:
        """A fresh start, from the shipped theme rather than the current one."""
        self._create_from(themes.builtin_theme(), self._unique("My theme"))

    def _duplicate(self) -> None:
        if self._current is None:
            return
        self._create_from(self._current, self._unique(f"{self._current.name} copy"))

    def _create_from(self, source: themes.Theme | None, suggested: str) -> None:
        dialog = NameDialog(self, "Name the theme", "name", suggested, "Create")
        if not dialog.exec():
            return
        name = dialog.value()
        if any(t.name == name for t in self._themes):
            ErrorDialog(self, "That name is taken",
                        f"There is already a theme called '{name}'.").exec()
            return
        base = source or themes.builtin_theme()
        try:
            themes.save(base.copy_as(name))
        except (OSError, ValueError) as exc:
            ErrorDialog(self, "Could not create the theme", str(exc)).exec()
            return
        self._current = None
        self.refresh()
        self._select_by_name(name)

    def _rename(self) -> None:
        if self._current is None or self._current.builtin:
            return
        was = self._current
        dialog = NameDialog(self, "Rename the theme", "name", was.name, "Rename")
        if not dialog.exec():
            return
        name = dialog.value()
        if name == was.name:
            return
        if any(t.name == name for t in self._themes):
            ErrorDialog(self, "That name is taken",
                        f"There is already a theme called '{name}'.").exec()
            return
        renamed = themes.Theme(name=name, values=dict(was.values))
        try:
            themes.save(renamed)
        except (OSError, ValueError) as exc:
            ErrorDialog(self, "Could not rename the theme", str(exc)).exec()
            return
        themes.delete(was)  # only once the new one is safely on disk
        self._current = None
        self.refresh()
        self._select_by_name(name)

    def _delete(self) -> None:
        if self._current is None or self._current.builtin:
            return
        doomed = self._current
        confirm = ConfirmDialog(
            self, f"Delete '{doomed.name}'?",
            f"{doomed.path} is removed. If this is the theme in use, the "
            "window goes back to the one vmmanager ships with.",
            "Delete",
        )
        if not confirm.exec():
            return
        themes.delete(doomed)
        self._current = None
        self.refresh()

    def _select_by_name(self, name: str) -> None:
        index = next((i for i, t in enumerate(self._themes) if t.name == name), None)
        if index is not None:
            self.list.setCurrentRow(index)
