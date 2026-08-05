"""Small pure pieces with outsized consequences.

URI parsing decides whether a console tunnels over SSH at all. The stats store
holds config history and schedules. The flow layout decides whether a row of
buttons stays reachable. None had any tests.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------- ssh targets


@pytest.mark.parametrize("uri,expected", [
    ("qemu+ssh://user@host/system", ("user@host", None)),
    ("qemu+ssh://host/system", ("host", None)),
    ("qemu+ssh://user@host/system?keyfile=/home/u/.ssh/id_ed25519",
     ("user@host", "/home/u/.ssh/id_ed25519")),
    ("xen+ssh://root@hv1/", ("root@hv1", None)),
    # not over ssh, so nothing to tunnel
    ("qemu:///system", None),
    ("qemu+tcp://host/system", None),
    ("qemu+tls://host/system", None),
    ("", None),
])
def test_ssh_target_parsing(uri, expected):
    from vmmanager.console.tunnel import ssh_target_of

    assert ssh_target_of(uri) == expected


@pytest.mark.parametrize("uri,remote", [
    ("qemu:///system", False),
    ("qemu:///session", False),
    ("qemu+ssh://host/system", True),
    ("qemu+tcp://host/system", True),
    ("test:///default", False),
])
def test_remote_detection(uri, remote):
    from vmmanager.console.tunnel import is_remote_uri

    assert is_remote_uri(uri) is remote


# ---------------------------------------------------------------- palette


def test_palette_matches_a_subsequence_not_just_a_prefix():
    from vmmanager.palette import _score

    assert _score("web", "Start web-01") is not None
    assert _score("stw", "Start web-01") is not None  # letters in order


def test_palette_rejects_letters_out_of_order():
    from vmmanager.palette import _score

    assert _score("bew", "web") is None


def test_palette_is_case_insensitive():
    from vmmanager.palette import _score

    assert _score("WEB", "start web-01") is not None


def test_palette_prefers_the_closer_match():
    """A lower score sorts first, so an exact prefix must beat a scattered one."""
    from vmmanager.palette import _score

    tight = _score("web", "web-01")
    loose = _score("web", "would everyone begin")
    assert tight is not None and loose is not None
    assert tight < loose


def test_an_empty_query_matches_everything():
    from vmmanager.palette import _score

    assert _score("", "anything") is not None


# ---------------------------------------------------------------- stats store


@pytest.fixture
def store(tmp_path):
    from vmmanager.data.history import StatsStore

    s = StatsStore(tmp_path / "stats.db")
    yield s
    s.close()


def test_config_history_keeps_one_row_per_distinct_definition(store):
    """Relaunching the app used to add an identical row every time."""
    store.record_xml("uuid-1", "<domain>one</domain>")
    store.record_xml("uuid-1", "<domain>one</domain>")
    store.record_xml("uuid-1", "<domain>one</domain>")
    assert store.latest_xml("uuid-1") == "<domain>one</domain>"

    store.record_xml("uuid-1", "<domain>two</domain>")
    assert store.latest_xml("uuid-1") == "<domain>two</domain>"
    rows = store._db.execute(
        "SELECT xml FROM xml_history WHERE uuid = ?", ("uuid-1",)
    ).fetchall()
    assert len(rows) == 2, f"expected two versions, got {len(rows)}"


def test_config_history_is_per_machine(store):
    store.record_xml("uuid-1", "<domain>one</domain>")
    store.record_xml("uuid-2", "<domain>two</domain>")
    assert store.latest_xml("uuid-1") == "<domain>one</domain>"
    assert store.latest_xml("uuid-2") == "<domain>two</domain>"


def test_no_history_for_an_unknown_machine(store):
    assert store.latest_xml("never-seen") is None


def test_a_schedule_round_trips(store):
    store.set_schedule("uuid-1", 24, 7, True)
    assert store.schedule_for("uuid-1") == (24, 7, True)
    store.clear_schedule("uuid-1")
    assert store.schedule_for("uuid-1") is None


def test_a_stack_round_trips(store):
    store.save_stack("lab", "debian-base", 3, "new-isolated")
    assert store.stacks() == [("lab", "debian-base", 3, "new-isolated")]
    store.delete_stack("lab")
    assert store.stacks() == []


def test_using_a_closed_store_does_not_raise(store):
    """Shutdown order used to let a poll tick reach a closed database."""
    store.close()
    store.record_xml("uuid-1", "<domain/>")   # no-ops rather than crashing
    assert store.latest_xml("uuid-1") is None
    assert store.stacks() == []
    assert store.schedules() == []
    assert store.schedule_for("uuid-1") is None
    store.record_event("uuid-1", "state", "off -> on")


# ---------------------------------------------------------------- flow layout


def test_flow_layout_minimum_is_one_item_not_the_whole_row(qapp):
    """The point of it: a row of buttons must not set the window's width."""
    from PySide6.QtWidgets import QPushButton, QWidget

    from vmmanager.widgets import flow_row

    host = QWidget()
    buttons = [QPushButton("A fairly wide button label") for _ in range(6)]
    layout = flow_row(buttons)
    host.setLayout(layout)
    widest = max(b.sizeHint().width() for b in buttons)
    all_of_them = sum(b.sizeHint().width() for b in buttons)
    assert layout.minimumSize().width() <= widest + 8
    assert layout.minimumSize().width() < all_of_them / 2


def test_flow_layout_reports_more_height_when_narrower(qapp):
    from PySide6.QtWidgets import QPushButton, QWidget

    from vmmanager.widgets import flow_row

    host = QWidget()
    buttons = [QPushButton(f"button {i}") for i in range(6)]
    layout = flow_row(buttons)
    host.setLayout(layout)
    one_line = layout.heightForWidth(4000)
    cramped = layout.heightForWidth(150)
    assert cramped > one_line, "narrow rows have to wrap onto more lines"


def test_flow_layout_places_items_without_overlapping(qapp):
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QPushButton, QWidget

    from vmmanager.widgets import flow_row

    host = QWidget()
    buttons = [QPushButton(f"button {i}") for i in range(6)]
    layout = flow_row(buttons)
    host.setLayout(layout)
    host.setGeometry(QRect(0, 0, 200, 400))
    host.show()
    qapp.processEvents()
    rects = [b.geometry() for b in buttons]
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not a.intersects(b), f"{a} overlaps {b}"


def test_a_task_result_for_a_closed_widget_is_dropped(qapp):
    """A page closed while its task was running should not look like a crash.

    The callbacks are lambdas closed over a widget, so Qt cannot disconnect them
    when it destroys that widget: the reply arrives and shiboken raises.
    """
    from PySide6.QtWidgets import QLabel

    from vmmanager.tasks import _to_whoever_is_left

    doomed = QLabel("here")
    deliver = _to_whoever_is_left(lambda value: doomed.setText(str(value)))
    deliver("still alive")
    assert doomed.text() == "still alive"

    doomed.deleteLater()
    from PySide6.QtCore import QEvent

    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    deliver("nobody home")  # would raise RuntimeError without the guard


def test_a_real_error_in_a_task_callback_still_surfaces(qapp):
    """Only the destroyed-widget case is swallowed, not every RuntimeError."""
    import pytest

    from vmmanager.tasks import _to_whoever_is_left

    def boom(_value):
        raise RuntimeError("the disk is on fire")

    with pytest.raises(RuntimeError, match="on fire"):
        _to_whoever_is_left(boom)("anything")


def test_the_display_name_and_the_identifier_are_kept_apart():
    """VMManager is what people read; vmmanager is what the system uses.

    Mixing them up would move the settings file, break the desktop entry, or
    rename the command, so the split is worth a test.
    """
    from pathlib import Path

    from vmmanager import APP_NAME

    assert APP_NAME == "VMManager"

    root = Path(__file__).resolve().parent.parent
    main = (root / "vmmanager" / "__main__.py").read_text()
    assert 'setDesktopFileName("vmmanager")' in main, (
        "the desktop file id has to keep matching vmmanager.desktop"
    )
    assert 'prog="vmmanager"' in main, "the command is lowercase"

    desktop = (root / "vmmanager.desktop").read_text()
    assert "Name=VMManager" in desktop
    assert "Exec=vmmanager" in desktop

    # the settings are opened with an explicit pair, so setApplicationName
    # cannot move them
    settings = (root / "vmmanager" / "pages" / "settings.py").read_text()
    assert '_SETTINGS = ("vmmanager", "vmmanager")' in settings


def test_the_suite_does_not_touch_the_real_stats_database():
    """Tests wrote to ~/.local/share/vmmanager/stats.db until the fixture landed.

    StatsStore took its path as a default argument, evaluated at import, so
    redirecting DB_PATH had no effect on the no-argument StatsStore() that
    MainWindow opens.
    """
    from pathlib import Path

    from vmmanager.data import history

    assert history.DB_PATH != Path.home() / ".local/share/vmmanager/stats.db", (
        "the scratch-database fixture is not in effect"
    )
    store = history.StatsStore()
    try:
        assert Path(store._db.execute("PRAGMA database_list").fetchone()[2]) \
            == history.DB_PATH, "StatsStore() ignored the redirected DB_PATH"
    finally:
        store.close()
