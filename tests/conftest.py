"""Fixtures.

libvirt ships a fake hypervisor at test:///default with one domain, one pool
and one network, so pointing the service layer at it runs the real code paths
against real libvirt semantics without touching a real machine.

Its state is shared by every connection open in this process, and resets only
once the last one closes. Two consequences:

- a define in one call is invisible to the next if the connection closed in
  between, hence `testconn` pinning one with close() stubbed out;
- a test that defines a domain has to undefine it, or it turns up in tests that
  expect only the driver's own - intermittently, depending on whether anything
  else still holds a connection open.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# before anything imports Qt, so the suite runs over SSH and in CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import libvirt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vmmanager.core.connection import DEFAULT_URI, set_uri  # noqa: E402

TEST_URI = "test:///default"


@pytest.fixture
def testconn(monkeypatch):
    """The service layer, pointed at libvirt's fake hypervisor."""
    conn = libvirt.open(TEST_URI)
    monkeypatch.setattr(conn, "close", lambda *a: None, raising=False)
    monkeypatch.setattr(libvirt, "open", lambda *a, **k: conn)
    set_uri(TEST_URI)

    # Hand every test the state the driver starts in. Several tests shut the
    # domain down on purpose and leave it that way; the suite used to get away
    # with that because the driver resets when the last connection closes, which
    # made it depend on when connections happened to close. It does not now.
    only = conn.lookupByName("test")
    if not only.isActive():
        only.create()

    yield conn
    set_uri(DEFAULT_URI)
    monkeypatch.undo()
    try:
        libvirt.virConnect.close(conn)
    except libvirt.libvirtError:
        pass


@pytest.fixture
def domain(testconn):
    """The fake driver's one domain. It's running."""
    return testconn.lookupByName("test")


@pytest.fixture(autouse=True)
def _scratch_database(tmp_path, monkeypatch):
    """Keep the suite out of the real stats database.

    MainWindow opens StatsStore() with no argument, so anything constructing one
    wrote to ~/.local/share/vmmanager/stats.db - the user's own history, and in
    CI a path that may not be writable at all.
    """
    from vmmanager.data import history

    monkeypatch.setattr(history, "DB_DIR", tmp_path / "share")
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "share" / "stats.db")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    import vmmanager.theme as theme

    theme.load_fonts()
    app.setStyleSheet(theme.QSS)
    return app


@pytest.fixture(autouse=True)
def _destroy_widgets_after_each_test():
    """Get rid of the widgets a test leaves behind.

    close() hides a window; it does not destroy it. A closed dialog is still a
    live top-level widget, and Qt restyles every live widget when the stylesheet
    changes - so without this the suite accumulated 208 of them, 7000-odd widgets
    between them, and every theme test dragged the lot along with it.
    """
    yield
    from PySide6.QtCore import QEvent, QThreadPool
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return

    # Let any service call still running finish first. A task that outlives its
    # test calls libvirt.open() after the fixture has un-patched it, gets a real
    # connection, and closes it - and when the last connection to the fake driver
    # closes, its state resets, which lands on whichever test is running by then.
    QThreadPool.globalInstance().waitForDone(10_000)
    app.processEvents()

    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    # processEvents() deliberately skips deferred deletes, so ask for them
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture
def scratch_settings(tmp_path):
    """QSettings pointed somewhere disposable.

    What it writes to otherwise is the user's own configuration, which a test
    has no business editing. Qt settles on the config directory the first time
    anything asks for it, so the environment is too late by the time the suite
    is running: setPath is what still moves it.
    """
    from pathlib import Path

    from PySide6.QtCore import QSettings

    fmt = QSettings.Format.NativeFormat
    scope = QSettings.Scope.UserScope
    real = Path(QSettings("vmmanager", "vmmanager").fileName()).parent.parent
    QSettings.setPath(fmt, scope, str(tmp_path))
    where = QSettings("vmmanager", "vmmanager").fileName()
    assert where.startswith(str(tmp_path)), f"settings still going to {where}"
    yield tmp_path
    QSettings.setPath(fmt, scope, str(real))
