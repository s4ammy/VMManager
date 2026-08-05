"""Serial console tab."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports for the tab modules


class SerialMixin:
    """Mixed into DetailPage; expects its attributes."""
    def _build_serial(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 0)
        box.setSpacing(10)
        self.term = TerminalWidget()
        self.term.setMinimumHeight(300)
        box.addWidget(self.term, 1)
        row = QHBoxLayout()
        self.serial_hint = QLabel("text console over the machine's serial port")
        self.serial_hint.setObjectName("ConsoleHint")
        row.addWidget(self.serial_hint)
        row.addStretch(1)
        self.serial_btn = QPushButton("Connect")
        self.serial_btn.setProperty("class", "PrimaryButton")
        self.serial_btn.clicked.connect(self._toggle_serial)
        row.addWidget(self.serial_btn)
        box.addLayout(row)
        return page

    def _toggle_serial(self) -> None:
        if self._serial is not None:
            self._stop_serial()
            return
        if not self.uuid or not self._snap or self._snap.state != "running":
            self.serial_hint.setText("machine is not running")
            return
        session = SerialSession(current_uri(), self.uuid)
        self._serial = session
        session.received.connect(self.term.feed)
        session.closed.connect(self._serial_closed)
        self.term.input_ready.connect(session.send)
        self.term.set_connected(True)
        self.term.setFocus()
        session.start()
        session.send(b"\r")  # nudge the getty so a prompt appears right away
        self.serial_btn.setText("Disconnect")
        self.serial_hint.setText(
            "connected - a getty must listen on ttyS0/hvc0 for a login prompt"
        )

    def _stop_serial(self) -> None:
        if self._serial is None:
            return
        session = self._serial
        self._serial = None
        try:
            self.term.input_ready.disconnect(session.send)
        except (RuntimeError, TypeError):
            pass
        session.stop()
        session.deleteLater()
        self.term.set_connected(False)
        self.serial_btn.setText("Connect")
        self.serial_hint.setText("text console over the machine's serial port")

    def _serial_closed(self, reason: str) -> None:
        if self._serial is None:
            return
        self._stop_serial()
        if reason:
            self.serial_hint.setText(f"serial console failed: {reason}")
