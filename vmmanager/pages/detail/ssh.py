"""SSH terminal tab."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports for the tab modules


class SshMixin:
    """Mixed into DetailPage; expects its attributes."""
    def _build_ssh(self) -> QWidget:
        from ...console.serialterm import TerminalWidget as _Term

        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 0)
        box.setSpacing(10)
        self.ssh_term = _Term()
        self.ssh_term.setMinimumHeight(300)
        box.addWidget(self.ssh_term, 1)
        row = QHBoxLayout()
        self.ssh_hint = QLabel("ssh straight to the machine's DHCP address")
        self.ssh_hint.setObjectName("ConsoleHint")
        row.addWidget(self.ssh_hint)
        row.addStretch(1)
        user_label = QLabel("USER")
        user_label.setProperty("class", "StatKey")
        row.addWidget(user_label)
        from PySide6.QtWidgets import QLineEdit

        from PySide6.QtCore import QSettings

        self.ssh_user = QLineEdit(
            QSettings("vmmanager", "vmmanager").value("ssh_user", "admin")
        )
        self.ssh_user.setFixedWidth(140)
        row.addWidget(self.ssh_user)
        self.ssh_btn = QPushButton("Connect")
        self.ssh_btn.setProperty("class", "PrimaryButton")
        self.ssh_btn.clicked.connect(self._toggle_ssh)
        row.addWidget(self.ssh_btn)
        box.addLayout(row)
        self._ssh: object | None = None
        return page

    def _toggle_ssh(self) -> None:
        if self._ssh is not None:
            self._stop_ssh()
            return
        if not self._snap or not self._snap.ip:
            self.ssh_hint.setText("no IP address yet, is the machine running?")
            return
        from ...console.sshterm import SshSession

        user = self.ssh_user.text().strip() or "root"
        from PySide6.QtCore import QSettings

        QSettings("vmmanager", "vmmanager").setValue("ssh_user", user)
        session = SshSession(self._snap.ip, user, parent=self)
        self._ssh = session
        session.received.connect(self.ssh_term.feed)
        session.closed.connect(lambda _r: self._stop_ssh())
        self.ssh_term.input_ready.connect(session.send)
        self.ssh_term.size_changed.connect(session.set_winsize)
        self.ssh_term.set_connected(True)
        self.ssh_term.setFocus()
        rows, cols = self.ssh_term.grid_size
        session.start(rows, cols)
        self.ssh_btn.setText("Disconnect")
        self.ssh_hint.setText(f"ssh {user}@{self._snap.ip}")

    def _stop_ssh(self) -> None:
        if self._ssh is None:
            return
        session = self._ssh
        self._ssh = None
        try:
            self.ssh_term.input_ready.disconnect(session.send)
            self.ssh_term.size_changed.disconnect(session.set_winsize)
        except (RuntimeError, TypeError):
            pass
        session.stop()
        self.ssh_term.set_connected(False)
        self.ssh_btn.setText("Connect")
        self.ssh_hint.setText("ssh straight to the machine's DHCP address")
