"""Per-vCPU CPU, and the toggle between it and the overall figure.

The overall figure is the guest's total divided by its vCPU count, so a
machine running one thread flat out reads 8% on twelve cores and looks
idle. Both numbers are useful and they answer different questions.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication  # noqa: F401 - PollWorker is a QThread

from vmmanager.core.poller import PollWorker


@pytest.fixture
def worker(qapp):
    return PollWorker()


def _cpu_sample(total_ns, per_cpu_ns, count=4):
    raw = {"cpu.time": total_ns, "vcpu.current": count, "vcpu.maximum": count}
    for i, value in enumerate(per_cpu_ns):
        raw[f"vcpu.{i}.time"] = value
    return raw


def test_each_vcpu_is_measured_on_its_own(worker, qapp):
    """One second of wall clock; core 0 busy the whole of it, the rest idle."""
    before = _cpu_sample(0, [0, 0, 0, 0])
    after = _cpu_sample(int(1e9), [int(1e9), 0, 0, 0])
    worker._domain_usage("u", before, 0.0, vcpus=4)
    usage = worker._domain_usage("u", after, 1.0, vcpus=4)

    assert usage.vcpus == (100.0, 0.0, 0.0, 0.0)


def test_the_overall_figure_is_still_the_total_over_the_count(worker, qapp):
    """The thing this exists to explain: one pinned core out of four is
    100% on that core and 25% overall."""
    before = _cpu_sample(0, [0, 0, 0, 0])
    after = _cpu_sample(int(1e9), [int(1e9), 0, 0, 0])
    worker._domain_usage("u", before, 0.0, vcpus=4)
    usage = worker._domain_usage("u", after, 1.0, vcpus=4)

    assert usage.cpu_pct == 25.0
    assert max(usage.vcpus) == 100.0


def test_a_reading_is_never_over_a_hundred(worker, qapp):
    before = _cpu_sample(0, [0])
    after = _cpu_sample(int(5e9), [int(5e9)], count=1)
    worker._domain_usage("u", before, 0.0, vcpus=1)
    usage = worker._domain_usage("u", after, 1.0, vcpus=1)
    assert usage.vcpus == (100.0,)


def test_a_counter_that_went_backwards_reads_as_idle(worker, qapp):
    before = _cpu_sample(0, [int(9e9)], count=1)
    after = _cpu_sample(int(1e9), [5], count=1)
    worker._domain_usage("u", before, 0.0, vcpus=1)
    assert worker._domain_usage("u", after, 1.0, vcpus=1).vcpus == (0.0,)


def test_cores_that_could_be_hot_plugged_are_not_drawn_yet(worker, qapp):
    """A machine with 4 vCPUs and room for 16 has four bars, not sixteen
    with twelve of them permanently empty."""
    before = {"cpu.time": 0, "vcpu.current": 4, "vcpu.maximum": 16,
              **{f"vcpu.{i}.time": 0 for i in range(4)}}
    after = {"cpu.time": int(1e9), "vcpu.current": 4, "vcpu.maximum": 16,
             **{f"vcpu.{i}.time": int(1e9) for i in range(4)}}
    worker._domain_usage("u", before, 0.0, vcpus=4)
    assert len(worker._domain_usage("u", after, 1.0, vcpus=4).vcpus) == 4


def test_a_hypervisor_that_reports_no_per_cpu_times_gives_none(worker, qapp):
    worker._domain_usage("u", {"cpu.time": 0}, 0.0, vcpus=2)
    usage = worker._domain_usage("u", {"cpu.time": int(1e9)}, 1.0, vcpus=2)
    assert usage.vcpus == ()
    assert usage.cpu_pct == 50.0, "the overall figure still works"


# ------------------------------------------------------------------- the card

def test_the_card_offers_the_toggle_only_where_it_means_something(qapp):
    from vmmanager.pages.detail.common import ChartCard

    cpu = ChartCard("cpu", max_value=100.0, per_core=True)
    memory = ChartCard("memory")
    try:
        assert cpu.mode_btn is not None and cpu.cores is not None
        assert memory.mode_btn is None and memory.cores is None
    finally:
        cpu.deleteLater()
        memory.deleteLater()


def test_switching_swaps_the_line_for_the_bars(qapp, scratch_settings):
    from vmmanager.pages.detail.common import ChartCard

    card = ChartCard("cpu", max_value=100.0, per_core=True)
    card.show()
    try:
        assert not card.showing_cores()
        assert card.spark.isVisible() and not card.cores.isVisible()

        card.mode_btn.setChecked(True)
        assert card.showing_cores()
        assert card.cores.isVisible() and not card.spark.isVisible()
        assert card.mode_btn.text() == "overall", "it offers the way back"

        card.mode_btn.setChecked(False)
        assert card.spark.isVisible() and not card.cores.isVisible()
    finally:
        card.deleteLater()


def test_the_choice_is_remembered(qapp, scratch_settings):
    from vmmanager.pages.detail.common import ChartCard
    from vmmanager.pages.settings import cpu_per_core

    assert not cpu_per_core()
    card = ChartCard("cpu", max_value=100.0, per_core=True)
    try:
        card.mode_btn.setChecked(True)
        assert cpu_per_core(), "the next machine opens on the same view"
    finally:
        card.deleteLater()


def test_the_bars_clamp_rather_than_overflowing(qapp):
    from vmmanager.widgets import CoreBars

    bars = CoreBars()
    try:
        bars.set_values([-10.0, 50.0, 150.0])
        assert bars._values == [0.0, 50.0, 100.0]
    finally:
        bars.deleteLater()


def test_the_bars_draw_without_data(qapp):
    """A machine that is not running has no per-core figures at all."""
    from vmmanager.widgets import CoreBars

    bars = CoreBars()
    bars.resize(200, 60)
    try:
        bars.set_values([])
        bars.render(bars.grab())  # must not raise
    finally:
        bars.deleteLater()
