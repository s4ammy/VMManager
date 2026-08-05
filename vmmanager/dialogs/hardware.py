"""Hardware dialogs: devices, CPU, memory, boot order, passthrough, Windows tools."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from .. import theme
from .base import SizedDialog, _buttons, _field_label, _title
# "From pool…" is how a remote host's storage is browsed, and three dialogs here
# offer it. storage.py imports nothing from this module, so this way round is
# safe.
from .storage import VolumePickerDialog


class AttachDiskDialog(SizedDialog):
    """Attach storage: a brand-new volume or an existing image/volume."""

    def __init__(self, parent, pools, remote: bool = False) -> None:  # pools: list[PoolInfo]
        super().__init__(parent)
        self.setWindowTitle("Add disk")
        self.setMinimumWidth(460)
        self._pools = pools
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Add disk"))

        self.create_new = QRadioButton("Create a new volume")
        self.use_existing = QRadioButton("Use an existing image")
        self.create_new.setChecked(True)
        box.addWidget(self.create_new)
        box.addWidget(self.use_existing)

        self._stack = QStackedWidget()
        new_page = QWidget()
        new_box = QVBoxLayout(new_page)
        new_box.setContentsMargins(0, 0, 0, 0)
        new_box.setSpacing(8)
        row = QHBoxLayout()
        pool_col = QVBoxLayout()
        pool_col.addWidget(_field_label("pool"))
        self.pool = QComboBox()
        self.pool.addItems([p.name for p in pools if p.active] or ["default"])
        pool_col.addWidget(self.pool)
        size_col = QVBoxLayout()
        size_col.addWidget(_field_label("size (GB)"))
        self.size = QDoubleSpinBox()
        self.size.setRange(0.1, 65536)
        self.size.setValue(20)
        self.size.setDecimals(1)
        size_col.addWidget(self.size)
        row.addLayout(pool_col, 1)
        row.addLayout(size_col)
        new_box.addLayout(row)
        new_box.addWidget(_field_label("volume name"))
        self.vol_name = QLineEdit()
        self.vol_name.setPlaceholderText("data.qcow2")
        new_box.addWidget(self.vol_name)
        self._stack.addWidget(new_page)

        existing_page = QWidget()
        ex_box = QVBoxLayout(existing_page)
        ex_box.setContentsMargins(0, 0, 0, 0)
        ex_box.setSpacing(8)
        ex_box.addWidget(_field_label("image path"))
        path_row = QHBoxLayout()
        self.path = QLineEdit()
        path_row.addWidget(self.path, 1)
        pool_browse = QPushButton("From pool…")
        pool_browse.setProperty("class", "GhostButton")
        pool_browse.clicked.connect(self._pick_volume)
        path_row.addWidget(pool_browse)
        if not remote:
            browse = QPushButton("Browse…")
            browse.setProperty("class", "GhostButton")
            browse.clicked.connect(self._pick)
            path_row.addWidget(browse)
        ex_box.addLayout(path_row)
        ex_box.addStretch(1)
        self._stack.addWidget(existing_page)
        box.addWidget(self._stack)

        box.addWidget(_field_label("bus"))
        self.bus = QComboBox()
        self.bus.addItems(["virtio", "sata", "scsi", "usb"])
        box.addWidget(self.bus)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Add disk"))
        self.create_new.toggled.connect(
            lambda on: self._stack.setCurrentIndex(0 if on else 1)
        )

    def _pick(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose disk image", "", "Disk images (*.qcow2 *.img *.raw);;All files (*)"
        )
        if path:
            self.path.setText(path)

    def _pick_volume(self) -> None:
        picker = VolumePickerDialog(self, self._pools)
        if picker.exec() == QDialog.DialogCode.Accepted and picker.selected_path():
            self.path.setText(picker.selected_path())

class AttachNicDialog(SizedDialog):
    def __init__(self, parent, networks: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add network interface")
        self.setMinimumWidth(420)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Add network interface"))
        box.addWidget(_field_label("network"))
        self.network = QComboBox()
        self.network.addItems(networks or ["default"])
        box.addWidget(self.network)
        box.addWidget(_field_label("model"))
        self.model = QComboBox()
        self.model.addItems(["virtio", "e1000e", "rtl8139"])
        box.addWidget(self.model)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Add interface"))

class CpuDialog(SizedDialog):
    """Processor editor: model and topology; vCPUs follow the topology."""

    def __init__(self, parent, hw, host_cpus: int) -> None:
        # hw: Hardware (cpu_mode, topology, vcpus)
        super().__init__(parent)
        self.setWindowTitle("Processor")
        self.setMinimumWidth(460)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Processor"))
        note = QLabel(
            "vCPU count applies live when the guest allows it; model and "
            "topology apply on the next start."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)

        box.addWidget(_field_label("model"))
        self.mode = QComboBox()
        self.mode.addItems(["host-passthrough", "host-model", "custom"])
        self.mode.setCurrentText(
            hw.cpu_mode
            if hw.cpu_mode in ("host-passthrough", "host-model", "custom")
            else "custom"
        )
        self.mode.setToolTip(
            "host-passthrough: fastest · host-model: migration-friendly · "
            "custom (qemu64): maximum compatibility"
        )
        box.addWidget(self.mode)

        sockets, cores, threads = hw.topology or (1, max(hw.vcpus, 1), 1)
        topo_row = QHBoxLayout()
        topo_row.setSpacing(14)
        for label, attr, value in (
            ("sockets", "sockets", sockets),
            ("cores", "cores", cores),
            ("threads", "threads", threads),
        ):
            col = QVBoxLayout()
            col.addWidget(_field_label(label))
            spin = QSpinBox()
            spin.setRange(1, max(host_cpus, value))
            spin.setValue(value)
            setattr(self, attr, spin)
            col.addWidget(spin)
            topo_row.addLayout(col)
        total_col = QVBoxLayout()
        total_col.addWidget(_field_label("vcpus"))
        self.total = QLabel("")
        self.total.setProperty("class", "ChartValue")
        total_col.addWidget(self.total)
        topo_row.addLayout(total_col)
        box.addLayout(topo_row)
        for spin in (self.sockets, self.cores, self.threads):
            spin.valueChanged.connect(self._recount)
        self._recount()
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Apply"))

    def _recount(self) -> None:
        self.total.setText(
            f"= {self.sockets.value() * self.cores.value() * self.threads.value()}"
        )

    def vcpu_count(self) -> int:
        return self.sockets.value() * self.cores.value() * self.threads.value()

class MemoryDialog(SizedDialog):
    """Memory editor: current (balloon) and maximum."""

    def __init__(self, parent, hw, host_mem_mb: int) -> None:
        # hw: Hardware (memory_mb, max_memory_mb)
        super().__init__(parent)
        self.setWindowTitle("Memory")
        self.setMinimumWidth(440)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Memory"))
        note = QLabel(
            "Current memory balloons live while the machine runs; the "
            "maximum can only change across a restart."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        mem_row = QHBoxLayout()
        mem_row.setSpacing(14)
        cur_col = QVBoxLayout()
        cur_col.addWidget(_field_label("current (MiB)"))
        self.memory = QSpinBox()
        self.memory.setRange(128, max(host_mem_mb, hw.max_memory_mb))
        self.memory.setSingleStep(512)
        self.memory.setValue(hw.memory_mb)
        cur_col.addWidget(self.memory)
        max_col = QVBoxLayout()
        max_col.addWidget(_field_label("maximum (MiB)"))
        self.max_memory = QSpinBox()
        self.max_memory.setRange(128, max(host_mem_mb, hw.max_memory_mb))
        self.max_memory.setSingleStep(512)
        self.max_memory.setValue(hw.max_memory_mb)
        max_col.addWidget(self.max_memory)
        mem_row.addLayout(cur_col)
        mem_row.addLayout(max_col)
        box.addLayout(mem_row)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Apply"))

class BootOrderDialog(SizedDialog):
    def __init__(self, parent, entries: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Boot order")
        self.setMinimumWidth(420)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Boot order"))
        self.list = QListWidget()
        self.list.addItems(entries)
        if entries:
            self.list.setCurrentRow(0)
        box.addWidget(self.list)
        row = QHBoxLayout()
        up = QPushButton("Move up")
        up.setProperty("class", "GhostButton")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("Move down")
        down.setProperty("class", "GhostButton")
        down.clicked.connect(lambda: self._move(1))
        row.addWidget(up)
        row.addWidget(down)
        row.addStretch(1)
        box.addLayout(row)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Apply order"))

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        if row < 0 or not 0 <= row + delta < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(row + delta, item)
        self.list.setCurrentRow(row + delta)

    def entries(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

class HostDeviceDialog(SizedDialog):
    """Pick a host USB or PCI device to pass through."""

    def __init__(self, parent, devices) -> None:  # devices: list[HostDevice]
        super().__init__(parent)
        self.setWindowTitle("Add host device")
        self.setMinimumSize(680, 420)
        self._devices = devices
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Pass through a host device"))
        note = QLabel(
            "The device detaches from the host while the machine uses it. "
            "PCI passthrough needs IOMMU (VT-d/AMD-Vi) enabled."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        self.table = QTableWidget(len(devices), 3)
        self.table.setHorizontalHeaderLabels(["Type", "Address", "Device"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        for r, dev in enumerate(devices):
            self.table.setItem(r, 0, QTableWidgetItem(dev.kind.upper()))
            self.table.setItem(r, 1, QTableWidgetItem(dev.ident))
            self.table.setItem(r, 2, QTableWidgetItem(dev.label))
        for c in range(2):
            self.table.resizeColumnToContents(c)
            self.table.setColumnWidth(c, self.table.columnWidth(c) + 24)
        box.addWidget(self.table, 1)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Attach device"))
        self._ok_button.setEnabled(False)
        self.table.itemSelectionChanged.connect(
            lambda: self._ok_button.setEnabled(self.table.currentRow() >= 0)
        )

    def selected(self):
        row = self.table.currentRow()
        return self._devices[row] if 0 <= row < len(self._devices) else None

class ShareFolderDialog(SizedDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("Share a folder")
        self.setMinimumWidth(460)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Share a host folder"))
        note = QLabel(
            "Mount inside the guest with:  mount -t virtiofs TAG /mnt  "
            "(or -t 9p -o trans=virtio for 9p)."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        box.addWidget(_field_label("host folder"))
        row = QHBoxLayout()
        self.source = QLineEdit()
        browse = QPushButton("Browse…")
        browse.setProperty("class", "GhostButton")
        browse.clicked.connect(self._pick)
        row.addWidget(self.source, 1)
        row.addWidget(browse)
        box.addLayout(row)
        grid = QHBoxLayout()
        tag_col = QVBoxLayout()
        tag_col.addWidget(_field_label("mount tag"))
        self.tag = QLineEdit("shared")
        tag_col.addWidget(self.tag)
        drv_col = QVBoxLayout()
        drv_col.addWidget(_field_label("driver"))
        self.driver = QComboBox()
        self.driver.addItems(["virtiofs", "9p"])
        drv_col.addWidget(self.driver)
        grid.addLayout(tag_col, 1)
        grid.addLayout(drv_col)
        box.addLayout(grid)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Share folder"))
        self._ok_button.setEnabled(False)

        def check() -> None:
            self._ok_button.setEnabled(
                bool(self.source.text().strip()) and bool(self.tag.text().strip())
            )

        self.source.textChanged.connect(check)
        self.tag.textChanged.connect(check)

    def _pick(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose folder to share")
        if path:
            self.source.setText(path)

class VideoDialog(SizedDialog):
    def __init__(self, parent, current: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Video model")
        self.setMinimumWidth(400)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Video model"))
        note = QLabel(
            "virtio for modern guests, qxl for SPICE multi-monitor, "
            "vga for ancient ones. Applies on next start."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        self.model = QComboBox()
        self.model.addItems(["virtio", "qxl", "vga", "bochs", "ramfb", "none"])
        if current in [self.model.itemText(i) for i in range(self.model.count())]:
            self.model.setCurrentText(current)
        box.addWidget(self.model)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Apply"))

class DiskCacheDialog(SizedDialog):
    def __init__(self, parent, dev: str, current: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Disk cache mode")
        self.setMinimumWidth(420)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Cache mode for {dev}"))
        note = QLabel(
            "none is safest for host crashes and best for raw performance; "
            "writeback is faster for bursty writes; unsafe only for throwaway "
            "machines. Applies on next start."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        self.cache = QComboBox()
        self.cache.addItems(
            ["default", "none", "writeback", "writethrough", "directsync", "unsafe"]
        )
        self.cache.setCurrentText(current if current else "default")
        box.addWidget(self.cache)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Apply"))

class PassthroughDialog(SizedDialog):
    """IOMMU groups with a verdict per device - why passthrough will or won't
    work, which is the part everyone gets stuck on."""

    @property
    def COLORS(self) -> dict[str, str]:
        """Read at use, so a theme change is reflected next time it opens."""
        return {"ready": theme.OK, "caution": theme.WARN,
                "blocked": theme.DANGER}

    def __init__(self, parent, report) -> None:  # report: IommuReport
        super().__init__(parent)
        self.setWindowTitle("PCI passthrough diagnostics")
        self.setMinimumSize(760, 560)
        from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

        from ..libvirt_service import passthrough_verdict

        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("PCI passthrough diagnostics"))
        if not report.enabled:
            warn = QLabel(
                "IOMMU is not enabled on this host. Turn on VT-d (Intel) or "
                "AMD-Vi in firmware, then add intel_iommu=on or amd_iommu=on "
                "to the kernel command line and reboot."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {self.COLORS['blocked']};")
            box.addWidget(warn)
        else:
            note = QLabel(
                "Devices in the same IOMMU group must be assigned together. "
                "Pick a device to see whether it can be passed through."
            )
            note.setWordWrap(True)
            note.setProperty("class", "Dim")
            box.addWidget(note)

        tree = QTreeWidget()
        tree.setHeaderLabels(["Device", "Address", "Driver", "Status"])
        tree.setColumnWidth(0, 340)
        tree.setColumnWidth(1, 120)
        tree.setColumnWidth(2, 110)
        groups: dict[int, list] = {}
        for dev in report.devices:
            groups.setdefault(dev.group, []).append(dev)
        for group in sorted(groups):
            members = groups[group]
            parent_item = QTreeWidgetItem([f"IOMMU group {group}", "", "", ""])
            tree.addTopLevelItem(parent_item)
            worst = "ready"
            for dev in members:
                status, why = passthrough_verdict(report, dev)
                if dev.is_bridge:
                    status = "bridge"
                item = QTreeWidgetItem([dev.label, dev.address, dev.driver, status])
                item.setToolTip(0, why)
                item.setToolTip(3, why)
                colour = self.COLORS.get(status)
                if colour:
                    item.setForeground(3, QColor(colour))
                item.setData(0, Qt.ItemDataRole.UserRole, (dev, status, why))
                parent_item.addChild(item)
                if status == "blocked":
                    worst = "blocked"
                elif status == "caution" and worst != "blocked":
                    worst = "caution"
            parent_item.setForeground(0, QColor(self.COLORS.get(worst, theme.TEXT_DIM)))
            parent_item.setExpanded(len(members) <= 4)
        box.addWidget(tree, 1)

        self.explain = QLabel("Select a device for the details.")
        self.explain.setWordWrap(True)
        self.explain.setObjectName("ConsoleHint")
        self.explain.setMinimumHeight(56)
        box.addWidget(self.explain)

        def on_select() -> None:
            items = tree.selectedItems()
            data = items[0].data(0, Qt.ItemDataRole.UserRole) if items else None
            if data is None:
                self.explain.setText("Select a device for the details.")
                self.explain.setStyleSheet("")
                return
            dev, status, why = data
            self.explain.setText(f"{dev.address} - {why}")
            self.explain.setStyleSheet(
                f"color: {self.COLORS.get(status, theme.TEXT_DIM)};"
            )

        tree.itemSelectionChanged.connect(on_select)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.setProperty("class", "GhostButton")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        box.addLayout(row)

class WindowsToolingDialog(SizedDialog):
    """Checklist for making a Windows guest behave like a first-class one."""

    def __init__(self, parent, vm_name: str, state: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle("Windows guest tools")
        self.setMinimumWidth(660)
        self.setMinimumHeight(440);
        self.action = None  # set to a key when the user picks a fix
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Windows guest tools - {vm_name}"))
        note = QLabel(
            "Windows needs the virtio drivers and guest tools before it can "
            "use virtio devices, share a clipboard, or report its state. "
            "Everything below is optional but each one unlocks a feature."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)

        checks = [
            ("agent_responding", "Guest agent responding",
             "Enables clean shutdown, disk-usage reporting and file transfer."),
            ("agent_channel", "Guest agent channel configured",
             "The virtio-serial port qemu-guest-agent talks over."),
            ("spice_agent_channel", "SPICE agent channel configured",
             "Clipboard sharing and display auto-resize."),
            ("tablet", "Tablet device present",
             "Gives the console an exact pointer instead of a relative one."),
            ("virtio_disk", "virtio disk",
             "Much faster than SATA once the driver is installed."),
            ("virtio_net", "virtio network",
             "Faster networking once the driver is installed."),
            ("iso_attached", "virtio-win disc attached",
             "The disc holding the drivers and the guest-tools installer."),
        ]
        for key, label, why in checks:
            ok = bool(state.get(key))
            row = QHBoxLayout()
            mark = QLabel("✓" if ok else "○")
            mark.setFixedWidth(18)
            mark.setProperty("class", "Ok" if ok else "Faint")
            text = QLabel(f"{label} - {why}")
            text.setWordWrap(True)
            text.setProperty("class", "" if ok else "Dim")
            row.addWidget(mark, alignment=Qt.AlignmentFlag.AlignTop)
            row.addWidget(text, 1)
            box.addLayout(row)

        box.addSpacing(6)
        steps = QLabel(
            "In Windows: open the attached disc and run "
            "<b>virtio-win-guest-tools.exe</b>. It installs every driver plus "
            "the QEMU and SPICE agents in one pass. Switching an existing "
            "install to a virtio disk needs the driver present first, so "
            "install the tools while still on SATA."
        )
        steps.setWordWrap(True)
        steps.setTextFormat(Qt.TextFormat.RichText)
        steps.setProperty("class", "Dim")
        box.addWidget(steps)

        box.addSpacing(8)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        def add_action(label: str, key: str, enabled: bool = True) -> None:
            btn = QPushButton(label)
            btn.setProperty("class", "GhostButton" if key != "iso" else "PrimaryButton")
            btn.setEnabled(enabled)
            btn.clicked.connect(lambda: (setattr(self, "action", key), self.accept()))
            buttons.addWidget(btn)

        add_action("Add virtio-win disc…", "iso", not state.get("iso_attached"))
        add_action("Add agent channel", "agent", not state.get("agent_channel"))
        add_action("Add SPICE channel", "spice", not state.get("spice_agent_channel"))
        add_action("Add tablet", "tablet", not state.get("tablet"))
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.setProperty("class", "GhostButton")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        box.addLayout(buttons)


class DisplayFixDialog(SizedDialog):
    """What is holding the graphical console back, and the fixes for it.

    The complaint this answers is "I installed the drivers and the console is
    still slow and still will not resize". Almost always the machine has a
    VGA-class display device, which no driver accelerates and whose resolution
    nothing can retarget - so the guest side was never the problem.
    """

    def __init__(self, parent, vm_name: str, health) -> None:
        super().__init__(parent)
        self.setWindowTitle("Display setup")
        self.setMinimumWidth(660)
        self.actions: list[str] = []  # fix keys, in the order they are applied
        self._health = health
        problems = health.problems()
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Display setup - {vm_name}"))

        summary = QLabel(
            f"Display: {', '.join(health.graphics) or 'none'} · "
            f"device: {health.video_model or 'none'}"
        )
        summary.setProperty("class", "Dim")
        box.addWidget(summary)

        if not problems:
            good = QLabel(
                "Nothing to fix: the machine has an accelerated display device, "
                "the agent channel it needs, and an absolute pointer. If the "
                "console still feels slow, the guest's own driver for this "
                "device is the next thing to check - on Windows that is the "
                "virtio-win disc."
            )
            good.setWordWrap(True)
            box.addWidget(good)
        for key, what, why in problems:
            row = QHBoxLayout()
            mark = QLabel("○")
            mark.setFixedWidth(18)
            mark.setProperty("class", "Faint")
            text = QLabel(f"<b>{what}</b><br>{why}")
            text.setWordWrap(True)
            text.setTextFormat(Qt.TextFormat.RichText)
            text.setProperty("class", "Dim")
            row.addWidget(mark, alignment=Qt.AlignmentFlag.AlignTop)
            row.addWidget(text, 1)
            box.addLayout(row)

        if problems and health.running:
            live = QLabel(
                "The machine is running. A display device is not something "
                "libvirt can swap under a guest, so this applies on its next "
                "start."
            )
            live.setWordWrap(True)
            live.setObjectName("ConsoleHint")
            box.addWidget(live)

        self.resize_guest = QCheckBox(
            "Resize the guest's resolution to match the console window"
        )
        self.resize_guest.setToolTip(
            "Needs an accelerated display device and, on SPICE, the agent - "
            "which is what the rest of this dialog is about."
        )
        try:
            from ..pages.settings import console_resize_guest

            self.resize_guest.setChecked(console_resize_guest())
        except Exception:  # noqa: BLE001 - preferences are optional
            pass
        box.addWidget(self.resize_guest)

        box.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for key, what, _why in problems:
            btn = QPushButton(self.LABELS.get(key, what))
            btn.setProperty("class", "GhostButton")
            btn.clicked.connect(
                lambda _=False, k=key: (self.actions.append(k), self.accept())
            )
            buttons.addWidget(btn)
        buttons.addStretch(1)
        if len(problems) > 1:
            every = QPushButton("Fix all of it")
            every.setProperty("class", "PrimaryButton")
            every.clicked.connect(
                lambda: (self.actions.extend(k for k, _w, _y in problems),
                         self.accept())
            )
            buttons.addWidget(every)
        close = QPushButton("Close")
        close.setProperty("class", "GhostButton")
        close.clicked.connect(self.accept)  # the resize tick box is still a result
        buttons.addWidget(close)
        box.addLayout(buttons)

    LABELS = {
        "video": "Change the display device",
        "agent": "Add the agent channel",
        "tablet": "Add a tablet",
    }


class VirtioIsoDialog(SizedDialog):
    """Say where the virtio-win disc is, and offer to remember it.

    Windows ships no virtio driver, so a machine given virtio disks boots to an
    installer that cannot see them until this disc is mounted and the drivers
    are loaded off it. The same disc serves every Windows guest and downloading
    it is 700 MB a time, so a path picked here comes back already filled in for
    the next machine - which is what the tick box is for.
    """

    def __init__(self, parent, saved: str = "", found=(), pools=(),
                 remote: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("virtio-win driver disc")
        self.setMinimumWidth(560)
        self._pools = pools
        self.download = False  # set when the user asks for a fresh copy instead
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Add the virtio-win driver disc"))
        note = QLabel(
            "Attaches the disc as a second optical drive. In Windows, load the "
            "storage driver from it during setup, or run "
            "<b>virtio-win-guest-tools.exe</b> off it on an installed system to "
            "get every driver and both agents in one pass."
        )
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setProperty("class", "Dim")
        box.addWidget(note)

        box.addWidget(_field_label("disc image"))
        # Editable, so a path can be typed or pasted, with everything already
        # known about listed under the arrow: what was remembered first, then
        # whatever is lying around on this host.
        self.path = QComboBox()
        self.path.setEditable(True)
        for candidate in [saved, *found]:
            if candidate and self.path.findText(candidate) < 0:
                self.path.addItem(candidate)
        self.path.setCurrentText(saved or (found[0] if found else ""))
        self.path.lineEdit().setPlaceholderText(
            "/usr/share/virtio-win/virtio-win.iso"
        )
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        pool_browse = QPushButton("From pool…")
        pool_browse.setProperty("class", "GhostButton")
        pool_browse.clicked.connect(self._pick_volume)
        row.addWidget(pool_browse)
        if not remote:
            browse = QPushButton("Browse…")
            browse.setProperty("class", "GhostButton")
            browse.clicked.connect(self._pick)
            row.addWidget(browse)
        box.addLayout(row)

        if found:
            hint = QLabel(f"Found on this host: {found[0]}")
            hint.setWordWrap(True)
            hint.setObjectName("ConsoleHint")
            box.addWidget(hint)
        elif remote:
            hint = QLabel(
                "This is a remote connection, so the path has to be one the "
                "host can read - pick it out of a storage pool."
            )
            hint.setWordWrap(True)
            hint.setObjectName("ConsoleHint")
            box.addWidget(hint)

        self.remember = QCheckBox("Remember this disc for the next machine")
        self.remember.setChecked(True)
        box.addWidget(self.remember)

        box.addSpacing(6)
        buttons = _buttons(self, "Attach disc")
        get_one = QPushButton("Download the latest…")
        get_one.setProperty("class", "GhostButton")
        get_one.setToolTip(
            "Fetches the disc from the virtio-win project and imports it into "
            "the default pool. About 700 MB."
        )
        get_one.clicked.connect(
            lambda: (setattr(self, "download", True), self.accept())
        )
        buttons.insertWidget(0, get_one)
        box.addLayout(buttons)

        self.path.currentTextChanged.connect(
            lambda text: self._ok_button.setEnabled(bool(text.strip()))
        )
        self._ok_button.setEnabled(bool(self.path.currentText().strip()))

    def chosen_path(self) -> str:
        return self.path.currentText().strip()

    def _pick(self) -> None:
        start = self.chosen_path() or "/usr/share/virtio-win"
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the virtio-win disc", start,
            "Disc images (*.iso);;All files (*)",
        )
        if path:
            self.path.setCurrentText(path)

    def _pick_volume(self) -> None:
        picker = VolumePickerDialog(self, self._pools)
        if picker.exec() == QDialog.DialogCode.Accepted and picker.selected_path():
            self.path.setCurrentText(picker.selected_path())


class ChoiceDialog(SizedDialog):
    """One labelled combo box, for the many "pick a value" edits."""

    def __init__(self, parent, title: str, label: str, options: list[str],
                 current: str = "", note: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(title))
        if note:
            hint = QLabel(note)
            hint.setWordWrap(True)
            hint.setProperty("class", "Dim")
            box.addWidget(hint)
        box.addWidget(_field_label(label))
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.addItems(options)
        if current:
            self.combo.setCurrentText(current)
        box.addWidget(self.combo)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Apply"))

    def value(self) -> str:
        return self.combo.currentText().strip()


class LabelsDialog(SizedDialog):
    """A machine's human title and free-form notes."""

    def __init__(self, parent, title: str, description: str) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QPlainTextEdit

        self.setWindowTitle("Name and notes")
        self.setMinimumWidth(460)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Name and notes"))
        note = QLabel(
            "The title is a friendly label shown alongside the machine name; "
            "notes are for anything you want to remember about it."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        box.addWidget(_field_label("title"))
        self.title_edit = QLineEdit(title)
        self.title_edit.setPlaceholderText("Build server")
        box.addWidget(self.title_edit)
        box.addWidget(_field_label("notes"))
        self.notes = QPlainTextEdit(description)
        self.notes.setMinimumHeight(110)
        box.addWidget(self.notes)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Save"))


class NicEditDialog(SizedDialog):
    """Edit an existing interface: MAC, model, link state."""

    def __init__(self, parent, nic, networks: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit network interface")
        self.setMinimumWidth(440)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Edit network interface"))
        note = QLabel(
            "The link can be pulled up or down while the machine runs - handy "
            "for testing how software copes. MAC and model changes need a "
            "restart."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        box.addWidget(_field_label("mac address"))
        self.mac = QLineEdit(nic.mac)
        box.addWidget(self.mac)
        box.addWidget(_field_label("model"))
        self.model = QComboBox()
        self.model.addItems(["virtio", "e1000e", "e1000", "rtl8139"])
        self.model.setCurrentText(nic.model)
        box.addWidget(self.model)
        self.link_up = QCheckBox("Link connected")
        self.link_up.setChecked(True)
        box.addWidget(self.link_up)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Apply"))


class HostdevOptionsDialog(SizedDialog):
    """PCI ROM BAR and USB startup policy for an assigned host device."""

    def __init__(self, parent, dev) -> None:
        super().__init__(parent)
        self.setWindowTitle("Host device options")
        self.setMinimumWidth(440)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"{dev.kind.upper()} device {dev.ident}"))
        self.rombar = QCheckBox("Expose the device's option ROM (ROM BAR)")
        self.rombar.setChecked(True)
        self.policy = QComboBox()
        self.policy.addItems(["mandatory", "requisite", "optional"])
        if dev.kind == "pci":
            hint = QLabel(
                "Turn the ROM off when a passed-through GPU's video BIOS stops "
                "the guest from booting."
            )
            hint.setWordWrap(True)
            hint.setProperty("class", "Dim")
            box.addWidget(hint)
            box.addWidget(self.rombar)
        else:
            hint = QLabel(
                "What happens when the device is missing at startup: mandatory "
                "refuses to start, optional carries on without it."
            )
            hint.setWordWrap(True)
            hint.setProperty("class", "Dim")
            box.addWidget(hint)
            box.addWidget(_field_label("startup policy"))
            box.addWidget(self.policy)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Apply"))


class TuningDialog(SizedDialog):
    """CPU pinning, hugepage backing, iothreads and per-disk throttling.

    Everything here is about contention. The defaults let the host schedule a
    guest's vCPUs anywhere, which is right until the guest is doing something
    latency-sensitive, at which point it is the reason for the stutter.
    """

    def __init__(self, parent, vm_name: str, vcpus: int, topology, tuning,
                 disks, guest_topology=None) -> None:
        # topology: HostTopology, tuning: Tuning, disks: tuple[DiskInfo, ...],
        # guest_topology: the machine's current (sockets, cores, threads) or None
        super().__init__(parent)
        from PySide6.QtWidgets import QScrollArea

        from ..core.tuning import (PIN_PAIRED, PIN_PER_CORE, DiskThrottle,
                                   auto_pin, emulator_cpus, format_cpuset)

        self.setWindowTitle("Tuning")
        self.setMinimumSize(720, 640)
        self._topology = topology
        self._vcpus = vcpus
        self._auto_pin = auto_pin
        self._emulator_cpus = emulator_cpus
        self._current_topology = guest_topology

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(10)
        outer.addWidget(_title(f"Tuning for {vm_name}"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        box = QVBoxLayout(inner)
        box.setContentsMargins(0, 0, 6, 6)
        box.setSpacing(10)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # -- cpu pinning
        cores = topology.physical_cores()
        box.addWidget(_field_label("cpu pinning"))
        cpu_note = QLabel(
            f"Host has {topology.total_cpus} logical CPUs on {len(cores)} "
            f"physical cores ({topology.threads} threads each). Pinning stops "
            "the host moving a vCPU between cores mid-workload."
        )
        cpu_note.setWordWrap(True)
        cpu_note.setObjectName("ConsoleHint")
        box.addWidget(cpu_note)

        self.pin_enabled = QCheckBox("Pin each vCPU to specific host CPUs")
        self.pin_enabled.setChecked(bool(tuning.vcpu_pins))
        box.addWidget(self.pin_enabled)

        layout_row = QHBoxLayout()
        layout_row.setSpacing(8)
        layout_row.addWidget(_field_label("layout"))
        self.pin_mode = QComboBox()
        self.pin_mode.addItem(
            "pair sibling threads - leaves whole cores for the host", PIN_PAIRED
        )
        self.pin_mode.addItem(
            "one vCPU per core - fastest per thread, uses twice the cores",
            PIN_PER_CORE,
        )
        self.pin_mode.setMaximumWidth(430)
        layout_row.addWidget(self.pin_mode)
        layout_row.addStretch(1)
        box.addLayout(layout_row)

        self.pin_fields: dict[int, QLineEdit] = {}
        grid = QGridLayout()
        grid.setSpacing(6)
        for vcpu in range(vcpus):
            grid.addWidget(_field_label(f"vcpu {vcpu}"), vcpu // 2, (vcpu % 2) * 2)
            field = QLineEdit(format_cpuset(tuning.vcpu_pins.get(vcpu, ())))
            field.setPlaceholderText("e.g. 2 or 2,10 or 2-3")
            # a cpuset is short; letting it take half the dialog pushed the
            # second column off the edge
            field.setMaximumWidth(190)
            self.pin_fields[vcpu] = field
            grid.addWidget(field, vcpu // 2, (vcpu % 2) * 2 + 1)
        box.addLayout(grid)

        self.emulator = QLineEdit(format_cpuset(tuning.emulator_pin))
        self.emulator.setPlaceholderText("host CPUs for the emulator, e.g. 0,8")
        self.emulator.setMaximumWidth(400)
        box.addWidget(_field_label("emulator"))
        box.addWidget(self.emulator)

        auto_row = QHBoxLayout()
        auto_btn = QPushButton("Fill in a sensible layout")
        auto_btn.setProperty("class", "GhostButton")
        auto_btn.clicked.connect(self._autofill)
        clear_btn = QPushButton("Clear")
        clear_btn.setProperty("class", "GhostButton")
        clear_btn.clicked.connect(self._clear_pins)
        auto_row.addWidget(auto_btn)
        auto_row.addWidget(clear_btn)
        auto_row.addStretch(1)
        box.addLayout(auto_row)

        box.addWidget(_field_label("guest topology"))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self.topology_mode = QComboBox()
        self.topology_mode.addItem("leave the guest as it is", "keep")
        self.topology_mode.addItem("match the pinning", "auto")
        self.topology_mode.addItem("set it myself", "manual")
        self.topology_mode.setCurrentIndex(1)
        self.topology_mode.setMaximumWidth(300)
        mode_row.addWidget(self.topology_mode)

        # shown only in manual mode
        self._manual_row = QWidget()
        manual = QHBoxLayout(self._manual_row)
        manual.setContentsMargins(0, 0, 0, 0)
        manual.setSpacing(8)
        self.sockets = QSpinBox()
        self.cores = QSpinBox()
        self.threads = QSpinBox()
        start = guest_topology or (1, max(vcpus, 1), 1)
        for spin, label, value, top in (
            (self.sockets, "sockets", start[0], 16),
            (self.cores, "cores", start[1], 256),
            (self.threads, "threads", start[2], 8),
        ):
            spin.setRange(1, top)
            spin.setValue(max(value, 1))
            spin.setMaximumWidth(90)
            spin.valueChanged.connect(lambda _v: self._refresh_topology_note())
            column = QVBoxLayout()
            column.setSpacing(2)
            column.addWidget(_field_label(label))
            column.addWidget(spin)
            manual.addLayout(column)
        manual.addStretch(1)
        mode_row.addWidget(self._manual_row)
        mode_row.addStretch(1)
        box.addLayout(mode_row)

        self.topology_note = QLabel("")
        self.topology_note.setWordWrap(True)
        self.topology_note.setObjectName("ConsoleHint")
        box.addWidget(self.topology_note)
        self.topology_mode.currentIndexChanged.connect(
            lambda _i: self._refresh_topology_note()
        )
        self.pin_enabled.toggled.connect(lambda _on: self._refresh_topology_note())
        self._refresh_topology_note()

        # -- hugepages
        box.addWidget(_field_label("memory backing"))
        self.hugepages = QComboBox()
        self.hugepages.addItem("ordinary 4 KiB pages", 0)
        for pool in topology.hugepages:
            spare = f"{pool.free} free of {pool.total}" if pool.total else "none allocated"
            self.hugepages.addItem(f"hugepages, {pool.label} ({spare})", pool.size_kb)
        current = self.hugepages.findData(tuning.hugepage_size_kb)
        self.hugepages.setCurrentIndex(max(current, 0))
        box.addWidget(self.hugepages)
        unallocated = [p for p in topology.hugepages if p.total == 0]
        if unallocated:
            warn = QLabel(
                "The host has hugepage sizes available but none reserved, so a "
                "machine asking for them will not start. Reserve some first, "
                "e.g. vm.nr_hugepages for 2 MiB pages, or hugepagesz on the "
                "kernel command line for 1 GiB."
            )
            warn.setWordWrap(True)
            warn.setProperty("class", "Warn")
            box.addWidget(warn)

        # -- iothreads
        box.addWidget(_field_label("iothreads"))
        io_row = QHBoxLayout()
        self.iothreads = QSpinBox()
        self.iothreads.setRange(0, 16)
        self.iothreads.setValue(tuning.iothreads)
        io_row.addWidget(self.iothreads)
        io_note = QLabel("0 leaves disk I/O on the vCPU threads")
        io_note.setObjectName("ConsoleHint")
        io_row.addWidget(io_note)
        io_row.addStretch(1)
        box.addLayout(io_row)

        # -- per-disk throttling
        box.addWidget(_field_label("disk limits"))
        self.throttle_fields: dict[str, tuple[QSpinBox, QSpinBox, QSpinBox, QSpinBox]] = {}
        for disk in disks:
            if disk.device != "disk":
                continue
            existing = tuning.throttles.get(disk.dev, DiskThrottle())
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(disk.dev)
            label.setProperty("class", "StatVal")
            label.setFixedWidth(40)
            row.addWidget(label)
            spins = []
            for suffix, value, maximum in (
                ("read MB/s", existing.read_bps // (1024 * 1024), 10_000),
                ("write MB/s", existing.write_bps // (1024 * 1024), 10_000),
                ("read IOPS", existing.read_iops, 1_000_000),
                ("write IOPS", existing.write_iops, 1_000_000),
            ):
                col = QVBoxLayout()
                col.setSpacing(2)
                col.addWidget(_field_label(suffix))
                spin = QSpinBox()
                spin.setRange(0, maximum)
                spin.setValue(value)
                spin.setSpecialValueText("none")
                col.addWidget(spin)
                spins.append(spin)
                row.addLayout(col)
            self.throttle_fields[disk.dev] = tuple(spins)
            box.addLayout(row)
        if not self.throttle_fields:
            none_label = QLabel("no writable disks to limit")
            none_label.setObjectName("ConsoleHint")
            box.addWidget(none_label)

        outer.addLayout(_buttons(self, "Apply"))

    def _set_note(self, text: str, level: str = "hint") -> None:
        self.topology_note.setText(text)
        self.topology_note.setStyleSheet(
            f"color: {theme.WARN};" if level == "warn" else ""
        )
        self.topology_note.setObjectName(
            "" if level == "warn" else "ConsoleHint"
        )
        self.topology_note.style().unpolish(self.topology_note)
        self.topology_note.style().polish(self.topology_note)

    def _refresh_topology_note(self) -> None:
        from ..core.tuning import guest_topology_for

        mode = self.topology_mode.currentData()
        self._manual_row.setVisible(mode == "manual")
        derived = guest_topology_for(self.pins(), self._topology)

        if mode == "keep":
            current = self._current_topology
            self._set_note(
                f"Left at {current[0]}x{current[1]}x{current[2]} "
                "(sockets x cores x threads)." if current else
                "The guest keeps whatever topology it has now."
            )
            return

        if mode == "auto":
            if not self.pins():
                self._set_note(
                    "Nothing to match: without pinning the host places vCPUs "
                    "wherever it likes. Pin them, or set a topology yourself.",
                    "warn",
                )
                return
            if derived is None:
                self._set_note(
                    "This layout puts more vCPUs on some cores than others, "
                    "which cannot be expressed as sockets x cores x threads. "
                    "Pair sibling threads, or set a topology yourself.",
                    "warn",
                )
                return
            _sockets, cores, threads = derived
            self._set_note(
                f"The guest will be told it has {cores} core"
                f"{'s' if cores != 1 else ''} of {threads} thread"
                f"{'s' if threads != 1 else ''}, which is what the pinning "
                "actually gives it. Applies on next start."
            )
            return

        # manual
        product = self.sockets.value() * self.cores.value() * self.threads.value()
        if product != self._vcpus:
            self._set_note(
                f"{self.sockets.value()} x {self.cores.value()} x "
                f"{self.threads.value()} is {product} vCPUs, but this machine "
                f"has {self._vcpus}. Applying it would change the count, which "
                "belongs in the processor editor, not here.",
                "warn",
            )
            return
        if derived is not None and self.manual_topology() != derived:
            self._set_note(
                f"Allowed, but it disagrees with the pinning: that pairs vCPUs "
                f"{derived[2]} to a core, while this tells the guest "
                f"{self.threads.value()}. The guest will schedule on what you "
                "say here, not on what the pinning does.",
                "warn",
            )
            return
        self._set_note("Applies on next start.")

    def manual_topology(self) -> tuple[int, int, int]:
        return (self.sockets.value(), self.cores.value(), self.threads.value())

    def guest_topology(self) -> tuple[int, int, int] | None:
        """The topology to hand the guest, or None to leave it alone."""
        from ..core.tuning import guest_topology_for

        mode = self.topology_mode.currentData()
        if mode == "manual":
            return self.manual_topology()
        if mode == "auto":
            return guest_topology_for(self.pins(), self._topology)
        return None

    def topology_problem(self) -> str | None:
        """Why this topology cannot be applied, if it cannot."""
        if self.topology_mode.currentData() != "manual":
            return None
        sockets, cores, threads = self.manual_topology()
        product = sockets * cores * threads
        if product != self._vcpus:
            return (
                f"{sockets} sockets x {cores} cores x {threads} threads is "
                f"{product} vCPUs, but this machine has {self._vcpus}.\n\n"
                "Applying it would change the vCPU count to match, which is a "
                "bigger change than a tuning tweak. Use Edit processor to "
                "change how many vCPUs the machine has."
            )
        return None

    def _autofill(self) -> None:
        from ..core.tuning import format_cpuset

        pins = self._auto_pin(
            self._vcpus, self._topology, self.pin_mode.currentData()
        )
        for vcpu, field in self.pin_fields.items():
            field.setText(format_cpuset(pins.get(vcpu, ())))
        self.emulator.setText(
            format_cpuset(self._emulator_cpus(self._topology, pins))
        )
        self.pin_enabled.setChecked(True)
        self._refresh_topology_note()

    def _clear_pins(self) -> None:
        for field in self.pin_fields.values():
            field.clear()
        self.emulator.clear()
        self.pin_enabled.setChecked(False)
        self._refresh_topology_note()

    # -- results

    def pins(self) -> dict[int, tuple[int, ...]]:
        from ..core.tuning import _parse_cpuset

        if not self.pin_enabled.isChecked():
            return {}
        out = {}
        for vcpu, field in self.pin_fields.items():
            cpus = _parse_cpuset(field.text())
            if cpus:
                out[vcpu] = cpus
        return out

    def emulator_pin(self) -> tuple[int, ...]:
        from ..core.tuning import _parse_cpuset

        return _parse_cpuset(self.emulator.text()) if self.pin_enabled.isChecked() else ()

    def hugepage_size_kb(self) -> int:
        return int(self.hugepages.currentData() or 0)

    def iothread_count(self) -> int:
        return self.iothreads.value()

    def throttles(self) -> dict:
        from ..core.tuning import DiskThrottle

        out = {}
        for dev, (read_mb, write_mb, read_iops, write_iops) in self.throttle_fields.items():
            out[dev] = DiskThrottle(
                read_bps=read_mb.value() * 1024 * 1024,
                write_bps=write_mb.value() * 1024 * 1024,
                read_iops=read_iops.value(),
                write_iops=write_iops.value(),
            )
        return out

    def invalid_cpus(self) -> list[int]:
        """CPU ids the host does not have, so we can refuse before applying."""
        valid = {cpu.id for cpu in self._topology.cpus}
        asked = set()
        for cpus in self.pins().values():
            asked |= set(cpus)
        asked |= set(self.emulator_pin())
        return sorted(asked - valid)


class GuestFeaturesDialog(SizedDialog):
    """Hyper-V enlightenments, hiding, CPU flags, Looking Glass, evdev.

    The settings people copy out of forum posts into raw XML. What the host
    supports is passed in rather than assumed, so a feature this QEMU has never
    heard of is not offered.
    """

    def __init__(self, parent, vm_name: str, features, support, evdev_devices,
                 machine: str = "q35") -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QScrollArea

        from ..core.features import (CPU_FLAG_NOTES, CPU_POLICIES, HYPERV_NOTES,
                                     looking_glass_hint, shmem_for_resolution)

        self.setWindowTitle("Guest features")
        self.setMinimumSize(720, 700)
        self._support = support
        self._machine = machine
        self._shmem_for_resolution = shmem_for_resolution
        self._looking_glass_hint = looking_glass_hint

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(10)
        outer.addWidget(_title(f"Guest features for {vm_name}"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        box = QVBoxLayout(inner)
        box.setContentsMargins(0, 0, 6, 6)
        box.setSpacing(10)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # -- hyper-V
        box.addWidget(_field_label(
            f"hyper-v enlightenments ({len(support.hyperv)} supported here)"
        ))
        hv_note = QLabel(
            "Tell Windows it is a guest so it stops working around problems it "
            "does not have. Worth having on any Windows machine."
        )
        hv_note.setWordWrap(True)
        hv_note.setObjectName("ConsoleHint")
        box.addWidget(hv_note)

        self.hyperv: dict[str, QCheckBox] = {}
        hv_grid = QGridLayout()
        hv_grid.setSpacing(4)
        for index, name in enumerate(support.hyperv):
            note = HYPERV_NOTES.get(name, "")
            check = QCheckBox(name)
            check.setChecked(features.hyperv.get(name, False))
            if note:
                check.setToolTip(note)
            self.hyperv[name] = check
            hv_grid.addWidget(check, index // 3, index % 3)
        box.addLayout(hv_grid)

        hv_row = QHBoxLayout()
        hv_row.setSpacing(10)
        all_btn = QPushButton("Recommended set")
        all_btn.setProperty("class", "GhostButton")
        all_btn.clicked.connect(self._recommended)
        none_btn = QPushButton("None")
        none_btn.setProperty("class", "GhostButton")
        none_btn.clicked.connect(
            lambda: [c.setChecked(False) for c in self.hyperv.values()]
        )
        hv_row.addWidget(all_btn)
        hv_row.addWidget(none_btn)
        hv_row.addStretch(1)
        box.addLayout(hv_row)

        detail = QHBoxLayout()
        detail.setSpacing(10)
        vid_col = QVBoxLayout()
        vid_col.setSpacing(2)
        vid_col.addWidget(_field_label("vendor id (max 12)"))
        self.vendor_id = QLineEdit(features.vendor_id or "AuthenticAMD")
        self.vendor_id.setMaxLength(12)
        self.vendor_id.setMaximumWidth(180)
        vid_col.addWidget(self.vendor_id)
        spin_col = QVBoxLayout()
        spin_col.setSpacing(2)
        spin_col.addWidget(_field_label("spinlock retries"))
        self.spinlocks = QSpinBox()
        self.spinlocks.setRange(4095, 1_000_000)
        self.spinlocks.setValue(max(features.spinlocks, 8191))
        self.spinlocks.setMaximumWidth(160)
        spin_col.addWidget(self.spinlocks)
        detail.addLayout(vid_col)
        detail.addLayout(spin_col)
        detail.addStretch(1)
        box.addLayout(detail)

        # -- hiding
        box.addWidget(_field_label("hiding the hypervisor"))
        self.kvm_hidden = QCheckBox(
            "Hide the KVM signature - the fix for NVIDIA's Code 43 on "
            "passthrough"
        )
        self.kvm_hidden.setChecked(features.kvm_hidden)
        box.addWidget(self.kvm_hidden)
        self.vmport = QCheckBox(
            "Leave the VMware port enabled (turn off for passthrough guests)"
        )
        self.vmport.setChecked(features.vmport)
        box.addWidget(self.vmport)

        # -- cpu flags
        box.addWidget(_field_label("cpu flags"))
        self.cpu_table = QTableWidget(0, 2)
        self.cpu_table.setHorizontalHeaderLabels(["flag", "policy"])
        self.cpu_table.horizontalHeader().setStretchLastSection(True)
        self.cpu_table.verticalHeader().hide()
        self.cpu_table.setMaximumHeight(150)
        for name, policy in sorted(features.cpu_features.items()):
            self._add_flag(name, policy)
        box.addWidget(self.cpu_table)
        flag_row = QHBoxLayout()
        flag_row.setSpacing(8)
        self.new_flag = QComboBox()
        self.new_flag.setEditable(True)
        self.new_flag.addItems(sorted(CPU_FLAG_NOTES))
        self.new_flag.setMaximumWidth(200)
        for index, name in enumerate(sorted(CPU_FLAG_NOTES)):
            self.new_flag.setItemData(index, CPU_FLAG_NOTES[name], Qt.ItemDataRole.ToolTipRole)
        self.new_policy = QComboBox()
        self.new_policy.addItems(CPU_POLICIES)
        self.new_policy.setMaximumWidth(140)
        add_flag = QPushButton("Add")
        add_flag.setProperty("class", "GhostButton")
        add_flag.clicked.connect(
            lambda: self._add_flag(self.new_flag.currentText().strip(),
                                   self.new_policy.currentText())
        )
        drop_flag = QPushButton("Remove selected")
        drop_flag.setProperty("class", "GhostButton")
        drop_flag.clicked.connect(self._drop_flag)
        flag_row.addWidget(self.new_flag)
        flag_row.addWidget(self.new_policy)
        flag_row.addWidget(add_flag)
        flag_row.addWidget(drop_flag)
        flag_row.addStretch(1)
        box.addLayout(flag_row)

        # -- looking glass
        box.addWidget(_field_label("looking glass"))
        lg_row = QHBoxLayout()
        lg_row.setSpacing(8)
        self.shmem = QComboBox()
        self.shmem.addItem("off", 0)
        for size in (32, 64, 128, 256):
            fits = "1080p" if size == 32 else ("1440p" if size == 64 else "2160p")
            self.shmem.addItem(f"{size} MiB - up to {fits}", size)
        current = self.shmem.findData(features.shmem_mb)
        self.shmem.setCurrentIndex(max(current, 0))
        self.shmem.setMaximumWidth(240)
        self.shmem.currentIndexChanged.connect(lambda _i: self._refresh_lg_note())
        lg_row.addWidget(self.shmem)
        lg_row.addStretch(1)
        box.addLayout(lg_row)
        self.lg_note = QLabel("")
        self.lg_note.setWordWrap(True)
        self.lg_note.setObjectName("ConsoleHint")
        box.addWidget(self.lg_note)

        # -- evdev
        box.addWidget(_field_label("input passthrough (evdev)"))
        ev_note = QLabel(
            "Shares a keyboard or mouse with the guest without handing over the "
            "whole USB device. The first one selected carries the release "
            "hotkey: both control keys together."
        )
        ev_note.setWordWrap(True)
        ev_note.setObjectName("ConsoleHint")
        box.addWidget(ev_note)
        self.evdev = QListWidget()
        self.evdev.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.evdev.setMaximumHeight(130)
        for path, label in evdev_devices:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.evdev.addItem(item)
            if path in features.evdev:
                item.setSelected(True)
        box.addWidget(self.evdev)
        if not evdev_devices:
            missing = QLabel("no input devices found under /dev/input/by-id")
            missing.setObjectName("ConsoleHint")
            box.addWidget(missing)

        # -- secure boot
        box.addWidget(_field_label("secure boot"))
        self.secure_boot = QCheckBox("Boot with secure boot enabled")
        self.secure_boot.setChecked(features.secure_boot)
        self.secure_boot.setEnabled(support.secure_boot)
        box.addWidget(self.secure_boot)
        sb_note = QLabel(
            f"Uses {support.secure_loader} and turns on SMM. Windows 11 asks "
            "for it. Changing the firmware resets the machine's NVRAM, so its "
            "boot entries go with it."
            if support.secure_boot else
            "This host has no secure-boot firmware installed, so it cannot be "
            "turned on. Install an OVMF build with secboot in its name."
        )
        sb_note.setWordWrap(True)
        sb_note.setObjectName("ConsoleHint")
        box.addWidget(sb_note)

        self._refresh_lg_note()
        outer.addLayout(_buttons(self, "Apply"))

    # -- helpers

    def _recommended(self) -> None:
        """What a Windows guest wants, minus the ones with side effects."""
        wanted = {
            "relaxed", "vapic", "spinlocks", "vpindex", "runtime", "synic",
            "stimer", "frequencies", "tlbflush", "ipi", "reenlightenment",
        }
        for name, check in self.hyperv.items():
            check.setChecked(name in wanted)

    def _add_flag(self, name: str, policy: str) -> None:
        if not name:
            return
        for row in range(self.cpu_table.rowCount()):
            if self.cpu_table.item(row, 0).text() == name:
                self.cpu_table.item(row, 1).setText(policy)
                return
        row = self.cpu_table.rowCount()
        self.cpu_table.insertRow(row)
        self.cpu_table.setItem(row, 0, QTableWidgetItem(name))
        self.cpu_table.setItem(row, 1, QTableWidgetItem(policy))

    def _drop_flag(self) -> None:
        for item in self.cpu_table.selectedItems():
            self.cpu_table.removeRow(item.row())
            return

    def _refresh_lg_note(self) -> None:
        self.lg_note.setText(self._looking_glass_hint(self.shmem.currentData() or 0))

    # -- results

    def result_features(self):
        from ..core.features import GuestFeatures

        return GuestFeatures(
            hyperv={name: check.isChecked() for name, check in self.hyperv.items()},
            vendor_id=self.vendor_id.text().strip(),
            spinlocks=self.spinlocks.value(),
            kvm_hidden=self.kvm_hidden.isChecked(),
            vmport=self.vmport.isChecked(),
            cpu_features={
                self.cpu_table.item(r, 0).text(): self.cpu_table.item(r, 1).text()
                for r in range(self.cpu_table.rowCount())
            },
            shmem_mb=int(self.shmem.currentData() or 0),
            evdev=tuple(
                item.data(Qt.ItemDataRole.UserRole)
                for item in self.evdev.selectedItems()
            ),
            secure_boot=self.secure_boot.isChecked(),
        )

    def problem(self) -> str | None:
        """Why this cannot be applied, if it cannot."""
        chosen = self.result_features()
        # Verified against libvirt 12.6 rather than assumed: stimer really
        # does require synic, but synic on its own is fine - an earlier version
        # of this also demanded vpindex, which would have blocked a valid setup.
        if chosen.hyperv.get("stimer") and not chosen.hyperv.get("synic"):
            return "stimer needs synic; libvirt refuses the pair otherwise."
        if chosen.hyperv.get("vendor_id") and not chosen.vendor_id:
            return (
                "vendor_id is on but has no value, so it would be dropped "
                "silently. Give it one, or turn it off."
            )
        if chosen.secure_boot and self._machine and "q35" not in self._machine:
            return (
                f"Secure boot needs a q35 machine; this one is {self._machine}. "
                "Change the machine type first, in the processor editor."
            )
        return None
