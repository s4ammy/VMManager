"""XML syntax highlighting for the domain-XML editor, in theme colors."""

from __future__ import annotations

import re
import weakref

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from shiboken6 import isValid

from . import theme

# Every highlighter alive right now, so a theme change can reach the ones
# already on screen. Weak, so a closed dialog's highlighter is not kept.
_live: weakref.WeakSet = weakref.WeakSet()


def _fmt(color: str, italic: bool = False, bold: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if italic:
        f.setFontItalic(True)
    if bold:
        f.setFontWeight(QFont.Weight.DemiBold)
    return f


class ThemedHighlighter(QSyntaxHighlighter):
    """A highlighter whose colours follow the theme.

    Formats are recoloured in place rather than replaced, because XmlHighlighter
    also keeps them in its rule table - handing out new objects would leave that
    table pointing at the old ones.
    """

    def __init__(self, document) -> None:
        super().__init__(document)
        self._spec: dict[str, str] = {}
        _live.add(self)

    def _define(self, name: str, token: str, italic: bool = False,
                bold: bool = False) -> QTextCharFormat:
        """Name an attribute after a theme token rather than a colour."""
        fmt = _fmt(getattr(theme, token), italic=italic, bold=bold)
        setattr(self, name, fmt)
        self._spec[name] = token
        return fmt

    def restyle(self) -> None:
        for name, token in self._spec.items():
            getattr(self, name).setForeground(QColor(getattr(theme, token)))
        self.rehighlight()


def restyle_all() -> None:
    """Recolour every highlighter on screen. Called when the theme changes.

    A highlighter dies with the document it was attached to, and the Python
    wrapper can outlive the C++ object it points at - so ask before touching it,
    or a theme change after a dialog closed raises out of the event loop.
    """
    for highlighter in list(_live):
        if isValid(highlighter):
            highlighter.restyle()


class DiffHighlighter(ThemedHighlighter):
    """Unified diffs: additions green, removals red, hunk headers accent."""

    def __init__(self, document) -> None:
        super().__init__(document)
        self._add = self._define("_add", "OK")
        self._remove = self._define("_remove", "DANGER")
        self._hunk = self._define("_hunk", "ACCENT", bold=True)
        self._meta = self._define("_meta", "TEXT_FAINT", italic=True)

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        if text.startswith("@@"):
            self.setFormat(0, len(text), self._hunk)
        elif text.startswith(("+++", "---")):
            self.setFormat(0, len(text), self._meta)
        elif text.startswith("+"):
            self.setFormat(0, len(text), self._add)
        elif text.startswith("-"):
            self.setFormat(0, len(text), self._remove)


class XmlHighlighter(ThemedHighlighter):
    """Element names in accent, attributes dimmed, values warm, comments faint."""

    _IN_COMMENT = 1

    def __init__(self, document) -> None:
        super().__init__(document)
        self._tag = self._define("_tag", "ACCENT", bold=True)
        self._bracket = self._define("_bracket", "TEXT_FAINT")
        self._attr = self._define("_attr", "OK")
        self._value = self._define("_value", "WARN")
        self._text = self._define("_text", "TEXT_DIM")
        self._comment = self._define("_comment", "TEXT_FAINT", italic=True)
        self._rules = [
            (re.compile(r"</?\s*([\w:.-]+)"), 1, self._tag),
            (re.compile(r"[<>/?]+"), 0, self._bracket),
            (re.compile(r"\b([\w:.-]+)\s*(?==)"), 1, self._attr),
            (re.compile(r"'[^']*'|\"[^\"]*\""), 0, self._value),
        ]
        self._comment_start = re.compile(r"<!--")
        self._comment_end = re.compile(r"-->")

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        for pattern, group, fmt in self._rules:
            for m in pattern.finditer(text):
                start, end = m.span(group)
                self.setFormat(start, end - start, fmt)

        # multi-line comments
        self.setCurrentBlockState(0)
        start = 0
        if self.previousBlockState() != self._IN_COMMENT:
            m = self._comment_start.search(text)
            start = m.start() if m else -1
        while start >= 0:
            m_end = self._comment_end.search(text, start)
            if m_end is None:
                self.setCurrentBlockState(self._IN_COMMENT)
                self.setFormat(start, len(text) - start, self._comment)
                break
            length = m_end.end() - start
            self.setFormat(start, length, self._comment)
            m_next = self._comment_start.search(text, m_end.end())
            start = m_next.start() if m_next else -1
