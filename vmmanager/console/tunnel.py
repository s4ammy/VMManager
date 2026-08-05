"""SSH port-forward tunnels for console access to remote libvirt hosts.

A VNC/SPICE display on a remote host listens on that host's loopback, so the
console widgets can't reach it directly. When the active connection is
qemu+ssh://…, we forward a free local port through ssh and point the console
widget at that instead - the same trick virt-manager uses.
"""

from __future__ import annotations

import socket
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QObject, QProcess, QTimer, Signal


def ssh_target_of(uri: str) -> tuple[str, str | None] | None:
    """("user@host", keyfile) when the URI transports over SSH, else None."""
    parsed = urlparse(uri)
    if "+ssh" not in parsed.scheme or not parsed.hostname:
        return None
    target = parsed.hostname
    if parsed.username:
        target = f"{parsed.username}@{target}"
    keyfile = None
    query = parse_qs(parsed.query)
    if query.get("keyfile"):
        keyfile = query["keyfile"][0]
    return target, keyfile


def is_remote_uri(uri: str) -> bool:
    return bool(urlparse(uri).hostname)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SSHTunnel(QObject):
    """ssh -N -L <local>:host:port, with readiness probing."""

    ready = Signal(int)  # local port
    failed = Signal(str)

    def __init__(
        self, target: str, remote_host: str, remote_port: int,
        keyfile: str | None = None, parent=None,
    ) -> None:
        super().__init__(parent)
        self._target = target
        self._remote = (remote_host or "127.0.0.1", remote_port)
        self._keyfile = keyfile
        self.local_port = _free_port()
        self._proc: QProcess | None = None
        self._probe = QTimer(self)
        self._probe.setInterval(200)
        self._probe.timeout.connect(self._check_ready)
        self._attempts = 0

    def start(self) -> None:
        args = [
            "-N",
            "-o", "BatchMode=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ConnectTimeout=10",
            "-L", f"127.0.0.1:{self.local_port}:{self._remote[0]}:{self._remote[1]}",
        ]
        if self._keyfile:
            args += ["-i", self._keyfile]
        args.append(self._target)
        proc = QProcess(self)
        proc.setProgram("ssh")
        proc.setArguments(args)
        proc.finished.connect(self._on_finished)
        proc.start()
        self._proc = proc
        self._attempts = 0
        self._probe.start()

    def _check_ready(self) -> None:
        self._attempts += 1
        s = socket.socket()
        s.settimeout(0.2)
        try:
            s.connect(("127.0.0.1", self.local_port))
            s.close()
            self._probe.stop()
            self.ready.emit(self.local_port)
            return
        except OSError:
            s.close()
        if self._attempts > 75:  # ~15s
            self._probe.stop()
            self.stop()
            self.failed.emit("ssh tunnel timed out")

    def _on_finished(self, _code, _status) -> None:
        if self._proc is None:
            return
        err = bytes(self._proc.readAllStandardError().data()).decode(
            "utf-8", "replace"
        ).strip()
        self._probe.stop()
        self._proc = None
        self.failed.emit(err.splitlines()[-1] if err else "ssh tunnel closed")

    def stop(self) -> None:
        self._probe.stop()
        if self._proc is not None:
            proc = self._proc
            self._proc = None
            proc.finished.disconnect(self._on_finished)
            proc.kill()
            proc.waitForFinished(1000)
