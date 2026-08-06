"""The Host page, the Activity page, and the pieces they are made of."""

from __future__ import annotations

from vmmanager.pages.activity import ActivityPage, ago, readable
from vmmanager.pages.host import HostPage


# ------------------------------------------------------------- activity

def test_a_service_name_is_turned_into_something_readable():
    assert readable("svc_set_boot_menu") == "set boot menu"
    assert readable("svc_add_rng") == "add rng"
    assert readable("svc_domain_action") == "machine action"
    assert readable("svc_delete") == "delete machine"


def test_how_long_ago_uses_the_roughest_unit_that_still_says_something():
    now = 1_000_000
    assert ago(now, now) == "just now"
    assert ago(now - 30, now) == "just now"
    assert ago(now - 300, now) == "5m ago"
    assert ago(now - 7200, now) == "2h ago"
    assert ago(now - 3 * 86400, now) == "3d ago"


def test_a_clock_that_went_backwards_does_not_read_as_the_future():
    assert ago(2_000, 1_000) == "just now"


def test_the_page_shows_what_the_log_holds(qapp):
    page = ActivityPage()
    try:
        page._arrived([
            (1_000, "u-1", "svc_set_boot_menu", "True → Applied", 1),
            (900, "u-2", "svc_delete", "→ volume in use", 0),
        ])
        assert page.table.rowCount() == 2
        assert page.table.item(0, 2).text() == "set boot menu"
        assert page.table.item(1, 2).text() == "delete machine"
        assert "1 failed" in page.status.text()
    finally:
        page.deleteLater()


def test_a_row_names_the_machine_rather_than_its_uuid(qapp):
    page = ActivityPage()
    try:
        snap = type("S", (), {"uuid": "u-1", "name": "Builder"})()
        page.set_machine_names([snap])
        page._arrived([(1_000, "u-1", "svc_set_boot_menu", "", 1)])
        assert page.table.item(0, 1).text() == "Builder"
    finally:
        page.deleteLater()


def test_filtering_narrows_without_re_reading(qapp):
    page = ActivityPage()
    try:
        page._arrived([
            (1_000, "u-1", "svc_add_rng", "", 1),
            (900, "u-2", "svc_add_tpm", "", 1),
        ])
        page.search.setText("tpm")
        assert page.table.rowCount() == 1
        assert "1 of 2 shown" in page.status.text()
        page.search.setText("")
        assert page.table.rowCount() == 2
    finally:
        page.deleteLater()


def test_an_empty_log_says_what_will_fill_it(qapp):
    page = ActivityPage()
    try:
        page._arrived([])
        assert "Nothing recorded yet" in page.status.text()
    finally:
        page.deleteLater()


# ----------------------------------------------------------------- host

def _host(**kwargs):
    from vmmanager.core.models import HostSnapshot, Usage

    base = dict(
        hostname="goober", hypervisor="QEMU", hypervisor_version="11.0.3",
        cpus=16, memory_mb=64000, running=1, total=2, cpu_pct=12.0,
        mem_used_mb=19000.0,
        history=tuple(Usage(cpu_pct=float(i), mem_mb=1000.0) for i in range(20)),
    )
    base.update(kwargs)
    return HostSnapshot(**base)


def _domain(name, uuid, state="running", cpu=10.0):
    from vmmanager.core.models import DomainSnapshot, Usage

    return DomainSnapshot(
        uuid=uuid, name=name, state=state, vcpus=4, memory_mb=2048,
        autostart=False,
        usage=Usage(cpu_pct=cpu, mem_mb=1024.0, disk_bps=1e6, net_bps=1e5),
    )


def test_it_says_what_the_host_is_made_of(qapp):
    from PySide6.QtWidgets import QLabel

    page = HostPage()
    try:
        page.update_from([_domain("a", "u-a")], _host())
        page.refresh()
        labels = []
        for i in range(page.chips.count()):
            chip = page.chips.itemAt(i).widget()
            if chip is not None:
                labels += [child.text() for child in chip.findChildren(QLabel)]
        assert "goober" in labels, "the node it is running on"
        assert "QEMU" in labels
        assert "16" in labels, "its cpus"
        assert "1 of 1 up" in labels


        assert page.chart_cpu.value.text() == "12%"
    finally:
        page.deleteLater()


def test_the_busiest_machine_is_the_top_row(qapp):
    page = HostPage()
    try:
        page.update_from(
            [_domain("quiet", "u-q", cpu=2.0), _domain("busy", "u-b", cpu=91.0)],
            _host(),
        )
        page.refresh()
        assert page.table.item(0, 0).text() == "busy"
        assert page.table.item(0, 1).text() == "91%"
    finally:
        page.deleteLater()


def test_machines_that_are_not_running_are_left_out_of_the_live_view(qapp):
    page = HostPage()
    try:
        page.update_from(
            [_domain("up", "u-1"), _domain("down", "u-2", state="shutoff")],
            _host(),
        )
        page.refresh()
        assert page.table.rowCount() == 1
        assert page.table.item(0, 0).text() == "up"
    finally:
        page.deleteLater()


def test_a_host_with_nothing_running_says_so(qapp):
    page = HostPage()
    try:
        page.update_from([_domain("down", "u-1", state="shutoff")], _host())
        page.refresh()
        assert page.table.rowCount() == 0
        assert "Nothing is running" in page.note.text()
    finally:
        page.deleteLater()


def test_it_draws_nothing_before_the_first_poll(qapp):
    page = HostPage()
    try:
        page.refresh()   # must not raise
        assert page.table.rowCount() == 0
    finally:
        page.deleteLater()


def test_both_pages_are_reachable_from_the_sidebar():
    from vmmanager.widgets.shell import Sidebar

    assert "Host" in Sidebar.NAV
    assert "Activity" in Sidebar.NAV


def test_the_chips_can_be_redrawn(qapp):
    """setParent(None) empties the layout item, so asking it for the widget
    a second time gets None - and the second poll tick crashed."""
    page = HostPage()
    try:
        for cpu in (10.0, 20.0, 30.0):
            page.update_from([_domain("a", "u-a", cpu=cpu)], _host())
            page.refresh()
        assert page.chips.count() == 6, "five chips and the stretch"
    finally:
        page.deleteLater()
