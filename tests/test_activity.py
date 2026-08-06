"""What the app did, recorded where you can go and look at it.

The events table holds what libvirt reported. This holds what was asked
for, by whom (this app), and what came back - so a machine that stopped on
its own and one that was stopped from here are told apart afterwards, and a
scheduled snapshot that failed at 04:00 leaves a line rather than nothing.
"""

from __future__ import annotations

import pytest

from vmmanager.core.activity import is_write, records
from vmmanager.data.history import query_activity


def test_writes_are_recorded_and_reads_are_not():
    assert is_write("svc_set_boot_menu")
    assert is_write("svc_delete")
    assert is_write("svc_domain_action")
    assert is_write("svc_start")
    assert not is_write("svc_get_hardware")
    assert not is_write("svc_list_pools")
    assert not is_write("svc_host_topology")


def test_every_service_function_is_deliberately_classified():
    """A new svc_ function that matches no prefix is treated as a read and
    silently never recorded. This is what makes that a decision rather than
    an accident: name it in EXTRA_WRITES or in READS.

    The known reads are listed here rather than derived, so adding a write
    that happens to look like one of them fails here first.
    """
    import vmmanager.libvirt_service as facade
    from vmmanager.core.activity import EXTRA_WRITES, READS, WRITE_PREFIXES

    known_reads = {
        "svc_backing_chain", "svc_backing_index", "svc_definition_diff",
        "svc_display_health", "svc_feature_support", "svc_fetch_file",
        "svc_get_device_xml", "svc_get_features", "svc_get_hardware",
        "svc_get_network_def", "svc_get_network_spec", "svc_get_nwfilter_xml",
        "svc_get_on_crash", "svc_get_tuning", "svc_get_xml",
        "svc_graphics_info", "svc_guest_fs_health", "svc_guest_info",
        "svc_hook_state", "svc_host_topology", "svc_inspect",
        "svc_iommu_report", "svc_list_checkpoints", "svc_list_domain_disks",
        "svc_list_evdev", "svc_list_host_devices", "svc_list_mdevs",
        "svc_list_modes", "svc_list_network_names", "svc_list_networks",
        "svc_list_nwfilters", "svc_list_pools", "svc_list_snapshots",
        "svc_machine_types", "svc_marker_state", "svc_mdev_types",
        "svc_mode_diff", "svc_nwfilter_names", "svc_orphan_volumes",
        "svc_probe_uri", "svc_qemu_cmdline", "svc_screenshot",
        "svc_sriov_pfs", "svc_start_problems", "svc_tpm_available",
        "svc_check_image", "svc_image_info", "svc_compare_machines",
        "svc_compare_definitions", "svc_capture_profile",
        "svc_usb_watch_state", "svc_windows_tooling_state", "svc_set_uri",
    }
    every = {n for n in dir(facade) if n.startswith("svc_")}
    unclassified = sorted(
        n for n in every
        if not n.startswith(WRITE_PREFIXES)
        and n not in EXTRA_WRITES
        and n not in READS
        and n not in known_reads
    )
    assert unclassified == [], (
        "decide whether these change anything - EXTRA_WRITES if they do, "
        "the read list if they do not:\n" + "\n".join(unclassified)
    )
    assert not (EXTRA_WRITES & known_reads), "a name cannot be both"


def test_the_operations_people_ask_about_are_recorded():
    """The ones worth having in a log after something went wrong."""
    for name in ("svc_delete", "svc_clone", "svc_domain_action",
                 "svc_change_media", "svc_guest_exec", "svc_pool_action",
                 "svc_switch_mode", "svc_teardown_stack"):
        assert is_write(name), name


def test_a_preference_is_not_a_change_to_a_machine():
    """svc_set_uri matches the prefix and changes nothing about any guest."""
    assert not is_write("svc_set_uri")
    assert not is_write("svc_start_problems")


def test_every_service_write_is_wrapped_exactly_once():
    from vmmanager.core import RECORDED
    import vmmanager.libvirt_service as facade

    assert len(RECORDED) > 50, "the wrapping did not run"
    for name in RECORDED:
        fn = getattr(facade, name)
        assert getattr(fn, "__wrapped_for_activity__", False), name
        assert fn.__name__ == name, "the name has to survive for the log"


def test_a_successful_call_lands_in_the_log(_scratch_database):
    @records
    def svc_set_thing(uuid, value):
        return "Applied to the config."

    svc_set_thing("601784c9-cceb-43dc-92b7-9060387eacb9", "loud")

    rows = query_activity()
    assert len(rows) == 1
    _ts, uuid, action, detail, ok = rows[0]
    assert action == "svc_set_thing"
    assert uuid == "601784c9-cceb-43dc-92b7-9060387eacb9"
    assert "loud" in detail and "Applied" in detail
    assert ok == 1


def test_a_failure_is_recorded_with_its_reason_and_still_raises(_scratch_database):
    @records
    def svc_delete_thing(uuid):
        raise RuntimeError("volume is in use")

    with pytest.raises(RuntimeError, match="in use"):
        svc_delete_thing("601784c9-cceb-43dc-92b7-9060387eacb9")

    rows = query_activity()
    assert len(rows) == 1
    assert rows[0][2] == "svc_delete_thing"
    assert "volume is in use" in rows[0][3]
    assert rows[0][4] == 0, "recorded as a failure"


def test_failures_can_be_asked_for_on_their_own(_scratch_database):
    @records
    def svc_set_ok(uuid):
        return "fine"

    @records
    def svc_set_bad(uuid):
        raise RuntimeError("no")

    svc_set_ok("u")
    with pytest.raises(RuntimeError):
        svc_set_bad("u")

    assert len(query_activity()) == 2
    only_bad = query_activity(failures_only=True)
    assert [r[2] for r in only_bad] == ["svc_set_bad"]


def test_it_can_be_narrowed_to_one_machine(_scratch_database):
    @records
    def svc_set_thing(uuid):
        return "done"

    a = "601784c9-cceb-43dc-92b7-9060387eacb9"
    b = "11111111-2222-3333-4444-555555555555"
    svc_set_thing(a)
    svc_set_thing(b)
    svc_set_thing(a)

    assert len(query_activity(uuid=a)) == 2
    assert len(query_activity(uuid=b)) == 1


def test_the_uuid_is_found_wherever_it_sits(_scratch_database):
    """svc_move_disk takes it second, svc_set_nic_filter first. Matching on
    shape rather than position means neither has to be special-cased."""
    @records
    def svc_move_disk(dev, uuid):
        return "moved"

    svc_move_disk("vda", "601784c9-cceb-43dc-92b7-9060387eacb9")
    assert query_activity()[0][1] == "601784c9-cceb-43dc-92b7-9060387eacb9"


def test_a_broken_log_does_not_break_the_operation(_scratch_database, monkeypatch):
    """Recording is a side effect of doing the thing. If the database is
    unreadable, the thing still has to happen."""
    import vmmanager.data.history as history

    def explode(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(history, "record_activity", explode)

    @records
    def svc_set_thing(uuid):
        return "still applied"

    assert svc_set_thing("u") == "still applied"


def test_a_long_argument_is_shortened_rather_than_stored_whole(_scratch_database):
    @records
    def svc_set_device_xml(uuid, text):
        return "ok"

    svc_set_device_xml("u", "<disk>" + "x" * 5000 + "</disk>")
    detail = query_activity()[0][3]
    assert len(detail) <= 400
    assert "…" in detail


def test_a_real_service_write_reaches_the_log(testconn, domain, _scratch_database):
    from vmmanager.libvirt_service import svc_domain_action

    svc_domain_action(domain.UUIDString(), "autostart-on")

    rows = query_activity()
    assert [r[2] for r in rows] == ["svc_domain_action"]
    assert rows[0][1] == domain.UUIDString()
    assert "autostart-on" in rows[0][3]
