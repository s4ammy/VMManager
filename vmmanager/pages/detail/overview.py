"""Overview tab: live charts, history scrub-back, spec chips."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports for the tab modules


class OverviewMixin:
    """Mixed into DetailPage; expects its attributes."""
    def _build_overview(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 0)
        box.setSpacing(12)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        range_label = QLabel("RANGE")
        range_label.setProperty("class", "StatKey")
        controls.addWidget(range_label)
        self.range_combo = QComboBox()
        for label, _secs in _RANGES:
            self.range_combo.addItem(label)
        self.range_combo.currentIndexChanged.connect(self._range_changed)
        controls.addWidget(self.range_combo)
        self.scrub = QSlider(Qt.Orientation.Horizontal)
        self.scrub.setRange(0, 1000)
        self.scrub.setValue(1000)
        self.scrub.setEnabled(False)
        self.scrub.valueChanged.connect(lambda _v: self._scrub_timer.start())
        controls.addWidget(self.scrub, 1)
        self.window_label = QLabel("")
        self.window_label.setObjectName("ConsoleHint")
        controls.addWidget(self.window_label)
        box.addLayout(controls)

        self.chips_row = QHBoxLayout()
        self.chips_row.setSpacing(8)
        box.addLayout(self.chips_row)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.chart_cpu = ChartCard("cpu", max_value=100.0)
        self.chart_mem = ChartCard("memory")
        self.chart_disk = ChartCard("disk i/o")
        self.chart_net = ChartCard("network i/o")
        grid.addWidget(self.chart_cpu, 0, 0)
        grid.addWidget(self.chart_mem, 0, 1)
        grid.addWidget(self.chart_disk, 1, 0)
        grid.addWidget(self.chart_net, 1, 1)
        box.addLayout(grid, 1)
        return page

    def _range_secs(self) -> int:
        return _RANGES[self.range_combo.currentIndex()][1]

    def _range_changed(self, _index: int) -> None:
        live = self._range_secs() == 0
        self.scrub.setEnabled(not live)
        if live:
            self.window_label.setText("")
            if self._snap is not None:
                self._apply_live_charts(self._snap)
        else:
            self.scrub.blockSignals(True)
            self.scrub.setValue(1000)
            self.scrub.blockSignals(False)
            self._load_history()

    def _load_history(self) -> None:
        if not self.uuid or self._range_secs() == 0:
            return
        uuid = self.uuid
        span = self._range_secs()
        frac = self.scrub.value() / 1000.0

        def work():
            now = time.time()
            extent = data_extent(uuid)
            oldest = extent[0] if extent else now - span
            earliest_end = min(oldest + span, now)
            end = earliest_end + (now - earliest_end) * frac
            return end, query_history(uuid, end - span, end)

        def apply(result) -> None:
            if self.uuid != uuid or self._range_secs() != span:
                return
            end, rows = result
            self.chart_cpu.spark.set_values([r[0] for r in rows])
            self.chart_mem.spark.set_values([r[1] for r in rows])
            self.chart_disk.spark.set_values([r[2] for r in rows])
            self.chart_net.spark.set_values([r[3] for r in rows])
            for card in (self.chart_cpu, self.chart_mem, self.chart_disk, self.chart_net):
                card.value.setText("history")
            fmt = "%H:%M" if span <= 86400 else "%m-%d %H:%M"
            start_s = datetime.datetime.fromtimestamp(end - span).strftime(fmt)
            end_s = datetime.datetime.fromtimestamp(end).strftime(fmt)
            self.window_label.setText(f"{start_s} → {end_s}")

        run_task(work, done=apply, failed=lambda _m: None)

    def _apply_live_charts(self, snap: DomainSnapshot) -> None:
        hist = snap.history
        self.chart_mem.spark.set_max(snap.memory_mb or None)
        self.chart_cpu.spark.set_values([u.cpu_pct for u in hist])
        self.chart_mem.spark.set_values([u.mem_mb for u in hist])
        self.chart_disk.spark.set_values([u.disk_bps for u in hist])
        self.chart_net.spark.set_values([u.net_bps for u in hist])
        if snap.state == "running":
            self.chart_cpu.value.setText(f"{snap.usage.cpu_pct:.0f}%")
            self.chart_mem.value.setText(
                f"{fmt_mem(snap.usage.mem_mb)} / {fmt_mem(snap.memory_mb)}"
            )
            self.chart_disk.value.setText(f"{fmt_bytes(snap.usage.disk_bps)}/s")
            self.chart_net.value.setText(f"{fmt_bytes(snap.usage.net_bps)}/s")
        else:
            for card in (self.chart_cpu, self.chart_mem, self.chart_disk, self.chart_net):
                card.value.setText(" - ")

    def _update_chips(self, hw: Hardware) -> None:
        while self.chips_row.count():
            item = self.chips_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        mem = fmt_mem(hw.memory_mb)
        if hw.max_memory_mb != hw.memory_mb:
            mem += f" / {fmt_mem(hw.max_memory_mb)}"
        chips = [
            ("machine", hw.machine),
            ("firmware", hw.firmware),
            ("vcpu", str(hw.vcpus)),
            ("memory", mem),
            ("video", hw.video),
            ("boot", " → ".join(hw.boot) if hw.boot else "hd"),
        ]
        for key, value in chips:
            chip = QFrame()
            chip.setObjectName("SpecChip")
            box = QVBoxLayout(chip)
            box.setContentsMargins(12, 6, 12, 7)
            box.setSpacing(1)
            k = QLabel(key.upper())
            k.setProperty("class", "StatKey")
            v = QLabel(value)
            v.setProperty("class", "StatVal Body")
            box.addWidget(k)
            box.addWidget(v)
            self.chips_row.addWidget(chip)
        self.chips_row.addStretch(1)
