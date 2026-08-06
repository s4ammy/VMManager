"""The host itself: what it has, what is using it, and what it has been doing.

The sidebar has always carried a four-line summary of the host in its
corner, which answers "is the box busy" and nothing else. The question you
actually have when you open the app is "busy with what" - and the answer is
already in the stats store, one row per machine per tick, waiting to be
added up.

Two halves. The top is the host as it is now: its CPU and memory over time,
and what it is made of. The bottom attributes that load to machines, over
whatever range is chosen, so a host at 90% names the guest responsible
rather than leaving you to click through them one at a time.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..data.history import query_history
from ..widgets.format import fmt_bytes, fmt_mem
from ..tasks import run_task
from ..widgets import Sparkline

# The ranges worth looking at a host over. "Live" is the poller's own ring;
# the rest come out of the stats store.
RANGES: tuple[tuple[str, int], ...] = (
    ("live", 0),
    ("1 hour", 3600),
    ("6 hours", 6 * 3600),
    ("24 hours", 24 * 3600),
    ("7 days", 7 * 86400),
)


class _Card(QFrame):
    """A titled chart, the same shape as the ones on a machine's overview."""

    def __init__(self, title: str, max_value: float | None = None) -> None:
        super().__init__()
        self.setProperty("class", "ChartCard")
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 13, 16, 13)
        box.setSpacing(6)
        head = QHBoxLayout()
        label = QLabel(title.upper())
        label.setProperty("class", "ChartTitle")
        self.value = QLabel(" - ")
        self.value.setProperty("class", "ChartValue")
        head.addWidget(label)
        head.addStretch(1)
        head.addWidget(self.value)
        box.addLayout(head)
        self.spark = Sparkline(max_value=max_value)
        self.spark.setMinimumHeight(90)
        box.addWidget(self.spark, 1)


class HostPage(QWidget):
    """Mirrors the machine overview, for the machine underneath them all."""

    def __init__(self) -> None:
        super().__init__()
        self._host = None
        self._domains: list = []

        box = QVBoxLayout(self)
        box.setContentsMargins(28, 24, 28, 24)
        box.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("Host")
        title.setProperty("class", "PageTitle")
        head.addWidget(title)
        head.addStretch(1)
        range_label = QLabel("RANGE")
        range_label.setProperty("class", "StatKey")
        head.addWidget(range_label)
        self.range_combo = QComboBox()
        for label, _secs in RANGES:
            self.range_combo.addItem(label)
        self.range_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        head.addWidget(self.range_combo)
        box.addLayout(head)

        self.subtitle = QLabel("")
        self.subtitle.setObjectName("ConsoleHint")
        box.addWidget(self.subtitle)

        self.chips = QHBoxLayout()
        self.chips.setSpacing(8)
        box.addLayout(self.chips)

        charts = QGridLayout()
        charts.setSpacing(14)
        self.chart_cpu = _Card("host cpu", max_value=100.0)
        self.chart_mem = _Card("host memory")
        charts.addWidget(self.chart_cpu, 0, 0)
        charts.addWidget(self.chart_mem, 0, 1)
        box.addLayout(charts)

        attribution = QLabel("WHAT IS USING IT")
        attribution.setProperty("class", "StatKey")
        box.addWidget(attribution)
        self.note = QLabel(
            "Averages over the chosen range, from the recorded history - so a "
            "machine that was busy an hour ago still shows here even if it is "
            "idle now."
        )
        self.note.setObjectName("ConsoleHint")
        self.note.setWordWrap(True)
        box.addWidget(self.note)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["machine", "cpu", "memory", "disk i/o", "network i/o"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        box.addWidget(self.table, 1)

    # ------------------------------------------------------------ updates

    def update_from(self, domains, host) -> None:
        """Called on every poll tick, whether or not this page is showing."""
        self._domains = list(domains)
        self._host = host
        if self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        if self._host is None:
            return
        self._draw_chips()
        if self._range_secs() == 0:
            self._draw_live()
        else:
            self._draw_history()

    def _range_secs(self) -> int:
        return RANGES[self.range_combo.currentIndex()][1]

    def _draw_chips(self) -> None:
        host = self._host
        while self.chips.count():
            item = self.chips.takeAt(0)
            # Hold the widget: setParent(None) takes it out of the layout
            # item, so asking the item for it a second time gets None.
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        running = sum(1 for d in self._domains if d.state == "running")
        for key, value in (
            ("node", getattr(host, "hostname", "") or "-"),
            ("hypervisor", getattr(host, "hypervisor", "") or "-"),
            ("cpus", str(getattr(host, "cpus", 0) or "-")),
            ("memory", fmt_mem(getattr(host, "memory_mb", 0))),
            ("machines", f"{running} of {len(self._domains)} up"),
        ):
            chip = QFrame()
            chip.setProperty("class", "SpecChip")
            inner = QVBoxLayout(chip)
            inner.setContentsMargins(12, 6, 12, 6)
            inner.setSpacing(0)
            k = QLabel(key.upper())
            k.setProperty("class", "StatKey")
            v = QLabel(value)
            v.setProperty("class", "StatVal")
            inner.addWidget(k)
            inner.addWidget(v)
            self.chips.addWidget(chip)
        self.chips.addStretch(1)

    def _draw_live(self) -> None:
        host = self._host
        history = list(getattr(host, "history", []) or [])
        self.chart_cpu.spark.set_values([u.cpu_pct for u in history])
        self.chart_mem.spark.set_max(getattr(host, "memory_mb", 0) or None)
        self.chart_mem.spark.set_values([u.mem_mb for u in history])
        self.chart_cpu.value.setText(f"{getattr(host, 'cpu_pct', 0):.0f}%")
        self.chart_mem.value.setText(
            f"{fmt_mem(getattr(host, 'mem_used_mb', 0))} / "
            f"{fmt_mem(getattr(host, 'memory_mb', 0))}"
        )
        self.subtitle.setText("live · the last few minutes")
        rows = [
            (d.name, d.usage.cpu_pct, d.usage.mem_mb, d.usage.disk_bps,
             d.usage.net_bps)
            for d in self._domains if d.state == "running"
        ]
        self._fill(rows, "Nothing is running.")

    def _draw_history(self) -> None:
        """Averages per machine over the range, out of the stats store."""
        secs = self._range_secs()
        names = {d.uuid: d.name for d in self._domains}
        self.subtitle.setText(f"recorded history · last {self.range_combo.currentText()}")

        import time

        end = time.time()
        start = end - secs

        def work():
            host_series = query_history("", start, end)
            per_machine = []
            for uuid, name in names.items():
                points = [p for p in query_history(uuid, start, end) if any(p)]
                if not points:
                    continue
                per_machine.append((
                    name,
                    _mean(p[0] for p in points),
                    _mean(p[1] for p in points),
                    _mean(p[2] for p in points),
                    _mean(p[3] for p in points),
                ))
            return host_series, per_machine

        run_task(work, done=self._history_arrived, failed=lambda _m: None)

    def _history_arrived(self, result) -> None:
        host_series, rows = result
        self.chart_cpu.spark.set_values([p[0] for p in host_series])
        self.chart_mem.spark.set_max(getattr(self._host, "memory_mb", 0) or None)
        self.chart_mem.spark.set_values([p[1] for p in host_series])
        points = [p for p in host_series if any(p)]
        if points:
            self.chart_cpu.value.setText(f"{_mean(p[0] for p in points):.0f}% avg")
            self.chart_mem.value.setText(
                f"{fmt_mem(_mean(p[1] for p in points))} avg"
            )
        else:
            self.chart_cpu.value.setText(" - ")
            self.chart_mem.value.setText(" - ")
        self._fill(rows, "No history recorded for this range yet.")

    def _fill(self, rows, empty_note: str) -> None:
        # Busiest first: the row you came here to find should be the top one.
        rows = sorted(rows, key=lambda r: r[1], reverse=True)
        self.note.setVisible(bool(rows))
        self.table.setRowCount(len(rows))
        for i, (name, cpu, mem, disk, net) in enumerate(rows):
            for column, text in enumerate((
                name, f"{cpu:.0f}%", fmt_mem(mem),
                f"{fmt_bytes(disk)}/s", f"{fmt_bytes(net)}/s",
            )):
                item = QTableWidgetItem(text)
                if column:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(i, column, item)
        if not rows:
            self.note.setText(empty_note)
            self.note.setVisible(True)


def _mean(values) -> float:
    collected = list(values)
    return sum(collected) / len(collected) if collected else 0.0
