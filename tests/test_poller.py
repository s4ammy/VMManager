"""The poll worker: right answers, no repeated work.

It reads what a machine is once and keeps it until libvirt says otherwise, so
there are two things to check: the cache holds, and an event breaks it.
"""

from __future__ import annotations

from collections import Counter

import libvirt
import pytest

from vmmanager.core.poller import PollWorker


@pytest.fixture
def worker():
    return PollWorker()


@pytest.fixture
def counted(monkeypatch):
    """Count libvirt round trips, so caching is asserted rather than assumed.

    XMLDesc is counted twice: by name, and split by flags. The worker reads the
    live description (flags 0) for a machine's devices, and the persistent one
    (XML_INACTIVE) on a slow sweep for config history. Only the first is
    cached, so counting them together can't tell pass from fail.
    """
    calls: Counter[str] = Counter()

    def wrap(cls, name):
        real = getattr(cls, name)

        def counting(self, *args, **kwargs):
            calls[name] += 1
            if name == "XMLDesc":
                flags = args[0] if args else kwargs.get("flags", 0)
                calls["XMLDesc:live" if not flags else "XMLDesc:persistent"] += 1
            return real(self, *args, **kwargs)

        monkeypatch.setattr(cls, name, counting)

    for name in ("XMLDesc", "state", "info", "autostart", "hasManagedSaveImage"):
        wrap(libvirt.virDomain, name)
    for name in ("getHostname", "getInfo", "getVersion"):
        wrap(libvirt.virConnect, name)
    return calls


def test_snapshot_describes_the_domain(testconn, worker):
    domains, host = worker._collect(testconn)
    assert [d.name for d in domains] == ["test"]
    snap = domains[0]
    assert snap.state == "running"
    assert snap.vcpus >= 1
    assert snap.memory_mb > 0
    assert host.total == 1
    assert host.running == 1
    assert host.hostname


def test_description_is_read_once_then_cached(testconn, worker, counted):
    worker._collect(testconn)
    assert counted["XMLDesc:live"] >= 1, "the first tick has to read the domain"

    counted.clear()
    for _ in range(5):
        worker._collect(testconn)
    assert counted["XMLDesc:live"] == 0, "later ticks must not re-read the domain"
    assert counted["autostart"] == 0
    assert counted["hasManagedSaveImage"] == 0


def test_host_facts_are_read_once(testconn, worker, counted):
    worker._collect(testconn)
    counted.clear()
    for _ in range(4):
        worker._collect(testconn)
    assert counted["getHostname"] == 0
    assert counted["getInfo"] == 0


def test_an_event_makes_the_worker_re_read_that_machine(testconn, worker, counted):
    """A stale UUID from the event thread invalidates the cache."""
    worker._collect(testconn)
    counted.clear()
    worker._collect(testconn)
    assert counted["XMLDesc"] == 0

    uuid = testconn.lookupByName("test").UUIDString()
    worker._watch.stale.add(uuid)          # what a lifecycle callback does
    counted.clear()
    worker._collect(testconn)
    assert counted["XMLDesc:live"] >= 1, "the event should have forced a re-read"


def test_poke_invalidates_everything(testconn, worker, counted):
    worker._collect(testconn)
    worker.poke()
    counted.clear()
    worker._collect(testconn)
    assert counted["XMLDesc:live"] >= 1
    assert counted["autostart"] == 1


def test_the_config_sweep_slows_down_when_events_are_available(testconn, worker, counted):
    """It only covers hosts that report nothing, so it should be rare."""
    worker._collect(testconn)          # sweeps on the first tick either way
    counted.clear()
    for _ in range(12):
        worker._collect(testconn)
    polling_sweeps = counted["XMLDesc:persistent"]
    assert polling_sweeps >= 2, "without events it must keep checking"

    events = PollWorker()
    events._watch._ids.append((testconn, 0))    # pretend the host subscribed us
    assert events.event_driven
    events._collect(testconn)
    counted.clear()
    for _ in range(12):
        events._collect(testconn)
    assert counted["XMLDesc:persistent"] < polling_sweeps


def test_tag_change_shows_up_after_its_event(testconn, worker):
    """The point of invalidation: new metadata reaches the snapshot."""
    from vmmanager.core.domains import _write_vmm_meta

    domain = testconn.lookupByName("test")
    assert worker._collect(testconn)[0][0].tags == ()

    _write_vmm_meta(domain, is_template=False, tags=("prod",))
    assert worker._collect(testconn)[0][0].tags == (), "cached, as designed"

    worker._watch.stale.add(domain.UUIDString())
    assert worker._collect(testconn)[0][0].tags == ("prod",)


def test_state_comes_from_the_batched_stats_call(testconn, worker, counted):
    """The per-machine state()/info() calls are gone. Keep them gone."""
    worker._collect(testconn)
    counted.clear()
    worker._collect(testconn)
    assert counted["state"] == 0


def test_falls_back_when_a_driver_lacks_batched_stats(testconn, worker, monkeypatch):
    """Not every driver implements getAllDomainStats."""
    monkeypatch.setattr(
        libvirt.virConnect, "getAllDomainStats",
        lambda self, *a, **k: (_ for _ in ()).throw(
            libvirt.libvirtError("unsupported")
        ),
    )
    domains, _host = worker._collect(testconn)
    assert [d.name for d in domains] == ["test"]
    assert domains[0].vcpus >= 1


def test_config_changes_are_reported_for_the_history_feature(testconn, worker):
    seen: list[tuple[str, str]] = []
    worker.xml_changed.connect(lambda uuid, xml: seen.append((uuid, xml)))
    worker._collect(testconn)
    assert seen, "the first sweep should report the current definition"


def test_stopping_a_worker_that_never_ran_is_safe(worker):
    worker.stop()          # no connection, no pump thread started


# -- what the memory graph is actually showing


def test_guest_memory_prefers_what_the_guest_itself_reports():
    """available - usable is the guest's own view, the number its task
    manager agrees with."""
    from vmmanager.core.poller import guest_memory_kb

    raw = {"balloon.available": 16688620, "balloon.usable": 13674734,
           "balloon.current": 16777216, "balloon.rss": 16859348}
    assert guest_memory_kb(raw) == (16688620 - 13674734, True)


def test_the_balloon_size_is_never_reported_as_usage():
    """`current` is what the guest was *given*, not what it is using, and
    for a machine that has never been ballooned it is simply its maximum.
    Using it meant a machine read max-of-max from the moment it started
    until the driver inside it came up - the whole of the boot."""
    from vmmanager.core.poller import guest_memory_kb

    kb, from_guest = guest_memory_kb({"balloon.current": 16777216,
                                      "balloon.rss": 2_100_000})
    assert kb == 2_100_000, "the host's real footprint, not the maximum"
    assert not from_guest


def test_the_host_footprint_stands_in_until_the_guest_can_speak():
    """rss is the qemu process's resident size: not the guest's own view,
    and it includes emulator overhead - but it is a measurement, and during
    a boot it tracks what the guest has actually touched."""
    from vmmanager.core.poller import guest_memory_kb

    assert guest_memory_kb({"balloon.rss": 16859348}) == (16859348, False)


def test_a_machine_with_no_figures_at_all_reports_nothing(qapp):
    from vmmanager.core.poller import guest_memory_kb

    assert guest_memory_kb({}) == (0.0, False)


def test_nonsense_guest_numbers_are_not_used():
    """usable above available would give a negative reading."""
    from vmmanager.core.poller import guest_memory_kb

    raw = {"balloon.available": 100, "balloon.usable": 500,
           "balloon.current": 4096, "balloon.rss": 900}
    kb, from_guest = guest_memory_kb(raw)
    assert kb == 900 and not from_guest


def test_a_boot_climbs_rather_than_starting_at_the_maximum(qapp):
    """The reported symptom, as a sequence: a machine with 16 GB starts,
    and until its balloon driver is up the graph used to read 16 of 16."""
    from vmmanager.core.poller import PollWorker

    worker = PollWorker()
    maximum_kb = 16 * 1024 ** 2
    booting = [
        {"balloon.current": maximum_kb, "balloon.rss": 400_000},
        {"balloon.current": maximum_kb, "balloon.rss": 1_200_000},
        {"balloon.current": maximum_kb, "balloon.rss": 2_500_000},
    ]
    readings = [
        worker._domain_usage("u", raw, float(i), vcpus=4).mem_mb
        for i, raw in enumerate(booting)
    ]
    assert readings == sorted(readings), "it should climb as the guest boots"
    assert all(mb < maximum_kb / 1024 for mb in readings), "never pinned at max"

    # ...and hands over to the guest's own figure once the driver reports.
    settled = worker._domain_usage(
        "u",
        {"balloon.current": maximum_kb, "balloon.rss": 9_000_000,
         "balloon.available": maximum_kb, "balloon.usable": 13_000_000},
        10.0, vcpus=4,
    )
    assert settled.mem_from_guest
    assert settled.mem_mb == (maximum_kb - 13_000_000) / 1024


def test_a_machine_is_only_asked_once_to_report_its_memory(testconn):
    """A guest with no balloon driver never answers, and asking it on every
    tick would be a wasted round trip per machine per second."""
    from vmmanager.core.poller import PollWorker

    class _Dom:
        def __init__(self): self.asked = 0
        def setMemoryStatsPeriod(self, period, flags=0): self.asked += 1

    worker = PollWorker()
    dom = _Dom()
    for _ in range(5):
        worker._enable_balloon_stats("uuid-1", dom, {"balloon.current": 1})
    assert dom.asked == 1

    # and not at all once it is reporting
    other = _Dom()
    worker._enable_balloon_stats("uuid-2", other, {"balloon.usable": 10})
    assert other.asked == 0
