from __future__ import annotations

import argparse
import sys

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import APP_NAME, logs, theme
from .core.connection import current_uri
from .main_window import MainWindow


def _startup_theme() -> str:
    """The stylesheet for whichever theme was last chosen.

    A theme that has since been deleted or gone unreadable falls back to the one
    vmmanager ships with, rather than starting unstyled.
    """
    from .core import themes
    from .pages.settings import saved_theme_name

    wanted = saved_theme_name()
    if wanted:
        chosen = themes.by_name(wanted)
        if chosen is not None:
            return theme.apply(chosen)
        logs.log.warning("theme %r is gone; using the built-in one", wanted)
    return theme.QSS


def main() -> None:
    parser = argparse.ArgumentParser(prog="vmmanager")
    parser.add_argument(
        "--debug", action="store_true", help="log at debug level"
    )
    parser.add_argument(
        "--screenshot",
        metavar="PATH",
        help="render the window offscreen, save a PNG, and exit (for development)",
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="run scheduled snapshots and power schedules without a window "
             "(see packaging/vmmanager-scheduler.service)",
    )
    args = parser.parse_args()

    import logging

    log_file = logs.setup(logging.DEBUG if args.debug else logging.INFO)

    if args.daemon:
        from .scheduler import run_daemon

        run_daemon()
        return

    app = QApplication(sys.argv)
    # The name shown to people. setDesktopFileName has to keep matching
    # vmmanager.desktop, and QSettings is opened with its own explicit
    # pair, so neither follows this.
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("vmmanager")
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "icon.svg")))
    theme.load_fonts()
    app.setStyleSheet(_startup_theme())

    window = MainWindow()

    def report_crash(summary: str) -> None:
        """Say so once, rather than freezing silently."""
        from .dialogs import ErrorDialog

        ErrorDialog(
            window,
            "Something went wrong",
            f"{summary}\n\nThe details are in {log_file}. "
            "The window is still usable, but this operation did not finish.",
        ).exec()

    logs.on_crash(report_crash)
    logs.log.info("started, connected to %s", current_uri())
    window.show()

    if args.screenshot:

        def grab() -> None:
            window.grab().save(args.screenshot)
            app.quit()

        QTimer.singleShot(3000, grab)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
