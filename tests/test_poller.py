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
