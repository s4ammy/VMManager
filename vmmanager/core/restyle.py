"""Applying a theme to an application that is already running.

Setting the stylesheet on the QApplication does nearly all of it: Qt works out
which rules every widget matches again, whether it is on screen or not. What it
cannot do is reach the places that copy a colour out of the theme when they are
built rather than reading it as they draw - a colour written into rich-text
markup, a QColor kept in an attribute, a syntax highlighter's character formats,
a stylesheet set on one widget.

Rather than list those from here, a widget that has something to rebuild says so
by having a `restyle()` method, and this calls it.

It is not cheap. Restyling vmmanager's window - seven pages, the detail page's
eight tabs, all alive whether shown or not - takes about a third of a second,
almost all of it inside Qt's own recalculation. That is why the Themes page waits
for a pause in the typing rather than applying on every keystroke.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget

from .. import theme
from ..logs import log
from .themes import Theme


def apply_theme(theme_to_use: Theme, app: QApplication | None = None) -> None:
    """Switch the running application to this theme."""
    stylesheet = theme.apply(theme_to_use)
    app = app or QApplication.instance()
    if app is None:
        return  # applied to the module; nothing on screen to update

    app.setStyleSheet(stylesheet)

    from ..syntax import restyle_all

    restyle_all()
    for window in app.topLevelWidgets():
        restyle_tree(window)


def restyle_tree(root: QWidget) -> None:
    """Let everything under here rebuild whatever it keeps a colour in.

    No unpolish/polish pass: setting the application stylesheet already made Qt
    re-resolve every widget, and doing it again was measured to change nothing.
    """
    for widget in [root, *root.findChildren(QWidget)]:
        own = getattr(widget, "restyle", None)
        if callable(own):
            try:
                own()
            except Exception:  # one themable widget must not break the switch
                log.exception("restyle failed for %s", type(widget).__name__)
    root.update()
