"""Auto-attach USB: the plan and the rule store."""

from __future__ import annotations

from vmmanager.core.hostdev import usb_auto_attach_plan


RULES = [("vm-a", "1234:5678"), ("vm-b", "aaaa:bbbb")]


def test_rule_fires_when_device_present_and_machine_running():
    plan = usb_auto_attach_plan(
        RULES, present={"1234:5678"}, running={"vm-a"}, attached={},
    )
    assert plan == [("vm-a", "1234:5678")]


def test_nothing_happens_for_absent_devices_or_stopped_machines():
    assert usb_auto_attach_plan(RULES, set(), {"vm-a", "vm-b"}, {}) == []
    assert usb_auto_attach_plan(
        RULES, {"1234:5678", "aaaa:bbbb"}, set(), {}
    ) == []


def test_a_device_already_inside_a_guest_is_left_alone():
    # even when it sits in a machine with no rule for it
    plan = usb_auto_attach_plan(
        RULES, present={"1234:5678"}, running={"vm-a"},
        attached={"vm-c": {"1234:5678"}},
    )
    assert plan == []
    # and one already in the right machine is not attached twice
    plan = usb_auto_attach_plan(
        RULES, present={"1234:5678"}, running={"vm-a"},
        attached={"vm-a": {"1234:5678"}},
    )
    assert plan == []


def test_two_rules_for_one_device_first_wins():
    rules = [("vm-a", "1234:5678"), ("vm-b", "1234:5678")]
    plan = usb_auto_attach_plan(
        rules, present={"1234:5678"}, running={"vm-a", "vm-b"}, attached={},
    )
    assert plan == [("vm-a", "1234:5678")]


def test_rules_round_trip_in_the_store(tmp_path):
    from vmmanager.data.history import StatsStore

    store = StatsStore(tmp_path / "stats.db")
    try:
        store.set_usb_rules("vm-a", ["1234:5678", "aaaa:bbbb"])
        store.set_usb_rules("vm-b", ["cccc:dddd"])
        assert sorted(store.usb_rules_for("vm-a")) == ["1234:5678", "aaaa:bbbb"]
        assert sorted(store.usb_rules()) == [
            ("vm-a", "1234:5678"), ("vm-a", "aaaa:bbbb"), ("vm-b", "cccc:dddd"),
        ]
        store.set_usb_rules("vm-a", [])
        assert store.usb_rules_for("vm-a") == []
        assert store.usb_rules() == [("vm-b", "cccc:dddd")]
    finally:
        store.close()
