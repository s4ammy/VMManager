"""Run blocking service calls on the global thread pool, deliver via signals."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .logs import log


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, fn) -> None:
        super().__init__()
        # QThreadPool must not delete the C++ runnable out from under the
        # Python wrapper - that segfaults in shiboken when the GC runs.
        self.setAutoDelete(False)
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as e:  # noqa: BLE001 - surface any failure to the UI
            try:
                self.signals.failed.emit(str(e))
            except RuntimeError:
                pass  # app shut down mid-task
            return
        try:
            self.signals.done.emit(result)
        except RuntimeError:
            pass


_live: set[Task] = set()


def _to_whoever_is_left(callback):
    """Deliver the answer, unless whoever asked for it has gone.

    Callbacks here are usually lambdas closed over a widget, and Qt's habit of
    disconnecting a destroyed receiver only works when the receiver is the object
    connected - which a lambda is not. So a page or dialog closed while its task
    was still running gets its reply delivered into a widget that Qt has already
    destroyed, and shiboken raises. Nobody is waiting for the answer at that
    point; the only wrong move is to treat it as a crash and tell the user.
    """

    def deliver(value) -> None:
        try:
            callback(value)
        except RuntimeError as exc:
            if "already deleted" not in str(exc):
                raise
            log.debug("dropped a task result: %s", exc)

    return deliver


def run_task(fn, done=None, failed=None) -> None:
    task = Task(fn)
    _live.add(task)

    def _cleanup(*_args) -> None:
        _live.discard(task)

    task.signals.done.connect(_cleanup)
    task.signals.failed.connect(_cleanup)
    if done:
        task.signals.done.connect(_to_whoever_is_left(done))
    if failed:
        task.signals.failed.connect(_to_whoever_is_left(failed))
    QThreadPool.globalInstance().start(task)
