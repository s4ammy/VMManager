"""Machines popped out into windows of their own.

The detail page keeps all of its state per instance, so a second one in a window
works without it knowing. What needs checking is the bookkeeping around that: one
window per machine, each fed from the same poll tick, and every one torn down
when it closes so a console or a serial thread is not left running.

The machines here are real ones on libvirt's fake driver rather than made-up
snapshots. Opening a machine starts nine reads, and against a uuid libvirt has
never heard of all nine fail and put a modal error dialog on screen that nothing
in a test can dismiss.
"""

from __future__ import annotations

import pytest

from vmmanager.core.models import DomainSnapshot, HostSnapshot

HOST = HostSnapshot(hostname="h", hypervisor="QEMU", hypervisor_version="11.0.0",
                    cpus=16, memory_mb=65536, running=0, total=2)

SECOND = (
    '<domain type="test"><name>db-01</name>'
    '<memory unit="MiB">1024</memory><vcpu>2</vcpu>'
    '<os><type arch="i686">hvm</type></os></domain>'
)


def snap(name: str, uuid: str, **kwargs) -> DomainSnapshot:
    base = dict(uuid=uuid, name=name, state="shutoff", vcpus=2,
                memory_mb=2048, autostart=False)
    base.update(kwargs)
    return DomainSnapshot(**base)


@pytest.fixture
def machines(testconn) -> dict[str, DomainSnapshot]:
    """Two machines by name. The fake driver ships with one, so define another.

    Undefined again afterwards. The fake driver's state is shared by every
    connection open in this process and only resets once the last one closes, so
    a domain left behind here turns up in tests that expect exactly one - and
    intermittently, depending on whether something else still holds a connection.
    """
    import libvirt

    try:
        extra = testconn.defineXML(SECOND)
    except libvirt.libvirtError:
        extra = testconn.lookupByName("db-01")  # a previous run left it behind
    try:
        yield {
            domain.name(): snap(domain.name(), domain.UUIDString())
            for domain in testconn.listAllDomains()
        }
    finally:
        try:
            extra.undefine()
        except libvirt.libvirtError:
            pass


@pytest.fixture
def window_of(qapp, machines):
    """MachineWindow instances, closed afterwards whatever the test did."""
    from vmmanager.pages.detail.window import MachineWindow

    made = []

    def build(name: str = "test"):
        window = MachineWindow(machines[name], HOST)
        made.append(window)
        qapp.processEvents()
        return window

    yield build
    for window in made:
        window.close()


@pytest.fixture
def main(qapp, testconn, machines):
    """A main window holding both machines, with no poll worker running."""
    from vmmanager.main_window import MainWindow

    window = MainWindow()
    window.worker.stop()  # the tests drive the updates themselves
    window._domains = list(machines.values())
    window._host = HOST
    qapp.processEvents()
    yield window
    for popped in list(window._windows.values()):
        popped.close()
    window.detail.shutdown()


# -- the window on its own


def test_a_window_shows_the_machine_it_was_given(window_of, machines):
    window = window_of("test")
    assert window.uuid == machines["test"].uuid
    assert window.page.uuid == machines["test"].uuid
    assert "test" in window.windowTitle()
    assert "VMManager" in window.windowTitle()


def test_a_window_drops_the_framing_that_belongs_to_the_main_window(window_of):
    """No list to go back to, and nothing to pop out of a window."""
    window = window_of("test")
    assert not window.page._back_btn.isVisible()
    assert not window.page._pop_btn.isVisible()


def test_a_renamed_machine_retitles_its_window(window_of, machines, qapp):
    window = window_of("test")
    window.update_from(snap("renamed", machines["test"].uuid))
    qapp.processEvents()
    assert "renamed" in window.windowTitle()


def test_closing_a_window_tears_the_page_down(window_of, machines, qapp):
    """Otherwise a console or serial thread outlives the window it fed."""
    window = window_of("test")
    events = []
    window.closed.connect(events.append)
    window.page.shutdown = lambda: events.append("shutdown")
    window.close()
    qapp.processEvents()
    assert "shutdown" in events
    assert machines["test"].uuid in events


# -- the bookkeeping in the main window


def test_popping_out_opens_a_window_and_returns_to_the_list(main, machines, qapp):
    uuid = machines["test"].uuid
    main._open_detail(uuid)
    qapp.processEvents()
    assert main.stack.currentWidget() is main.detail

    main._pop_out(uuid)
    qapp.processEvents()
    assert set(main._windows) == {uuid}
    assert main.stack.currentWidget() is main.machines, (
        "the machine moved to its own window, so the main one should not keep a "
        "second copy of it on screen"
    )


def test_two_machines_can_be_open_at_once(main, machines, qapp):
    for machine in machines.values():
        main._pop_out(machine.uuid)
    qapp.processEvents()

    assert set(main._windows) == {m.uuid for m in machines.values()}
    pages = [window.page for window in main._windows.values()]
    assert pages[0] is not pages[1]
    assert pages[0].uuid != pages[1].uuid


def test_the_same_machine_does_not_get_two_windows(main, machines, qapp):
    """Two consoles onto one guest would fight over a single-connection VNC."""
    uuid = machines["test"].uuid
    main._pop_out(uuid)
    was = main._windows[uuid]
    main._pop_out(uuid)
    qapp.processEvents()
    assert set(main._windows) == {uuid}
    assert main._windows[uuid] is was


def test_opening_a_popped_machine_from_the_list_raises_its_window(main, machines,
                                                                 qapp):
    uuid = machines["test"].uuid
    main._pop_out(uuid)
    main._navigate("Machines")
    qapp.processEvents()

    main._open_detail(uuid)
    qapp.processEvents()
    assert main.stack.currentWidget() is main.machines, (
        "it is already in a window; showing it inline as well would give the "
        "machine two consoles"
    )


def test_every_window_is_fed_from_the_same_tick(main, machines, qapp):
    for machine in machines.values():
        main._pop_out(machine.uuid)
    qapp.processEvents()

    a, b = machines["test"], machines["db-01"]
    main._update_windows(
        [snap(a.name, a.uuid, state="running"),
         snap(b.name, b.uuid, state="paused")], HOST,
    )
    qapp.processEvents()
    assert main._windows[a.uuid].page._snap.state == "running"
    assert main._windows[b.uuid].page._snap.state == "paused"
    assert main._windows[a.uuid].page.host is HOST


def test_a_window_closes_when_its_machine_is_gone(main, machines, qapp):
    for machine in machines.values():
        main._pop_out(machine.uuid)
    qapp.processEvents()

    kept = machines["db-01"]
    main._update_windows([kept], HOST)  # the other was deleted elsewhere
    qapp.processEvents()
    assert set(main._windows) == {kept.uuid}


def test_closing_a_window_removes_it_from_the_register(main, machines, qapp):
    uuid = machines["test"].uuid
    main._pop_out(uuid)
    qapp.processEvents()
    main._windows[uuid].close()
    qapp.processEvents()
    assert main._windows == {}


def test_modes_reach_a_popped_out_window(main, machines, qapp):
    """The mode button belongs to whichever page is showing the machine."""
    from vmmanager.core.modes import Mode

    uuid = machines["test"].uuid
    main._pop_out(uuid)
    qapp.processEvents()

    pages = main._detail_pages(uuid)
    assert main._windows[uuid].page in pages, (
        "a popped-out page should be among the pages showing this machine"
    )
    for page in pages:
        page.set_modes([Mode(name="debug", note="", marker="", created=0,
                             active=True)])
    assert main._windows[uuid].page._mode_btn.isVisible()


def test_quitting_closes_the_windows(main, machines, qapp):
    """A left-over window would keep the application alive after it was quit."""
    for machine in machines.values():
        main._pop_out(machine.uuid)
    qapp.processEvents()

    main._really_quit = True
    main.close()
    qapp.processEvents()
    assert main._windows == {}


def test_one_error_dialog_at_a_time(window_of, qapp):
    """Nine failed reads used to stack nine modal dialogs, each inside the last.

    exec() runs a nested event loop, so the dialog for the first failure
    delivered the second failure, which opened a dialog on top of it, and so on
    down. One unreadable machine buried the window.
    """
    page = window_of("test").page
    opened = []

    class FakeDialog:
        def __init__(self, *args):
            opened.append(args[-1])

        def exec(self):
            # what the real dialog does: pump events, which is where the next
            # failure used to arrive
            page._show_error("second failure")
            page._show_error("third failure")

    import vmmanager.pages.detail.page as page_module

    real = page_module.ErrorDialog
    page_module.ErrorDialog = FakeDialog
    try:
        page._show_error("first failure")
    finally:
        page_module.ErrorDialog = real

    assert opened == ["first failure"], (
        f"only the first failure should reach a dialog, got {opened}"
    )
    assert not page._error_open, "the flag has to clear, or errors go quiet forever"
