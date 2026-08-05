"""SSH under a pty, feeding the same terminal widget as the serial console."""

from __future__ import annotations

import fcntl
import os
import pty
import signal
import struct
import termios

from PySide6.QtCore import QObject, QSocketNotifier, Signal


class SshSession(QObject):
    """Forks ssh on a pseudo-terminal; bytes flow via a QSocketNotifier."""

    received = Signal(bytes)
    closed = Signal(str)

    def __init__(self, host: str, user: str, parent=None) -> None:
        super().__init__(parent)
        self._host = host
        self._user = user
        self._pid = 0
        self._fd = -1
        self._notifier: QSocketNotifier | None = None

    def start(self, rows: int = 24, cols: int = 80) -> None:
        pid, fd = pty.fork()
        if pid == 0:  # child
            os.environ["TERM"] = "linux"
            os.execvp(
                "ssh",
                [
                    "ssh",
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "ConnectTimeout=10",
                    f"{self._user}@{self._host}",
                ],
            )
            os._exit(127)  # exec failed
        self._pid = pid
        self._fd = fd
        self.set_winsize(rows, cols)
        self._notifier = QSocketNotifier(fd, QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._on_readable)

    def _on_readable(self) -> None:
        try:
            data = os.read(self._fd, 4096)
        except OSError:
            data = b""
        if not data:
            self.stop(emit_closed=True)
            return
        self.received.emit(data)

    def send(self, data: bytes) -> None:
        if self._fd >= 0:
            try:
                os.write(self._fd, data)
            except OSError:
                pass

    def set_winsize(self, rows: int, cols: int) -> None:
        if self._fd >= 0:
            try:
                fcntl.ioctl(
                    self._fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0),
                )
            except OSError:
                pass

    def stop(self, emit_closed: bool = False) -> None:
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        if self._pid > 0:
            try:
                os.kill(self._pid, signal.SIGHUP)
                os.waitpid(self._pid, os.WNOHANG)
            except (OSError, ChildProcessError):
                pass
            self._pid = 0
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1
        if emit_closed:
            self.closed.emit("session ended")

    @property
    def running(self) -> bool:
        return self._pid > 0
