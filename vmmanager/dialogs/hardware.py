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



class MdevDialog(SizedDialog):
    """Mediated devices: the types this host offers, the instances that
    exist, and which one to hand to the machine.

    Selection is an instance; the type list is where new ones are created.
    """

    def __init__(self, parent, types, mdevs) -> None:
        # types: list[MdevType], mdevs: list[MdevInfo]
        super().__init__(parent)
        self.setWindowTitle("Mediated devices")
        self.setMinimumSize(680, 480)
        self.create_requested = None  # set by the caller; (parent, type_id)
        self.delete_requested = None  # set by the caller; (uuid)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Mediated devices"))
        if not types and not mdevs:
            note = QLabel(
                "This host advertises no mediated-device types. They come "
                "from the graphics driver - NVIDIA vGPU (the enterprise "
                "driver), Intel GVT-g - and appear under /sys/class/mdev_bus "
                "once that driver is set up."
            )
            note.setWordWrap(True)
            note.setProperty("class", "Dim")
            box.addWidget(note)
        else:
            note = QLabel(
                "An instance is assigned like a PCI device. Instances made "
                "here are transient - gone after a host reboot; mdevctl can "
                "persist them."
            )
            note.setWordWrap(True)
            note.setProperty("class", "Dim")
            box.addWidget(note)

        box.addWidget(_field_label("types this host offers"))
        self.type_list = QListWidget()
        self.type_list.setMaximumHeight(140)
        box.addWidget(self.type_list)
        create_btn = QPushButton("Create instance of selected type")
        create_btn.setProperty("class", "GhostButton")
        create_btn.clicked.connect(self._create)
        box.addWidget(create_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        box.addWidget(_field_label("instances"))
        self.mdev_list = QListWidget()
        box.addWidget(self.mdev_list, 1)
        delete_btn = QPushButton("Delete selected instance")
        delete_btn.setProperty("class", "GhostButton")
        delete_btn.clicked.connect(self._delete)
        box.addWidget(delete_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        self.status = QLabel("")
        self.status.setObjectName("ConsoleHint")
        self.status.setWordWrap(True)
        box.addWidget(self.status)

        box.addSpacing(6)
        box.addLayout(_buttons(self, "Attach to machine"))
        self.populate(types, mdevs)

    def populate(self, types, mdevs) -> None:
        self.type_list.clear()
        for t in types:
            item = QListWidgetItem(
                f"{t.type_id} - {t.name}  ({t.available} available, "
                f"on {t.parent})"
            )
            item.setData(Qt.ItemDataRole.UserRole, t)
            self.type_list.addItem(item)
        self.mdev_list.clear()
        for m in mdevs:
            used = f" - assigned to {m.attached_to}" if m.attached_to else ""
            item = QListWidgetItem(f"{m.uuid}  ({m.type_id} on {m.parent}){used}")
            item.setData(Qt.ItemDataRole.UserRole, m)
            self.mdev_list.addItem(item)

    def _create(self) -> None:
        item = self.type_list.currentItem()
        if item is None or self.create_requested is None:
            return
        t = item.data(Qt.ItemDataRole.UserRole)
        self.create_requested(t.parent, t.type_id)

    def _delete(self) -> None:
        item = self.mdev_list.currentItem()
        if item is None or self.delete_requested is None:
            return
        m = item.data(Qt.ItemDataRole.UserRole)
        self.delete_requested(m.uuid)

    def chosen(self):
        item = self.mdev_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


class GrowDiskDialog(SizedDialog):
    """Make a machine's disk bigger. Growing only - see svc_grow_disk."""

    def __init__(self, parent, dev: str, current_gb: float) -> None:
        super().__init__(parent)
        self.setWindowTitle("Grow disk")
        self.setMinimumWidth(460)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Grow {dev}"))
        note = QLabel(
            f"Currently {current_gb:.1f} GB. The disk is made bigger and a "
            "running machine is told about it straight away - but the guest "
            "still has to extend the partition and the filesystem on it "
            "before the space is usable. On Windows that is Disk Management; "
            "on Linux, growpart and resize2fs or xfs_growfs.\n\n"
            "Only growing is offered here. Shrinking a disk throws away "
            "whatever was past the new end, and the filesystem inside finds "
            "out afterwards."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        box.addWidget(_field_label("new size (GB)"))
        self.size = QDoubleSpinBox()
        self.size.setRange(max(current_gb + 0.1, 0.2), 65536)
        self.size.setDecimals(1)
        self.size.setValue(max(current_gb + 10, current_gb + 0.1))
        box.addWidget(self.size)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Grow"))


class MoveDiskDialog(SizedDialog):
    """Pick the pool a disk's storage should move to."""

    def __init__(self, parent, dev: str, pools, current_source: str,
                 running: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("Move disk")
        self.setMinimumWidth(440)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Move {dev} to another pool"))
        note = QLabel(
            ("The disk is mirrored onto the new volume while the machine "
             "runs, then switched over - no downtime."
             if running else
             "The volume is cloned into the chosen pool and the machine "
             "pointed at the copy.")
            + " A linked clone's disk is flattened by the move."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        box.addWidget(_field_label("destination pool"))
        self.pool = QComboBox()
        for p in pools:
            if p.active and not (p.path and current_source.startswith(p.path + "/")):
                self.pool.addItem(p.name)
        box.addWidget(self.pool)
        self.delete_source = QCheckBox("Delete the old volume once moved")
        box.addWidget(self.delete_source)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Move"))


def _tuning_cpu_limits(dialog) -> tuple[int, int]:
    """(shares, cap %) as the tuning dialog has them, 0 for off."""
    shares = dialog.cpu_shares.value() if dialog.cpu_shares_on.isChecked() else 0
    cap = dialog.cpu_cap.value() if dialog.cpu_cap_on.isChecked() else 0
    return shares, cap


class PassthroughDialog(SizedDialog):
    """IOMMU groups with a verdict per device - why passthrough will or won't
    work, which is the part everyone gets stuck on."""

    @property
    def COLORS(self) -> dict[str, str]:
        """Read at use, so a theme change is reflected next time it opens."""
        return {"ready": theme.OK, "caution": theme.WARN,
                "blocked": theme.DANGER}

    def __init__(self, parent, report, persisted: list[str] | None = None,
                 iommu_hint: str = "") -> None:  # report: IommuReport
        super().__init__(parent)
        persisted = persisted or []
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
                "AMD-Vi in firmware, then put this on the kernel command "
                "line:\n\n" + (iommu_hint or
                               "intel_iommu=on (or amd_iommu=on) iommu=pt")
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
                label = dev.label + (f"  ·  {dev.sriov}" if dev.sriov else "")
                item = QTreeWidgetItem([label, dev.address, dev.driver, status])
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
        self._tree = tree

        # The diagnostics used to stop at the verdict, which left the fix -
        # binding, and making it survive a reboot - as an exercise.
        self.status = QLabel("")
        self.status.setObjectName("ConsoleHint")
        self.status.setProperty("class", "Accent")
        self.status.setWordWrap(True)
        box.addWidget(self.status)

        # set by the caller; each takes the selected IommuDevice
        self.bind_requested = None
        self.restore_requested = None
        self.persist_requested = None
        self.unpersist_requested = None

        row = QHBoxLayout()
        row.setSpacing(8)
        self._bind_btn = QPushButton("Bind to vfio-pci")
        self._bind_btn.setProperty("class", "GhostButton")
        self._bind_btn.setToolTip(
            "Takes the whole card - every function of it - off the host "
            "driver now. Nothing on the host may be using it."
        )
        self._bind_btn.clicked.connect(
            lambda: self._act(self.bind_requested)
        )
        row.addWidget(self._bind_btn)
        restore = QPushButton("Give back to host")
        restore.setProperty("class", "GhostButton")
        restore.clicked.connect(lambda: self._act(self.restore_requested))
        row.addWidget(restore)
        persist = QPushButton("Bind at boot…")
        persist.setProperty("class", "GhostButton")
        persist.setToolTip(
            "Claims the card for vfio-pci before the host's driver can, "
            "which is the only thing that works for most GPUs. Writes a "
            "modprobe.d file and rebuilds the initramfs."
        )
        persist.clicked.connect(lambda: self._act(self.persist_requested))
        row.addWidget(persist)
        self._unpersist = QPushButton("Stop binding at boot")
        self._unpersist.setProperty("class", "GhostButton")
        self._unpersist.clicked.connect(
            lambda: self._act(self.unpersist_requested)
        )
        self._unpersist.setVisible(bool(persisted))
        row.addWidget(self._unpersist)
        row.addStretch(1)
        close = QPushButton("Close")
        close.setProperty("class", "GhostButton")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        box.addLayout(row)
        if persisted:
            self.status.setText(
                "bound at boot already: " + ", ".join(persisted)
            )
        elif not report.enabled:
            self.status.setText(iommu_hint)

    def selected_device(self):
        items = self._tree.selectedItems()
        data = items[0].data(0, Qt.ItemDataRole.UserRole) if items else None
        return data[0] if data else None

    def _act(self, slot) -> None:
        dev = self.selected_device()
        if dev is None:
            self.status.setText("select a device first")
            return
        if slot is not None:
            slot(dev)


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

        # -- how much host CPU it may take
        box.addWidget(_field_label("cpu limits"))
        limits_note = QLabel(
            "Pinning decides <i>which</i> cores this machine runs on; these "
            "decide how much it may take when something else wants the same "
            "ones. Weight only matters while the host is contended - a "
            "machine at 2048 gets twice the CPU of one at the default 1024, "
            "and nothing is given up while the host is idle. A ceiling is "
            "enforced either way, so it slows the guest down even on an idle "
            "host; leave it off unless something must be held back."
        )
        limits_note.setWordWrap(True)
        limits_note.setTextFormat(Qt.TextFormat.RichText)
        limits_note.setProperty("class", "Dim")
        box.addWidget(limits_note)
        limits_row = QHBoxLayout()
        limits_row.setSpacing(10)
        self.cpu_shares_on = QCheckBox("Weight")
        self.cpu_shares_on.setChecked(bool(tuning.cpu_shares))
        limits_row.addWidget(self.cpu_shares_on)
        self.cpu_shares = QSpinBox()
        self.cpu_shares.setRange(2, 262144)
        self.cpu_shares.setSingleStep(256)
        self.cpu_shares.setValue(tuning.cpu_shares or 1024)
        self.cpu_shares.setToolTip("1024 is what everything else starts with")
        limits_row.addWidget(self.cpu_shares)
        limits_row.addSpacing(12)
        self.cpu_cap_on = QCheckBox("Ceiling, % of one vCPU")
        self.cpu_cap_on.setChecked(bool(tuning.cpu_cap_pct))
        limits_row.addWidget(self.cpu_cap_on)
        self.cpu_cap = QSpinBox()
        self.cpu_cap.setRange(1, 100)
        self.cpu_cap.setSuffix("%")
        self.cpu_cap.setValue(tuning.cpu_cap_pct or 50)
        limits_row.addWidget(self.cpu_cap)
        limits_row.addStretch(1)
        box.addLayout(limits_row)
        self.cpu_shares_on.toggled.connect(self.cpu_shares.setEnabled)
        self.cpu_cap_on.toggled.connect(self.cpu_cap.setEnabled)
        self.cpu_shares.setEnabled(self.cpu_shares_on.isChecked())
        self.cpu_cap.setEnabled(self.cpu_cap_on.isChecked())

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


class SingleGpuDialog(SizedDialog):
    """Set up the hooks that hand the host's only graphics card over.

    The work is a page of shell run as root at the moment the screen goes
    dark, which is a terrible place to debug. So it is shown here in full
    before anything is written, and the scripts stay editable afterwards.
    """

    def __init__(self, parent, vm_name: str, gpus, plan, state,
                 governor_available: bool, pinned: bool) -> None:
        # gpus: list[IommuDevice] (display class), plan: GpuHandoff,
        # state: HookState
        super().__init__(parent)
        from PySide6.QtWidgets import QPlainTextEdit

        self.setWindowTitle("Single-GPU passthrough")
        self.setMinimumSize(760, 620)
        self.install_requested = None  # (address, isolate, governor)
        self.remove_requested = None
        self.preview_requested = None  # (address, isolate, governor)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Single-GPU passthrough for {vm_name}"))
        note = QLabel(
            "With one graphics card, the host has to let go of it before the "
            "guest can have it: stop the desktop, take the consoles and the "
            "boot framebuffer off the card, unload the driver - then all of "
            "it backwards when the machine stops. These are libvirt hooks, so "
            "they run whether or not this app is open."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)

        box.addWidget(_field_label("graphics card to hand over"))
        self.gpu = QComboBox()
        for dev in gpus:
            self.gpu.addItem(f"{dev.label}  ({dev.address})", dev.address)
        if plan is not None and plan.addresses:
            index = self.gpu.findData(plan.addresses[0])
            if index >= 0:
                self.gpu.setCurrentIndex(index)
        self.gpu.currentIndexChanged.connect(self._refresh)
        box.addWidget(self.gpu)

        self.isolate = QCheckBox(
            "Keep the host's own work off this machine's pinned cores while "
            "it runs"
        )
        self.isolate.setToolTip(
            "systemd's AllowedCPUs on system.slice, user.slice and "
            "init.scope. The guest's qemu lives in machine.slice and is left "
            "alone. Undone when the machine stops."
        )
        self.isolate.setEnabled(pinned)
        self.isolate.setChecked(pinned)
        box.addWidget(self.isolate)
        if not pinned:
            pin_note = QLabel(
                "CPU isolation needs the machine pinned first - Tuning, on "
                "the hardware tab."
            )
            pin_note.setWordWrap(True)
            pin_note.setObjectName("ConsoleHint")
            box.addWidget(pin_note)

        self.governor = QCheckBox(
            "Put the host CPUs on the performance governor while it runs"
        )
        self.governor.setEnabled(governor_available)
        if not governor_available:
            self.governor.setToolTip(
                "This host exposes no scaling governor, so there is nothing "
                "to switch."
            )
        box.addWidget(self.governor)
        self.isolate.toggled.connect(self._refresh)
        self.governor.toggled.connect(self._refresh)

        box.addWidget(_field_label("what will run before the machine starts"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        from ..syntax import ShellHighlighter

        self._highlighter = ShellHighlighter(self.preview.document())
        box.addWidget(self.preview, 1)

        self.status = QLabel("")
        self.status.setObjectName("ConsoleHint")
        self.status.setProperty("class", "Accent")
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        if state is not None and state.foreign_dispatcher:
            self.status.setText(
                "You already have your own /etc/libvirt/hooks/qemu - it will "
                "be left alone, so make sure it runs the scripts in qemu.d/."
            )

        row = QHBoxLayout()
        row.setSpacing(8)
        self._remove = QPushButton("Remove hooks")
        self._remove.setProperty("class", "GhostButton")
        self._remove.clicked.connect(
            lambda: self.remove_requested and self.remove_requested()
        )
        self._remove.setVisible(bool(state and state.start_installed))
        row.addWidget(self._remove)
        row.addStretch(1)
        cancel = QPushButton("Close")
        cancel.setProperty("class", "GhostButton")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        install = QPushButton(
            "Update hooks" if (state and state.start_installed)
            else "Install hooks"
        )
        install.setProperty("class", "PrimaryButton")
        install.clicked.connect(self._install)
        row.addWidget(install)
        box.addLayout(row)

    def choices(self) -> tuple[str, bool, str]:
        return (
            self.gpu.currentData() or "",
            self.isolate.isChecked() and self.isolate.isEnabled(),
            "performance" if (
                self.governor.isChecked() and self.governor.isEnabled()
            ) else "",
        )

    def _refresh(self) -> None:
        if self.preview_requested is not None:
            self.preview_requested(*self.choices())

    def show_preview(self, text: str) -> None:
        self.preview.setPlainText(text)

    def _install(self) -> None:
        if self.install_requested is not None:
            self.install_requested(*self.choices())
