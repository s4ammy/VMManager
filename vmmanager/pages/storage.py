"""Storage page: pools with capacity bars and their volumes."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..dialogs import (
    ConfirmDialog,
    ErrorDialog,
    ResizeVolumeDialog,
    VolumeDialog,
)
from ..libvirt_service import (
    PoolInfo,
    svc_create_volume,
    svc_delete_pool,
    svc_delete_volume,
    svc_list_pools,
    svc_pool_action,
    svc_resize_volume,
)
from ..tasks import run_task
from ..widgets import UsageBar, fmt_size


class PoolCard(QFrame):
    def __init__(self, page: "StoragePage", pool: PoolInfo) -> None:
        super().__init__()
        self.setProperty("class", "ChartCard")
        self.pool_name = pool.name
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 15, 18, 15)
        box.setSpacing(8)

        head = QHBoxLayout()
        name = QLabel(pool.name)
        name.setProperty("class", "SectionTitle")
        state = QLabel("ACTIVE" if pool.active else "INACTIVE")
        state.setObjectName("VmState")
        state.setStyleSheet(
            f"color: {theme.OK if pool.active else theme.TEXT_FAINT};"
        )
        path = QLabel(pool.path)
        path.setProperty("class", "StatVal")
        head.addWidget(name)
        head.addWidget(state)
        head.addStretch(1)
        head.addWidget(path)

        toggle = QPushButton("Stop" if pool.active else "Start")
        toggle.setProperty("class", "GhostButton")
        toggle.clicked.connect(
            lambda: page.pool_action(pool.name, "stop" if pool.active else "start")
        )
        auto = QPushButton(
            "Disable autostart" if pool.autostart else "Enable autostart"
        )
        auto.setProperty("class", "GhostButton")
        auto.clicked.connect(
            lambda: page.pool_action(
                pool.name, "autostart-off" if pool.autostart else "autostart-on"
            )
        )
        forget = QPushButton("Delete pool…")
        forget.setProperty("class", "GhostButton")
        forget.clicked.connect(lambda: page.delete_pool(pool.name))
        head.addWidget(toggle)
        head.addWidget(auto)
        head.addWidget(forget)
        box.addLayout(head)

        if pool.active and pool.capacity:
            bar_row = QHBoxLayout()
            bar = UsageBar()
            bar.set_fraction(pool.allocation / pool.capacity)
            usage = QLabel(
                f"{fmt_size(pool.allocation)} / {fmt_size(pool.capacity)}"
                f" · {fmt_size(pool.available)} free"
            )
            usage.setProperty("class", "StatVal")
            bar_row.addWidget(bar, 1)
            bar_row.addWidget(usage)
            box.addLayout(bar_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Volume", "Format", "Capacity", "Allocated"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setRowCount(len(pool.volumes))
        for r, vol in enumerate(pool.volumes):
            self.table.setItem(r, 0, QTableWidgetItem(vol.name))
            self.table.setItem(r, 1, QTableWidgetItem(vol.format))
            self.table.setItem(r, 2, QTableWidgetItem(fmt_size(vol.capacity)))
            self.table.setItem(r, 3, QTableWidgetItem(fmt_size(vol.allocation)))
        for c in range(self.table.columnCount() - 1):
            self.table.resizeColumnToContents(c)
            self.table.setColumnWidth(c, self.table.columnWidth(c) + 24)
        self.table.setMinimumHeight(min(90 + 30 * len(pool.volumes), 300))
        box.addWidget(self.table)

        actions = QHBoxLayout()
        actions.addStretch(1)
        resize = QPushButton("Resize selected volume")
        resize.setProperty("class", "GhostButton")
        resize.clicked.connect(lambda: page.resize_volume(self))
        delete = QPushButton("Delete selected volume")
        delete.setProperty("class", "GhostButton")
        delete.clicked.connect(lambda: page.delete_volume(self))
        actions.addWidget(resize)
        actions.addWidget(delete)
        box.addLayout(actions)
        self._volumes = pool.volumes

    def selected_volume(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.text() if item else None

    def selected_volume_info(self):
        row = self.table.currentRow()
        return self._volumes[row] if 0 <= row < len(self._volumes) else None


class StoragePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        content = QVBoxLayout(self)
        content.setContentsMargins(36, 30, 36, 0)
        content.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(10)  # inherits 0 from `content` otherwise
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("Storage")
        title.setObjectName("PageTitle")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("PageSub")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        head.addLayout(title_box)
        head.addStretch(1)
        reclaim_btn = QPushButton("Reclaim space ▾")
        reclaim_btn.setProperty("class", "GhostButton")
        reclaim_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reclaim_btn.clicked.connect(lambda: self._reclaim_menu(reclaim_btn))
        head.addWidget(reclaim_btn, alignment=Qt.AlignmentFlag.AlignTop)
        new_pool_btn = QPushButton("+ New pool")
        new_pool_btn.setProperty("class", "GhostButton")
        new_pool_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_pool_btn.clicked.connect(self._new_pool)
        head.addWidget(new_pool_btn, alignment=Qt.AlignmentFlag.AlignTop)
        new_btn = QPushButton("+ New volume")
        new_btn.setProperty("class", "PrimaryButton")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.clicked.connect(self._new_volume)
        head.addWidget(new_btn, alignment=Qt.AlignmentFlag.AlignTop)
        content.addLayout(head)
        content.addSpacing(20)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.pool_list = QVBoxLayout(inner)
        self.pool_list.setContentsMargins(0, 0, 6, 30)
        self.pool_list.setSpacing(14)
        self.pool_list.addStretch(1)
        scroll.setWidget(inner)
        content.addWidget(scroll, 1)

        self._pools: list[PoolInfo] = []

    def refresh(self) -> None:
        run_task(
            svc_list_pools,
            done=self._apply,
            failed=lambda m: ErrorDialog(self, "libvirt error", m).exec(),
        )

    def _apply(self, pools: list[PoolInfo]) -> None:
        self._pools = pools
        while self.pool_list.count() > 1:
            item = self.pool_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, pool in enumerate(pools):
            self.pool_list.insertWidget(i, PoolCard(self, pool))
        total_vols = sum(len(p.volumes) for p in pools)
        self.subtitle.setText(f"{len(pools)} pools · {total_vols} volumes")

    def _new_volume(self) -> None:
        active = [p.name for p in self._pools if p.active]
        if not active:
            ErrorDialog(self, "No active pools", "Start a storage pool first.").exec()
            return
        dialog = VolumeDialog(self, active)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        pool = dialog.pool.currentText()
        name = dialog.name.text().strip()
        size = dialog.size.value()
        fmt = dialog.format.currentText()
        run_task(
            lambda: svc_create_volume(pool, name, size, fmt),
            done=lambda _: self.refresh(),
            failed=lambda m: ErrorDialog(self, "Create failed", m).exec(),
        )

    def delete_volume(self, card: PoolCard) -> None:
        vol = card.selected_volume()
        if not vol:
            return
        confirm = ConfirmDialog(
            self,
            "Delete volume",
            f"Delete '{vol}' from pool '{card.pool_name}'? Any machine using "
            "it will lose the disk. This can't be undone.",
            "Delete volume",
        )
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        run_task(
            lambda: svc_delete_volume(card.pool_name, vol),
            done=lambda _: self.refresh(),
            failed=lambda m: ErrorDialog(self, "Delete failed", m).exec(),
        )

    def resize_volume(self, card: PoolCard) -> None:
        vol = card.selected_volume_info()
        if vol is None:
            return
        dialog = ResizeVolumeDialog(self, vol.name, vol.capacity / 1024**3)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_gb = dialog.size.value()
        if new_gb < vol.capacity / 1024**3:
            confirm = ConfirmDialog(
                self,
                "Shrink volume",
                f"Shrinking '{vol.name}' below its current size can destroy "
                "data inside the guest. Continue?",
                "Shrink anyway",
            )
            if confirm.exec() != QDialog.DialogCode.Accepted:
                return
        run_task(
            lambda: svc_resize_volume(card.pool_name, vol.name, new_gb),
            done=lambda _: self.refresh(),
            failed=lambda m: ErrorDialog(self, "Resize failed", m).exec(),
        )

    def pool_action(self, name: str, op: str) -> None:
        run_task(
            lambda: svc_pool_action(name, op),
            done=lambda _: self.refresh(),
            failed=lambda m: ErrorDialog(self, "Pool action failed", m).exec(),
        )

    def _new_pool(self) -> None:
        from ..core.storage import svc_create_pool_ex
        from ..dialogs import PoolDialog

        dialog = PoolDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.name.text().strip()
        ptype = dialog.pool_type()
        target = dialog.target.text().strip()
        options = dialog.options()
        run_task(
            lambda: svc_create_pool_ex(name, ptype, target, options),
            done=lambda _: self.refresh(),
            failed=lambda m: ErrorDialog(self, "Create pool failed", m).exec(),
        )

    def _reclaim_menu(self, anchor: QPushButton) -> None:
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction("Find unused volumes…", self._reclaim)
        menu.addAction("Compact disk images…", self._compact)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _compact(self) -> None:
        """Rewrite qcow2 images to drop clusters their data no longer needs."""
        from ..libvirt_service import svc_compact_candidates

        self.subtitle.setText("measuring disk images…")

        def show(candidates) -> None:
            self.subtitle.setText("")
            if not candidates:
                ErrorDialog(
                    self, "Nothing to compact",
                    "No qcow2 image here is big enough to be worth rewriting.",
                ).exec()
                return
            from PySide6.QtWidgets import QCheckBox, QVBoxLayout as _V

            from ..dialogs import _buttons, _title

            dialog = QDialog(self)
            dialog.setWindowTitle("Compact disk images")
            dialog.setMinimumWidth(620)
            box = _V(dialog)
            box.setContentsMargins(24, 22, 24, 20)
            box.setSpacing(10)
            box.addWidget(_title("Compact disk images"))
            note = QLabel(
                "Compacting rewrites an image without the clusters its data "
                "no longer needs. The rewrite happens beside the original, so "
                "a failure leaves the original untouched. Machines using an "
                "image must be shut off.\n\n"
                "'At least' figures are a floor: an image whose freed space "
                "was overwritten rather than discarded usually shrinks much "
                "more, and the real saving is reported afterwards."
            )
            note.setWordWrap(True)
            note.setProperty("class", "Dim")
            box.addWidget(note)
            checks = []
            total = 0
            for c in candidates:
                total += c.slack
                label = f"{c.pool}/{c.name} · {fmt_size(c.allocation)} on disk"
                if c.slack > 0:
                    label += f" · at least {fmt_size(c.slack)} reclaimable"
                if c.in_use_by:
                    label += f" · used by {c.in_use_by}"
                    if c.running:
                        label += " (running)"
                check = QCheckBox(label)
                check.setEnabled(not c.running)
                box.addWidget(check)
                checks.append((check, c))
            summary = QLabel(f"at least {fmt_size(total)} reclaimable in total")
            summary.setObjectName("ConsoleHint")
            box.addWidget(summary)
            box.addLayout(_buttons(dialog, "Compact selected"))
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            chosen = [c for check, c in checks if check.isChecked() and not c.running]
            if not chosen:
                return
            self.subtitle.setText(f"compacting {len(chosen)} image(s)…")

            def work():
                from ..libvirt_service import svc_compact_volume

                messages = []
                for c in chosen:
                    messages.append(svc_compact_volume(c.pool, c.name))
                return " · ".join(messages)

            run_task(
                work,
                done=lambda msg: (self.subtitle.setText(str(msg)), self.refresh()),
                failed=lambda m: (
                    self.subtitle.setText(""),
                    ErrorDialog(self, "Compaction failed", m).exec(),
                ),
            )

        run_task(
            svc_compact_candidates, done=show,
            failed=lambda m: ErrorDialog(self, "Scan failed", m).exec(),
        )

    def _reclaim(self) -> None:
        from ..libvirt_service import svc_orphan_volumes

        self.subtitle.setText("scanning for unused volumes…")

        def show(orphans) -> None:
            self.subtitle.setText("")
            if not orphans:
                ErrorDialog(
                    self, "Nothing to reclaim",
                    "Every volume is referenced by a machine (directly, as a "
                    "backing file, or as NVRAM).",
                ).exec()
                return
            from PySide6.QtWidgets import QCheckBox, QVBoxLayout as _V

            from ..dialogs import _buttons, _title

            dialog = QDialog(self)
            dialog.setWindowTitle("Unused volumes")
            dialog.setMinimumWidth(560)
            box = _V(dialog)
            box.setContentsMargins(24, 22, 24, 20)
            box.setSpacing(10)
            box.addWidget(_title("Unused volumes"))
            note = QLabel(
                "No machine references these, not even as a snapshot backing "
                "file. Check what to delete."
            )
            note.setWordWrap(True)
            note.setProperty("class", "Dim")
            box.addWidget(note)
            checks = []
            total = 0
            for o in orphans:
                total += o.capacity
                c = QCheckBox(f"{o.pool}/{o.name} · {fmt_size(o.capacity)}")
                box.addWidget(c)
                checks.append((c, o))
            summary = QLabel(f"{len(orphans)} volumes · {fmt_size(total)} reclaimable")
            summary.setObjectName("ConsoleHint")
            box.addWidget(summary)
            box.addLayout(_buttons(dialog, "Delete selected", danger=True))
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            doomed = [(o.pool, o.name) for c, o in checks if c.isChecked()]

            def work():
                for pool, name in doomed:
                    svc_delete_volume(pool, name)
                return len(doomed)

            run_task(
                work,
                done=lambda n: (self.subtitle.setText(f"deleted {n} volumes"), self.refresh()),
                failed=lambda m: ErrorDialog(self, "Delete failed", m).exec(),
            )

        run_task(
            svc_orphan_volumes,
            done=show,
            failed=lambda m: ErrorDialog(self, "Scan failed", m).exec(),
        )

    def delete_pool(self, name: str) -> None:
        confirm = ConfirmDialog(
            self,
            "Delete pool",
            f"Stop and forget pool '{name}'? The volumes inside stay on disk "
            "Only the libvirt definition goes away.",
            "Delete pool",
        )
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        run_task(
            lambda: svc_delete_pool(name),
            done=lambda _: self.refresh(),
            failed=lambda m: ErrorDialog(self, "Delete failed", m).exec(),
        )
