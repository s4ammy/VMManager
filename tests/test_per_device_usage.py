"""Which disk is busy, not just how busy the disks are.

The overview drew one line for all disks and one for all networks, so a
machine with three disks could not show which of them was thrashing. The
figures were always there - libvirt reports them per device - they were
just summed on the way past.

Kept live only. One row per device per tick over thirty days is a lot of
database for a question ("which disk is busy") that is always about now.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication  # noqa: F401 - PollWorker is a QThread

from vmmanager.core.poller import PollWorker


@pytest.fixture
def worker(qapp):
    return PollWorker()


def _sample(**counters):
    """Raw stats in libvirt's own shape."""
    return counters


def test_each_disk_is_reported_under_the_name_the_guest_knows(worker, qapp):
    before = _sample(
        **{"block.count": 2,
           "block.0.name": "vda", "block.0.rd.bytes": 0, "block.0.wr.bytes": 0,
           "block.1.name": "sdb", "block.1.rd.bytes": 0, "block.1.wr.bytes": 0}
    )
    after = _sample(
        **{"block.count": 2,
           "block.0.name": "vda", "block.0.rd.bytes": 1000, "block.0.wr.bytes": 2000,
           "block.1.name": "sdb", "block.1.rd.bytes": 500, "block.1.wr.bytes": 0}
    )
    worker._domain_usage("u", before, 100.0, vcpus=1)
    usage = worker._domain_usage("u", after, 101.0, vcpus=1)

    assert usage.disks == (("vda", 3000.0), ("sdb", 500.0))
    assert usage.disk_bps == 3500.0, "the total is still the sum of them"


def test_each_interface_too(worker, qapp):
    before = _sample(**{"net.count": 2,
                        "net.0.name": "vnet0", "net.0.rx.bytes": 0, "net.0.tx.bytes": 0,
                        "net.1.name": "vnet1", "net.1.rx.bytes": 0, "net.1.tx.bytes": 0})
    after = _sample(**{"net.count": 2,
                       "net.0.name": "vnet0", "net.0.rx.bytes": 400, "net.0.tx.bytes": 100,
                       "net.1.name": "vnet1", "net.1.rx.bytes": 0, "net.1.tx.bytes": 0})
    worker._domain_usage("u", before, 10.0, vcpus=2)
    usage = worker._domain_usage("u", after, 12.0, vcpus=2)

    assert usage.nets == (("vnet0", 250.0), ("vnet1", 0.0))
    assert usage.net_bps == 250.0


def test_a_device_libvirt_does_not_name_still_gets_a_row(worker, qapp):
    """Older libvirt omits the name for some device types; a row with no
    label is worse than one labelled by its position."""
    before = _sample(**{"block.count": 1, "block.0.rd.bytes": 0})
    after = _sample(**{"block.count": 1, "block.0.rd.bytes": 100})
    worker._domain_usage("u", before, 0.0, vcpus=1)
    usage = worker._domain_usage("u", after, 1.0, vcpus=1)

    assert usage.disks == (("block0", 100.0),)


def test_a_counter_that_went_backwards_is_ignored(worker, qapp):
    """A device removed and re-added reuses the index with a fresh counter,
    which as a difference is a large negative number."""
    before = _sample(**{"block.count": 1, "block.0.name": "vda",
                        "block.0.rd.bytes": 10_000_000})
    after = _sample(**{"block.count": 1, "block.0.name": "vda",
                       "block.0.rd.bytes": 5})
    worker._domain_usage("u", before, 0.0, vcpus=1)
    usage = worker._domain_usage("u", after, 1.0, vcpus=1)

    assert usage.disks == (("vda", 0.0),)
    assert usage.disk_bps == 0.0


def test_the_first_sample_has_nothing_to_compare_against(worker, qapp):
    usage = worker._domain_usage("u", _sample(**{"block.count": 1}), 0.0, vcpus=1)
    assert usage.disks == () and usage.nets == ()


def test_a_machine_with_no_devices_reports_none(worker, qapp):
    worker._domain_usage("u", _sample(), 0.0, vcpus=1)
    usage = worker._domain_usage("u", _sample(), 1.0, vcpus=1)
    assert usage.disks == () and usage.nets == ()
    assert usage.disk_bps == 0.0
