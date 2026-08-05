"""Logging, and somewhere for a crash to land.

An exception raised inside a Qt slot goes to stderr and the window keeps
running but stops responding. It looks like a hang rather than a crash, so we
log it and say so.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import traceback
from pathlib import Path
from typing import Callable

LOG_DIR = Path.home() / ".cache" / "vmmanager"
LOG_FILE = LOG_DIR / "vmmanager.log"

log = logging.getLogger("vmmanager")

_on_crash: Callable[[str], None] | None = None


def setup(level: int = logging.INFO, to_stderr: bool = True) -> Path:
    """Start logging, and install the excepthook. Returns the log path."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(level)
    log.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    # a megabyte each, three back: this session and the one before it
    rotating = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    rotating.setFormatter(fmt)
    log.addHandler(rotating)
    if to_stderr:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        log.addHandler(stream)

    sys.excepthook = _excepthook
    _install_qt_handler()
    return LOG_FILE


def on_crash(callback: Callable[[str], None]) -> None:
    """Set what to show the user when an exception escapes a slot."""
    global _on_crash
    _on_crash = callback


def _excepthook(exc_type, exc, tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("unhandled exception\n%s", text)
    if _on_crash is not None:
        try:
            _on_crash(f"{exc_type.__name__}: {exc}")
        except Exception:  # noqa: BLE001 - reporting must not mask the crash
            log.exception("could not report the crash to the user")


_QT_LEVELS = {0: logging.DEBUG, 1: logging.WARNING, 2: logging.ERROR, 3: logging.CRITICAL}


def _install_qt_handler() -> None:
    """Route Qt's own messages into the same log."""
    try:
        from PySide6.QtCore import qInstallMessageHandler
    except ImportError:  # pragma: no cover
        return

    def handler(mode, _context, message: str) -> None:
        log.log(_QT_LEVELS.get(int(mode), logging.INFO), "qt: %s", message)

    qInstallMessageHandler(handler)
