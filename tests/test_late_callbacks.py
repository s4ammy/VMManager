"""Background work that finishes after the widgets it updates are gone.

PySide hands out a Python wrapper around a C++ widget, and the wrapper
outlives the widget. A thread that finishes late - the logo downloader,
here - then writes into something already deleted:

    RuntimeError: libshiboken: Internal C++ object (QLabel) already deleted.

which comes out of the Qt event loop, so it lands on whatever happens to
be running rather than on the thing that caused it.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent
from shiboken6 import isValid

from vmmanager.core.models import DomainSnapshot, HostSnapshot
from vmmanager.pages.machines import MachinesPage

HOST = HostSnapshot(hostname="h", hypervisor="QEMU", hypervisor_version="11.0.0",
                    cpus=8, memory_mb=16384, running=1, total=1)


def _snap(uuid="u1", name="web-01"):
    return DomainSnapshot(uuid=uuid, name=name, state="running", vcpus=2,
                          memory_mb=2048, autostart=False)


def test_refreshing_cards_after_they_are_deleted_does_not_raise(qapp):
    """What the logo downloader does when it lands after a window closed."""
    page = MachinesPage()
    page.update_from([_snap()], HOST)
    qapp.processEvents()
    card = page._cards["u1"]
    assert isValid(card)

    # Delete the C++ side while the page keeps its Python reference - what
    # closing the window that owns the cards does. deleteLater alone is not
    # enough here: processEvents does not deliver DeferredDelete, so the
    # object survives and the test proves nothing.
    card.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    assert not isValid(card), "the C++ widget was not actually deleted"

    page.refresh_cards()  # used to raise from inside the event loop
    qapp.processEvents()


def test_a_stopped_downloader_says_nothing(qapp):
    """Stopping it has to mean nothing is emitted, or the guard above is the
    only thing standing between a closed window and a crash."""
    from vmmanager.data.oslogos import LogoDownloader

    heard = []
    downloader = LogoDownloader(["definitely-not-a-real-logo-slug"])
    downloader.fetched.connect(heard.append)
    downloader.stop()
    downloader.start()
    downloader.wait(5000)
    qapp.processEvents()
    assert heard == []


def test_a_guarded_connection_drops_a_late_answer(qapp):
    """The general form of the fix: a worker's signal wrapped so a reply
    arriving after its widgets are gone is dropped, not raised. Qt does
    this itself for a bound method of a QObject, and cannot for a plain
    function - which is what these connections are."""
    from PySide6.QtCore import QObject, Signal

    from vmmanager.tasks import connect_guarded

    class _Worker(QObject):
        done = Signal(str)

    worker = _Worker()
    landed = []

    def touch_a_dead_widget(text):
        landed.append(text)
        raise RuntimeError(
            "libshiboken: Internal C++ object (QLabel) already deleted."
        )

    connect_guarded(worker.done, touch_a_dead_widget)
    worker.done.emit("late")          # must not raise
    assert landed == ["late"]


def test_a_real_error_still_surfaces():
    """Only the already-deleted case is swallowed. A genuine bug in a
    callback has to keep reaching the crash reporter, or this guard would
    hide every fault in every worker reply.

    Tested through the wrapper rather than a signal emission: pytest-qt
    intercepts exceptions raised inside slots, so an emit would prove
    nothing about who raised what.
    """
    import pytest

    from vmmanager.tasks import _to_whoever_is_left

    def genuinely_broken(_value):
        raise RuntimeError("something genuinely broken")

    with pytest.raises(RuntimeError, match="genuinely broken"):
        _to_whoever_is_left(genuinely_broken)("x")


def test_the_deleted_case_is_swallowed_by_the_same_wrapper():
    from vmmanager.tasks import _to_whoever_is_left

    def dead_widget(_value):
        raise RuntimeError(
            "libshiboken: Internal C++ object (QLabel) already deleted."
        )

    _to_whoever_is_left(dead_widget)("x")  # must not raise
