"""The shared schedule decisions, and the daemon heartbeat handshake.

The app and `vmmanager --daemon` both run these; the heartbeat is what
keeps them from firing the same schedule twice.
"""

from __future__ import annotations

import time

from vmmanager.scheduler import (
    beat,
    external_scheduler_active,
    snapshot_tick,
    snapshots_due,
    wake_actions,
)


def test_snapshots_due_respects_interval_and_known_machines():
    now = 10_000.0
    schedules = [
        ("vm-a", 3600, 8, 1, int(now - 3700)),   # over the hour → due
        ("vm-b", 3600, 4, 0, int(now - 60)),     # ran a minute ago
        ("vm-gone", 3600, 8, 0, 0),              # not on this connection
    ]
    due = snapshots_due(schedules, {"vm-a", "vm-b"}, now)
    assert due == [("vm-a", 8, True)]


def test_wake_actions_fire_once_per_minute_key():
    schedules = [("vm-a", "08:00", "20:00", "all", "")]
    facts = {"vm-a": ("shutoff", False)}
    fired = wake_actions(schedules, facts, "08:00", "2026-08-05", True)
    assert fired == [("vm-a", "start", "2026-08-05 08:00 start")]
    # the stored key makes the same minute a no-op
    schedules = [("vm-a", "08:00", "20:00", "all", "2026-08-05 08:00 start")]
    assert wake_actions(schedules, facts, "08:00", "2026-08-05", True) == []


def test_wake_respects_day_filters_state_and_templates():
    facts = {"vm-a": ("shutoff", False)}
    weekdays = [("vm-a", "08:00", "", "weekdays", "")]
    assert wake_actions(weekdays, facts, "08:00", "2026-08-08", False) == []
    # a running machine is not started again
    assert wake_actions(
        [("vm-a", "08:00", "", "all", "")],
        {"vm-a": ("running", False)}, "08:00", "2026-08-05", True,
    ) == []
    # a template is never started
    assert wake_actions(
        [("vm-a", "08:00", "", "all", "")],
        {"vm-a": ("shutoff", True)}, "08:00", "2026-08-05", True,
    ) == []
    # stop fires only on a running machine
    assert wake_actions(
        [("vm-a", "", "20:00", "all", "")],
        {"vm-a": ("running", False)}, "20:00", "2026-08-05", True,
    ) == [("vm-a", "shutdown", "2026-08-05 20:00 stop")]


def test_heartbeat_freshness(tmp_path):
    hb = tmp_path / "heartbeat"
    assert not external_scheduler_active(hb)  # no file, no daemon
    beat(hb)
    assert external_scheduler_active(hb)
    # an old heartbeat means the daemon is gone and the app takes over
    assert not external_scheduler_active(hb, now=time.time() + 3600)


def test_snapshot_tick_takes_and_prunes(testconn, domain, tmp_path):
    """The daemon's pass end to end against the fake driver."""
    from vmmanager.data.history import StatsStore
    from vmmanager.libvirt_service import svc_list_snapshots

    uuid = domain.UUIDString()
    store = StatsStore(tmp_path / "stats.db")
    try:
        store.set_schedule(uuid, interval_s=1, keep=2, external=False)
        facts = {uuid: (domain.name(), "running", False)}
        first = snapshot_tick(store, facts)
        assert first and "snapshotted" in first[0]
        # immediately after, nothing is due
        assert snapshot_tick(store, facts) == []
        names = [s.name for s in svc_list_snapshots(uuid)]
        assert len([n for n in names if n.startswith("auto-")]) == 1
    finally:
        for s in domain.listAllSnapshots():
            s.delete(0)
        store.close()
