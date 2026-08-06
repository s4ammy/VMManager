"""Machine-level dialogs: delete, clone, migrate, schedules, image catalog."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from .. import theme
from .base import SizedDialog, _buttons, _field_label, _title


class DeleteVmDialog(SizedDialog):
    def __init__(self, parent, name: str, disks=None) -> None:  # disks: list[DomainDisk]
        super().__init__(parent)
        self.setWindowTitle("Delete machine")
        self.setMinimumWidth(480)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(12)
        box.addWidget(_title(f"Delete {name}?"))
        body = QLabel(
            "The machine will be shut off if running and removed from libvirt. "
            "Checked disks below are deleted too. This can't be undone."
        )
        body.setWordWrap(True)
        body.setProperty("class", "Dim")
        box.addWidget(body)
        self._disk_checks: list[tuple[QCheckBox, str]] = []
        for disk in disks or []:
            check = QCheckBox(
                f"{disk.dev} · {disk.capacity_gb:.1f} GB · {disk.path}"
            )
            check.setChecked(False)
            dependents = getattr(disk, "dependents", 0)
            if dependents:
                # A linked clone is an overlay on this file. Removing it
                # breaks every one of them, quietly, the next time they read
                # a block they do not hold themselves.
                check.setEnabled(False)
                check.setToolTip(
                    f"{dependents} other disk(s) are layered on this one"
                )
                check.setText(
                    check.text()
                    + f"  — kept: {dependents} machine(s) are built on it"
                )
            box.addWidget(check)
            self._disk_checks.append((check, disk.path))
        if not disks:
            hint = QLabel("No file-backed disks attached.")
            hint.setObjectName("ConsoleHint")
            box.addWidget(hint)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Delete machine", danger=True))

    def paths_to_delete(self) -> list[str]:
        return [path for check, path in self._disk_checks if check.isChecked()]

class CloneDialog(SizedDialog):
    def __init__(self, parent, original: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clone machine")
        self.setMinimumWidth(420)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Clone {original}"))
        note = QLabel("Copies the machine definition and all its disks.")
        note.setProperty("class", "Dim")
        box.addWidget(note)
        box.addWidget(_field_label("new name"))
        self.name = QLineEdit(f"{original}-clone")
        box.addWidget(self.name)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Clone"))
        self.name.textChanged.connect(
            lambda t: self._ok_button.setEnabled(bool(t.strip()))
        )

class MigrateDialog(SizedDialog):
    def __init__(self, parent, vm_name: str, uris: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Migrate machine")
        self.setMinimumWidth(460)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Migrate {vm_name}"))
        note = QLabel(
            "Moves the machine to another host. Live migration keeps it "
            "running; storage must be shared or identical on both ends."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        box.addWidget(_field_label("destination uri"))
        self.uri = QComboBox()
        self.uri.setEditable(True)
        self.uri.addItems(uris or ["qemu+ssh://user@host/system"])
        box.addWidget(self.uri)
        self.live = QCheckBox("Live migration (keep it running)")
        self.live.setChecked(True)
        box.addWidget(self.live)
        self.tunnelled = QCheckBox(
            "Tunnel through the libvirt connection (no extra ports to open)"
        )
        box.addWidget(self.tunnelled)
        self.temporary = QCheckBox(
            "Temporary - this host keeps the definition, so it comes back here"
        )
        box.addWidget(self.temporary)
        self.unsafe = QCheckBox(
            "Allow unsafe (skip the shared-storage and cache checks)"
        )
        box.addWidget(self.unsafe)

        box.addWidget(_field_label("tuning (0 = leave to libvirt)"))
        tune = QHBoxLayout()
        tune.setSpacing(14)
        for label, attr, maximum, suffix in (
            ("bandwidth", "bandwidth", 100000, " MiB/s"),
            ("max downtime", "downtime", 60000, " ms"),
            ("dest port", "dest_port", 65535, ""),
        ):
            col = QVBoxLayout()
            col.addWidget(_field_label(label))
            spin = QSpinBox()
            spin.setRange(0, maximum)
            spin.setSuffix(suffix)
            setattr(self, attr, spin)
            col.addWidget(spin)
            tune.addLayout(col)
        addr_col = QVBoxLayout()
        addr_col.addWidget(_field_label("dest listen address"))
        self.dest_address = QLineEdit()
        self.dest_address.setPlaceholderText("optional, e.g. 10.0.0.5")
        addr_col.addWidget(self.dest_address)
        tune.addLayout(addr_col, 1)
        box.addLayout(tune)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Migrate"))

class CatalogDialog(SizedDialog):
    """Pick a cloud image, download + verify it, import it into a pool.

    On accept, `volume_path` holds the imported image's path in the pool and
    `image` the catalog entry (for OS defaults).
    """

    def __init__(self, parent, pools) -> None:  # pools: list[PoolInfo]
        super().__init__(parent)
        self.setWindowTitle("Cloud image catalog")
        self.setMinimumSize(560, 460)
        self.volume_path: str | None = None
        self.image = None
        self._downloader = None
        from ..data.catalog import CATALOG

        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Cloud image catalog"))
        note = QLabel(
            "Downloaded once into ~/.cache/vmmanager, checksum-verified, then "
            "imported into the chosen pool. Pair with cloud-init in the wizard."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        self.list = QListWidget()
        for img in CATALOG:
            self.list.addItem(img.name)
        self.list.setCurrentRow(0)
        box.addWidget(self.list, 1)
        pool_row = QHBoxLayout()
        pool_row.addWidget(_field_label("import into pool"))
        self.pool = QComboBox()
        self.pool.addItems([p.name for p in pools if p.active] or ["default"])
        pool_row.addWidget(self.pool, 1)
        box.addLayout(pool_row)
        from PySide6.QtWidgets import QProgressBar

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        box.addWidget(self.bar)
        self.status = QLabel("")
        self.status.setObjectName("ConsoleHint")
        box.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setProperty("class", "GhostButton")
        cancel.clicked.connect(self.reject)
        self.go_btn = QPushButton("Download && import")
        self.go_btn.setProperty("class", "PrimaryButton")
        self.go_btn.clicked.connect(self._go)
        row.addWidget(cancel)
        row.addWidget(self.go_btn)
        box.addLayout(row)

    def _go(self) -> None:
        from ..data.catalog import CATALOG, ImageDownloader

        idx = self.list.currentRow()
        if idx < 0:
            return
        self.image = CATALOG[idx]
        self.go_btn.setEnabled(False)
        self.list.setEnabled(False)
        downloader = ImageDownloader(self.image)
        self._downloader = downloader
        downloader.progress.connect(self._on_progress)
        downloader.failed.connect(self._on_failed)
        downloader.finished_ok.connect(self._on_downloaded)
        downloader.start()

    def _on_progress(self, pct: int, text: str) -> None:
        if pct < 0:
            self.bar.setRange(0, 0)  # busy
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(pct)
        self.status.setText(text)

    def _on_failed(self, message: str) -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.status.setText(f"failed: {message}")
        self.go_btn.setEnabled(True)
        self.list.setEnabled(True)

    def _on_downloaded(self, path: str) -> None:
        from ..libvirt_service import svc_upload_volume_from_file
        from ..tasks import run_task

        self.bar.setRange(0, 0)
        self.status.setText("importing into pool…")
        pool = self.pool.currentText()
        name = path.rsplit("/", 1)[-1]
        if not name.endswith(".qcow2"):
            name += ".qcow2"
        run_task(
            lambda: svc_upload_volume_from_file(pool, name, path, "qcow2"),
            done=self._on_imported,
            failed=self._on_failed,
        )

    def _on_imported(self, volume_path: str) -> None:
        self.volume_path = volume_path
        self.accept()

    def reject(self) -> None:  # noqa: N802 - Qt override
        if self._downloader is not None and self._downloader.isRunning():
            self._downloader.cancel()
            self._downloader.wait(2000)
        super().reject()

class ScheduleDialog(SizedDialog):
    """Per-VM automatic snapshot schedule with retention."""

    INTERVALS = [
        ("every hour", 3600),
        ("every 6 hours", 6 * 3600),
        ("daily", 86400),
        ("weekly", 7 * 86400),
    ]

    def __init__(self, parent, vm_name: str, current) -> None:
        # current: (interval_s, keep, external) | None
        super().__init__(parent)
        self.setWindowTitle("Scheduled snapshots")
        self.setMinimumWidth(440)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Scheduled snapshots for {vm_name}"))
        note = QLabel(
            "Snapshots are taken while the app is running, named auto-…, and "
            "old ones beyond the keep count are pruned automatically."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        self.enabled = QCheckBox("Snapshot this machine on a schedule")
        self.enabled.setChecked(current is not None)
        box.addWidget(self.enabled)
        row = QHBoxLayout()
        int_col = QVBoxLayout()
        int_col.addWidget(_field_label("frequency"))
        self.interval = QComboBox()
        self.interval.addItems([label for label, _s in self.INTERVALS])
        int_col.addWidget(self.interval)
        keep_col = QVBoxLayout()
        keep_col.addWidget(_field_label("keep last"))
        self.keep = QSpinBox()
        self.keep.setRange(1, 100)
        self.keep.setValue(8)
        keep_col.addWidget(self.keep)
        row.addLayout(int_col, 1)
        row.addLayout(keep_col)
        box.addLayout(row)
        self.external = QCheckBox("External snapshots (works with UEFI, running or not)")
        self.external.setChecked(True)
        box.addWidget(self.external)
        if current is not None:
            interval_s, keep, external = current
            idx = next(
                (i for i, (_l, s) in enumerate(self.INTERVALS) if s == interval_s), 0
            )
            self.interval.setCurrentIndex(idx)
            self.keep.setValue(keep)
            self.external.setChecked(external)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Save schedule"))

    def interval_seconds(self) -> int:
        return self.INTERVALS[self.interval.currentIndex()][1]

class WakeScheduleDialog(SizedDialog):
    """Start/stop a machine at fixed times of day."""

    def __init__(self, parent, vm_name: str, current) -> None:
        # current: (start_hm, stop_hm, days) | None
        super().__init__(parent)
        self.setWindowTitle("Power schedule")
        self.setMinimumWidth(440)
        from PySide6.QtCore import QTime
        from PySide6.QtWidgets import QTimeEdit

        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Power schedule for {vm_name}"))
        note = QLabel("Fires while the app (or its tray icon) is running.")
        note.setObjectName("ConsoleHint")
        box.addWidget(note)
        start_hm, stop_hm, days = current or ("", "", "all")
        row1 = QHBoxLayout()
        self.start_enabled = QCheckBox("Start at")
        self.start_enabled.setChecked(bool(start_hm))
        self.start_time = QTimeEdit(QTime.fromString(start_hm or "09:00", "HH:mm"))
        self.start_time.setDisplayFormat("HH:mm")
        row1.addWidget(self.start_enabled)
        row1.addWidget(self.start_time)
        row1.addStretch(1)
        box.addLayout(row1)
        row2 = QHBoxLayout()
        self.stop_enabled = QCheckBox("Shut down at")
        self.stop_enabled.setChecked(bool(stop_hm))
        self.stop_time = QTimeEdit(QTime.fromString(stop_hm or "18:00", "HH:mm"))
        self.stop_time.setDisplayFormat("HH:mm")
        row2.addWidget(self.stop_enabled)
        row2.addWidget(self.stop_time)
        row2.addStretch(1)
        box.addLayout(row2)
        box.addWidget(_field_label("days"))
        self.days = QComboBox()
        self.days.addItems(["every day", "weekdays", "weekends"])
        self.days.setCurrentIndex({"all": 0, "weekdays": 1, "weekends": 2}.get(days, 0))
        box.addWidget(self.days)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Save schedule"))

    def result_schedule(self) -> tuple[str, str, str]:
        start = (
            self.start_time.time().toString("HH:mm")
            if self.start_enabled.isChecked()
            else ""
        )
        stop = (
            self.stop_time.time().toString("HH:mm")
            if self.stop_enabled.isChecked()
            else ""
        )
        days = ("all", "weekdays", "weekends")[self.days.currentIndex()]
        return start, stop, days


class ConnectionDialog(SizedDialog):
    """Build a libvirt URI: hypervisor, local or remote, with a live probe."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        from ..core.connection import HYPERVISORS

        self.setWindowTitle("Add connection")
        self.setMinimumWidth(520)
        self._hvs = list(HYPERVISORS.items())
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Add connection"))

        box.addWidget(_field_label("hypervisor"))
        self.hypervisor = QComboBox()
        for _key, spec in self._hvs:
            self.hypervisor.addItem(spec["label"])
        box.addWidget(self.hypervisor)
        self.hv_note = QLabel("")
        self.hv_note.setWordWrap(True)
        self.hv_note.setObjectName("ConsoleHint")
        box.addWidget(self.hv_note)

        self.remote = QCheckBox("Connect to a remote host")
        box.addWidget(self.remote)
        self._remote_box = QWidget()
        rbox = QVBoxLayout(self._remote_box)
        rbox.setContentsMargins(0, 0, 0, 0)
        rbox.setSpacing(8)
        row = QHBoxLayout()
        host_col = QVBoxLayout()
        host_col.addWidget(_field_label("host"))
        self.host = QLineEdit()
        self.host.setPlaceholderText("server.local")
        host_col.addWidget(self.host)
        user_col = QVBoxLayout()
        user_col.addWidget(_field_label("username"))
        self.user = QLineEdit()
        self.user.setPlaceholderText("root")
        user_col.addWidget(self.user)
        transport_col = QVBoxLayout()
        transport_col.addWidget(_field_label("transport"))
        self.transport = QComboBox()
        self.transport.addItems(["ssh", "tcp", "tls", "libssh2", "libssh"])
        transport_col.addWidget(self.transport)
        row.addLayout(host_col, 2)
        row.addLayout(user_col, 1)
        row.addLayout(transport_col, 1)
        rbox.addLayout(row)
        rbox.addWidget(_field_label("ssh key file (optional)"))
        self.keyfile = QLineEdit()
        self.keyfile.setPlaceholderText("~/.ssh/id_ed25519")
        rbox.addWidget(self.keyfile)
        self._remote_box.hide()
        box.addWidget(self._remote_box)

        box.addWidget(_field_label("uri"))
        self.uri = QLineEdit()
        self.uri.setToolTip("Edit directly for anything the fields above can't express")
        box.addWidget(self.uri)
        self.probe_result = QLabel("")
        self.probe_result.setWordWrap(True)
        self.probe_result.setObjectName("ConsoleHint")
        box.addWidget(self.probe_result)

        row2 = QHBoxLayout()
        probe = QPushButton("Test connection")
        probe.setProperty("class", "GhostButton")
        probe.clicked.connect(self._probe)
        row2.addWidget(probe)
        row2.addStretch(1)
        box.addLayout(row2)
        box.addSpacing(4)
        box.addLayout(_buttons(self, "Add connection"))

        self.remote.toggled.connect(self._remote_box.setVisible)
        self.remote.toggled.connect(self._rebuild)
        self.hypervisor.currentIndexChanged.connect(self._rebuild)
        for widget in (self.host, self.user, self.keyfile):
            widget.textChanged.connect(self._rebuild)
        self.transport.currentIndexChanged.connect(self._rebuild)
        self._rebuild()

    def _rebuild(self) -> None:
        import os

        from ..core.connection import build_uri

        key, spec = self._hvs[self.hypervisor.currentIndex()]
        self.hv_note.setText(spec["note"])
        keyfile = os.path.expanduser(self.keyfile.text().strip())
        self.uri.setText(
            build_uri(
                key,
                host=self.host.text().strip() if self.remote.isChecked() else "",
                user=self.user.text().strip() if self.remote.isChecked() else "",
                transport=self.transport.currentText(),
                keyfile=keyfile,
            )
        )
        self.probe_result.setText("")

    def _probe(self) -> None:
        from ..core.connection import svc_probe_uri
        from ..tasks import run_task

        uri = self.uri.text().strip()
        if not uri:
            return
        self.probe_result.setText("connecting…")
        run_task(
            lambda: svc_probe_uri(uri),
            done=lambda info: self.probe_result.setText(f"✓ {info}"),
            failed=lambda m: self.probe_result.setText(f"✗ {m}"),
        )

    def chosen_uri(self) -> str:
        return self.uri.text().strip()


class CloneDetailsDialog(SizedDialog):
    """Clone with a decision per disk, rather than copying everything."""

    ACTIONS = ("clone", "share", "skip")

    def __init__(self, parent, original: str, disks) -> None:  # disks: [DomainDisk]
        super().__init__(parent)
        self.setWindowTitle("Clone machine")
        self.setMinimumWidth(620)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Clone {original}"))
        note = QLabel(
            "Copying a disk duplicates its data; sharing points the clone at "
            "the same file, which only makes sense for read-only data; skipping "
            "leaves the clone without that disk."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        box.addWidget(_field_label("new name"))
        self.name = QLineEdit(f"{original}-clone")
        box.addWidget(self.name)

        self._rows = []
        if disks:
            box.addWidget(_field_label("disks"))
        for disk in disks:
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(f"{disk.dev} · {disk.capacity_gb:.1f} GB")
            label.setProperty("class", "StatVal")
            label.setFixedWidth(150)
            action = QComboBox()
            action.addItems(["copy to", "share original", "skip"])
            target = QLineEdit(self._suggest(disk.path, original))
            row.addWidget(label)
            row.addWidget(action)
            row.addWidget(target, 1)
            box.addLayout(row)
            action.currentIndexChanged.connect(
                lambda idx, t=target: t.setEnabled(idx == 0)
            )
            self._rows.append((disk, action, target))

        self.preserve_macs = QCheckBox(
            "Keep the original MAC addresses (only for a clone that replaces it)"
        )
        box.addWidget(self.preserve_macs)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Clone"))
        self._ok_button.setEnabled(True)
        self.name.textChanged.connect(
            lambda t: self._ok_button.setEnabled(bool(t.strip()))
        )
        self.name.textChanged.connect(self._rename_targets)

    @staticmethod
    def _suggest(path: str, original: str) -> str:
        directory, _, filename = path.rpartition("/")
        return f"{directory}/{filename.replace(original, original + '-clone', 1)}"

    def _rename_targets(self, new_name: str) -> None:
        for disk, _action, target in self._rows:
            directory, _, filename = disk.path.rpartition("/")
            suffix = filename.rpartition(".")[2]
            target.setText(f"{directory}/{new_name.strip()}-{disk.dev}.{suffix}")

    def disk_plan(self) -> list[tuple[str, str, str]]:
        plan = []
        for disk, action, target in self._rows:
            choice = self.ACTIONS[action.currentIndex()]
            path = target.text().strip() if choice == "clone" else disk.path
            plan.append((disk.dev, choice, path))
        return plan


class OsIconDialog(SizedDialog):
    """Pin a machine's icon, or leave it to detection."""

    def __init__(self, parent, vm_name: str, detected: str, override: str) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import (QAbstractItemView, QListView,
                                       QListWidget, QListWidgetItem)

        from ..core.osident import display_name
        from ..data.oslogos import all_keys, logo_pixmap, source_of

        self.setWindowTitle("Operating system icon")
        self.setMinimumSize(620, 540)
        self._chosen = override
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Icon for {vm_name}"))
        note = QLabel(
            "Detection reads the machine's recorded operating system, then "
            "falls back to its name. Pick one below to pin it instead."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)

        self.auto = QCheckBox(
            f"Detect automatically - currently {display_name(detected)}"
        )
        self.auto.setChecked(not override)
        box.addWidget(self.auto)

        # a picked file is stored as its path, which no catalogue key looks like
        from ..core.osident import is_custom_icon

        self._custom = override if is_custom_icon(override) else ""
        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        pick = QPushButton("Choose an image…")
        pick.setProperty("class", "GhostButton")
        pick.clicked.connect(self._pick_file)
        file_row.addWidget(pick)
        self.custom_label = QLabel()
        self.custom_label.setObjectName("ConsoleHint")
        file_row.addWidget(self.custom_label, 1)
        self.clear_custom = QPushButton("Use one below instead")
        self.clear_custom.setProperty("class", "GhostButton")
        self.clear_custom.clicked.connect(self._drop_file)
        file_row.addWidget(self.clear_custom)
        box.addLayout(file_row)

        self.grid = QListWidget()
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setIconSize(QSize(40, 40))
        self.grid.setGridSize(QSize(118, 86))
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setSpacing(4)
        # scroll whole rows, so we never show a half-clipped one
        self.grid.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerItem
        )
        for key in all_keys():
            if key == "unknown":
                continue
            item = QListWidgetItem(QIcon(logo_pixmap(key, 40)), display_name(key))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(f"{key} - from {source_of(key)}")
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.grid.addItem(item)
            if key == (override or detected):
                self.grid.setCurrentItem(item)
        box.addWidget(self.grid, 1)
        current = self.grid.currentItem()
        if current is not None:
            self.grid.scrollToItem(
                current, QAbstractItemView.ScrollHint.PositionAtCenter
            )

        self.auto.toggled.connect(lambda _on: self._refresh_state())
        self._refresh_state()
        self.grid.itemDoubleClicked.connect(lambda _i: self.accept())
        box.addSpacing(4)
        box.addLayout(_buttons(self, "Apply"))

    def _pick_file(self) -> None:
        from ..data.oslogos import CUSTOM_FILTER

        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an icon", str(Path.home()), CUSTOM_FILTER
        )
        if not path:
            return
        self._custom = path
        self.auto.setChecked(False)
        self._refresh_state()

    def _drop_file(self) -> None:
        self._custom = ""
        self._refresh_state()

    def _refresh_state(self) -> None:
        """Three ways to answer, so only one of them is live at a time."""
        auto = self.auto.isChecked()
        self.custom_label.setText(
            "" if auto or not self._custom else self._custom.rsplit("/", 1)[-1]
        )
        self.custom_label.setToolTip(self._custom)
        self.clear_custom.setVisible(bool(self._custom) and not auto)
        self.grid.setEnabled(not auto and not self._custom)

    def chosen_key(self) -> str:
        """The key to pin, a file path, or "" for auto-detect."""
        if self.auto.isChecked():
            return ""
        if self._custom:
            return self._custom
        item = self.grid.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""


class ModesDialog(SizedDialog):
    """Named configurations for one machine, and switching between them.

    A passthrough guest is really two machines: one with the GPU handed over
    and no console, one with a console and the GPU left alone. This keeps both
    and swaps the definition over.
    """

    switch_requested = Signal(str)  # mode name
    save_requested = Signal(str, str, str)  # name, note, marker
    delete_requested = Signal(str)
    diff_requested = Signal(str)

    def __init__(self, parent, vm_name: str, modes, running: bool) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QListWidget, QListWidgetItem

        self.setWindowTitle("Modes")
        self.setMinimumSize(620, 520)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Modes for {vm_name}"))
        note = QLabel(
            "A mode is the whole definition saved under a name. Switching "
            "defines it again, so it takes effect the next time the machine "
            "starts. What was there before is kept automatically."
        )
        note.setWordWrap(True)
        note.setObjectName("ConsoleHint")
        box.addWidget(note)

        self.list = QListWidget()
        for mode in modes:
            marks = []
            if mode.active:
                marks.append("active")
            if not mode.matches:
                marks.append("definition has changed since")
            label = mode.name
            if marks:
                label += "   (" + ", ".join(marks) + ")"
            if mode.note:
                label += f"\n{mode.note}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, mode.name)
            if mode.active:
                item.setForeground(QColor(theme.ACCENT))
            self.list.addItem(item)
        box.addWidget(self.list, 1)
        if not modes:
            empty = QLabel(
                "None saved yet. Set the machine up one way and save it as a "
                "mode. Set it up the other way and save that too. After that "
                "you can switch between them."
            )
            empty.setWordWrap(True)
            empty.setObjectName("ConsoleHint")
            box.addWidget(empty)

        if running:
            warn = QLabel(
                f"{vm_name} is running, so switching is unavailable. A mode "
                "changes the definition, which only applies on the next start."
            )
            warn.setWordWrap(True)
            warn.setProperty("class", "Warn")
            box.addWidget(warn)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.switch_btn = QPushButton("Switch to this")
        self.switch_btn.setProperty("class", "PrimaryButton")
        self.switch_btn.setEnabled(False)
        self.switch_btn.clicked.connect(self._switch)
        diff_btn = QPushButton("Compare")
        diff_btn.setProperty("class", "GhostButton")
        diff_btn.clicked.connect(self._diff)
        delete_btn = QPushButton("Delete")
        delete_btn.setProperty("class", "GhostButton")
        delete_btn.clicked.connect(self._delete)
        actions.addWidget(self.switch_btn)
        actions.addWidget(diff_btn)
        actions.addWidget(delete_btn)
        actions.addStretch(1)
        box.addLayout(actions)
        self._running = running
        self.list.currentItemChanged.connect(
            lambda cur, _prev: self.switch_btn.setEnabled(
                cur is not None and not running
            )
        )

        box.addWidget(_field_label("save the current configuration as"))
        save_row = QHBoxLayout()
        save_row.setSpacing(8)
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("prod")
        self.new_name.setMaximumWidth(160)
        self.new_note = QLineEdit()
        self.new_note.setPlaceholderText("what you use this one for")
        save_row.addWidget(self.new_name)
        save_row.addWidget(self.new_note, 1)
        save_btn = QPushButton("Save")
        save_btn.setProperty("class", "GhostButton")
        save_btn.clicked.connect(self._save)
        save_row.addWidget(save_btn)
        box.addLayout(save_row)

        self.marker = QLineEdit()
        self.marker.setPlaceholderText(
            "optional: a file a libvirt hook reads, e.g. "
            "/etc/libvirt/hooks/win11-mode"
        )
        box.addWidget(self.marker)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close = QPushButton("Close")
        close.setProperty("class", "GhostButton")
        close.clicked.connect(self.accept)
        close_row.addWidget(close)
        box.addLayout(close_row)

    def _selected(self) -> str:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def _switch(self) -> None:
        if self._selected() and not self._running:
            self.switch_requested.emit(self._selected())

    def _diff(self) -> None:
        if self._selected():
            self.diff_requested.emit(self._selected())

    def _delete(self) -> None:
        if self._selected():
            self.delete_requested.emit(self._selected())

    def _save(self) -> None:
        name = self.new_name.text().strip()
        if name:
            self.save_requested.emit(
                name, self.new_note.text().strip(), self.marker.text().strip()
            )


class UsbRulesDialog(SizedDialog):
    """Which host USB devices follow this machine automatically.

    A ticked device is attached whenever it is plugged in while the
    machine runs. Devices with a rule but not plugged in right now are
    listed too, so a rule can be removed without the device present.
    """

    def __init__(self, parent, vm_name: str, devices, rules: list[str]) -> None:
        # devices: list[HostDevice] (kind == "usb"), rules: idents
        super().__init__(parent)
        from PySide6.QtWidgets import QListWidgetItem

        self.setWindowTitle("Auto-attach USB")
        self.setMinimumSize(520, 420)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Auto-attach USB to {vm_name}"))
        note = QLabel(
            "A ticked device is handed to this machine whenever it appears "
            "on the host while the machine is running - plug it in and it "
            "shows up inside. A device already inside another machine is "
            "left alone."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)

        self.device_list = QListWidget()
        seen = set()
        for dev in devices:
            item = QListWidgetItem(f"{dev.label}  ({dev.ident})")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if dev.ident in rules
                else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, dev.ident)
            self.device_list.addItem(item)
            seen.add(dev.ident)
        for ident in rules:
            if ident in seen:
                continue
            item = QListWidgetItem(f"(not plugged in now)  ({ident})")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, ident)
            self.device_list.addItem(item)
        box.addWidget(self.device_list, 1)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Save rules"))

    def chosen(self) -> list[str]:
        out = []
        for i in range(self.device_list.count()):
            item = self.device_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out


class StartCheckDialog(SizedDialog):
    """What about this host would stop a machine starting.

    Opened after a failed start, and from the machine's menu when someone
    wants to look before trying. Reads only - every fix it points at lives
    somewhere else in the app, and is named rather than done from here.
    """

    def __init__(self, parent, vm_name: str, problems, libvirt_said: str = "") -> None:
        super().__init__(parent)
        from .. import theme

        self.setWindowTitle("Start check")
        self.setMinimumWidth(640)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Why {vm_name} will not start"))

        if libvirt_said:
            said = QLabel(f"libvirt said: {libvirt_said}")
            said.setWordWrap(True)
            said.setObjectName("ConsoleHint")
            box.addWidget(said)

        if not problems:
            good = QLabel(
                "Nothing about this host looks wrong: the disks are where "
                "the definition says, the memory is available, and any "
                "assigned devices are free. Whatever libvirt reported is "
                "the whole story."
            )
            good.setWordWrap(True)
            box.addWidget(good)
        colours = {"blocked": theme.DANGER, "caution": theme.WARN}
        for problem in problems:
            row = QHBoxLayout()
            mark = QLabel("●")
            mark.setFixedWidth(18)
            mark.setStyleSheet(f"color: {colours.get(problem.severity, theme.TEXT_DIM)};")
            text = QLabel(f"<b>{problem.what}</b><br>{problem.why}")
            text.setWordWrap(True)
            text.setTextFormat(Qt.TextFormat.RichText)
            text.setProperty("class", "Dim")
            row.addWidget(mark, alignment=Qt.AlignmentFlag.AlignTop)
            row.addWidget(text, 1)
            box.addLayout(row)

        box.addSpacing(6)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.setProperty("class", "GhostButton")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        box.addLayout(row)


class CompareDialog(SizedDialog):
    """Two machines, lined up property by property.

    The differences are what anyone opens this for, so they come first and
    the rest is behind a tick box. The whole diff of both definitions is a
    click away for when the summary does not have the answer.
    """

    def __init__(self, parent, names, rows, full_diff="") -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import (
            QCheckBox, QTableWidget, QTableWidgetItem,
        )

        left_name, right_name = names
        self.setWindowTitle(f"{left_name} vs {right_name}")
        self.setMinimumSize(760, 560)
        self._rows = list(rows)
        self._full_diff = full_diff

        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"{left_name}  vs  {right_name}"))

        differing = [r for r in self._rows if not r.same]
        self.summary = QLabel(
            f"{len(differing)} of {len(self._rows)} properties differ."
            if differing else
            "These two are the same in everything compared here."
        )
        self.summary.setWordWrap(True)
        self.summary.setProperty("class", "Dim")
        box.addWidget(self.summary)

        self.show_all = QCheckBox("Show properties that match as well")
        self.show_all.toggled.connect(lambda _on: self._fill())
        box.addWidget(self.show_all)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["", left_name, right_name])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # The label column is as wide as its longest label; the two value
        # columns split what is left evenly, because a comparison where one
        # side is elided and the other is not is hard to read across.
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, header.ResizeMode.Stretch)
        header.setSectionResizeMode(2, header.ResizeMode.Stretch)
        box.addWidget(self.table, 1)
        self._item = QTableWidgetItem
        self._accent = QColor(theme.ACCENT)
        self._faint = QColor(theme.TEXT_FAINT)
        self._fill()

        row = QHBoxLayout()
        if full_diff:
            whole = QPushButton("Whole definition…")
            whole.setProperty("class", "GhostButton")
            whole.clicked.connect(self._show_diff)
            row.addWidget(whole)
        row.addStretch(1)
        close = QPushButton("Close")
        close.setProperty("class", "PrimaryButton")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        box.addLayout(row)

    def _fill(self) -> None:
        rows = self._rows if self.show_all.isChecked() else [
            r for r in self._rows if not r.same
        ]
        self.table.setRowCount(len(rows))
        for i, difference in enumerate(rows):
            for column, text in enumerate(
                (difference.label, difference.left, difference.right)
            ):
                item = self._item(text)
                item.setToolTip(text)  # the long ones are elided in place
                if not difference.same and column:
                    item.setForeground(self._accent)
                elif difference.same:
                    item.setForeground(self._faint)
                self.table.setItem(i, column, item)

    def _show_diff(self) -> None:
        from .base import DiffDialog

        DiffDialog(
            self, "Both definitions", self._full_diff,
            note="Everything, including the parts that are unique to each "
                 "machine by definition - the name, the uuid, the MAC.",
        ).exec()
