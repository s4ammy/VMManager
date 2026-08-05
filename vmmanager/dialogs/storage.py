"""Storage dialogs: volumes, resize, pools, volume picker."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from .base import SizedDialog, _buttons, _field_label, _title


class VolumeDialog(SizedDialog):
    def __init__(self, parent, pools: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("New volume")
        self.setMinimumWidth(420)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("New volume"))
        box.addWidget(_field_label("pool"))
        self.pool = QComboBox()
        self.pool.addItems(pools)
        box.addWidget(self.pool)
        box.addWidget(_field_label("name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("data.qcow2")
        box.addWidget(self.name)
        row = QHBoxLayout()
        size_col = QVBoxLayout()
        size_col.addWidget(_field_label("size (GB)"))
        self.size = QDoubleSpinBox()
        self.size.setRange(0.1, 65536)
        self.size.setValue(20)
        self.size.setDecimals(1)
        size_col.addWidget(self.size)
        fmt_col = QVBoxLayout()
        fmt_col.addWidget(_field_label("format"))
        self.format = QComboBox()
        self.format.addItems(["qcow2", "raw"])
        fmt_col.addWidget(self.format)
        row.addLayout(size_col)
        row.addLayout(fmt_col)
        box.addLayout(row)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Create volume"))
        self._ok_button.setEnabled(False)
        self.name.textChanged.connect(
            lambda t: self._ok_button.setEnabled(bool(t.strip()))
        )

class ResizeVolumeDialog(SizedDialog):
    def __init__(self, parent, vol_name: str, current_gb: float) -> None:
        super().__init__(parent)
        self.setWindowTitle("Resize volume")
        self.setMinimumWidth(420)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Resize {vol_name}"))
        note = QLabel(
            f"Currently {current_gb:.1f} GB. Growing is safe; the guest sees "
            "the new size after a restart or rescan. Shrinking risks data "
            "loss and most formats refuse it."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        box.addWidget(_field_label("new size (GB)"))
        self.size = QDoubleSpinBox()
        self.size.setRange(0.1, 65536)
        self.size.setDecimals(1)
        self.size.setValue(max(current_gb, 0.1))
        box.addWidget(self.size)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Resize"))

class NewPoolDialog(SizedDialog):
    # type key -> (label, needs_target, fields: [(attr, label, placeholder)])
    TYPES = [
        ("dir", "dir - local directory", True, []),
        ("netfs", "netfs - NFS export", True,
         [("host", "nfs host", "nas.local"),
          ("export_path", "exported path", "/export/images")]),
        ("fs", "fs - mount a block device", True,
         [("source_device", "block device", "/dev/sdb1")]),
        ("logical", "logical - LVM volume group", False,
         [("source_name", "volume group", "vg-vms"),
          ("source_device", "pv device (only to create a new vg)", "/dev/sdb")]),
        ("iscsi", "iscsi - iSCSI target", False,
         [("host", "portal host", "san.local"),
          ("source_device", "target IQN", "iqn.2026-01.local.san:vms")]),
        ("zfs", "zfs - ZFS pool", False,
         [("source_name", "zpool name", "tank/vms")]),
    ]

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("New storage pool")
        self.setMinimumWidth(480)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("New storage pool"))
        box.addWidget(_field_label("name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("fast-nvme")
        box.addWidget(self.name)
        box.addWidget(_field_label("type"))
        self.ptype = QComboBox()
        self.ptype.addItems([label for _k, label, _t, _f in self.TYPES])
        box.addWidget(self.ptype)

        self._target_widget = QWidget()
        target_box = QVBoxLayout(self._target_widget)
        target_box.setContentsMargins(0, 0, 0, 0)
        target_box.setSpacing(8)
        target_box.addWidget(_field_label("target path"))
        row = QHBoxLayout()
        self.target = QLineEdit()
        self.target.setPlaceholderText("/var/lib/libvirt/pools/fast-nvme")
        browse = QPushButton("Browse…")
        browse.setProperty("class", "GhostButton")
        browse.clicked.connect(self._pick)
        row.addWidget(self.target, 1)
        row.addWidget(browse)
        target_box.addLayout(row)
        box.addWidget(self._target_widget)

        # per-type extra fields, all created up front and shown as needed
        self.host = QLineEdit()
        self.export_path = QLineEdit()
        self.source_device = QLineEdit()
        self.source_name = QLineEdit()
        self._field_rows: dict[str, QWidget] = {}
        for attr in ("host", "export_path", "source_device", "source_name"):
            holder = QWidget()
            holder_box = QVBoxLayout(holder)
            holder_box.setContentsMargins(0, 0, 0, 0)
            holder_box.setSpacing(4)
            holder_box.addWidget(_field_label(attr))  # placeholder, relabelled below
            holder_box.addWidget(getattr(self, attr))
            holder.hide()
            self._field_rows[attr] = holder
            box.addWidget(holder)

        box.addSpacing(6)
        box.addLayout(_buttons(self, "Create pool"))
        self._ok_button.setEnabled(False)
        for w in (self.name, self.target, self.host, self.export_path,
                  self.source_device, self.source_name):
            w.textChanged.connect(self._check)
        self.ptype.currentIndexChanged.connect(self._type_changed)
        self._type_changed(0)

    def _spec(self):
        return self.TYPES[self.ptype.currentIndex()]

    def pool_type(self) -> str:
        return self._spec()[0]

    def _type_changed(self, _index: int) -> None:
        key, _label, needs_target, fields = self._spec()
        self._target_widget.setVisible(needs_target)
        wanted = {attr for attr, _l, _p in fields}
        for attr, holder in self._field_rows.items():
            holder.setVisible(attr in wanted)
        for attr, label, placeholder in fields:
            holder = self._field_rows[attr]
            holder.layout().itemAt(0).widget().setText(label.upper())
            getattr(self, attr).setPlaceholderText(placeholder)
        self._check()

    def _check(self) -> None:
        key, _label, needs_target, fields = self._spec()
        ok = bool(self.name.text().strip())
        if needs_target:
            ok = ok and bool(self.target.text().strip())
        for attr, label, _p in fields:
            if "only to" in label:  # optional field
                continue
            ok = ok and bool(getattr(self, attr).text().strip())
        self._ok_button.setEnabled(ok)

    def _pick(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose target directory")
        if path:
            self.target.setText(path)

class VolumePickerDialog(SizedDialog):
    """Browse pools/volumes on the active connection - works remotely."""

    def __init__(self, parent, pools) -> None:  # pools: list[PoolInfo]
        super().__init__(parent)
        self.setWindowTitle("Choose a volume")
        self.setMinimumSize(560, 420)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Choose a volume"))
        from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Format", "Capacity"])
        self.tree.setColumnWidth(0, 300)
        self._paths: dict[int, str] = {}
        for pool in pools:
            if not pool.active:
                continue
            top = QTreeWidgetItem([pool.name, "", ""])
            self.tree.addTopLevelItem(top)
            for vol in pool.volumes:
                item = QTreeWidgetItem(
                    [vol.name, vol.format, f"{vol.capacity / 1024**3:.1f} GB"]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, vol.path)
                top.addChild(item)
            top.setExpanded(True)
        box.addWidget(self.tree, 1)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Choose"))
        self._ok_button.setEnabled(False)
        self.tree.itemSelectionChanged.connect(
            lambda: self._ok_button.setEnabled(self.selected_path() is not None)
        )
        self.tree.itemDoubleClicked.connect(
            lambda item, _c: item.data(0, Qt.ItemDataRole.UserRole) and self.accept()
        )

    def selected_path(self) -> str | None:
        items = self.tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.ItemDataRole.UserRole)


class PoolDialog(SizedDialog):
    """Create a pool of any type libvirt supports.

    Field layout comes from core.storage.POOL_TYPES, so adding a pool type
    there is enough - this dialog picks it up without changes.
    """

    def __init__(self, parent) -> None:
        super().__init__(parent)
        from ..core.storage import POOL_TYPES

        self.setWindowTitle("New storage pool")
        self.setMinimumWidth(520)
        self._types = list(POOL_TYPES.items())
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("New storage pool"))
        box.addWidget(_field_label("name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("fast-nvme")
        box.addWidget(self.name)
        box.addWidget(_field_label("type"))
        self.ptype = QComboBox()
        for _key, spec in self._types:
            self.ptype.addItem(spec["label"])
        box.addWidget(self.ptype)

        self._target_widget = QWidget()
        tbox = QVBoxLayout(self._target_widget)
        tbox.setContentsMargins(0, 0, 0, 0)
        tbox.setSpacing(8)
        tbox.addWidget(_field_label("target path"))
        row = QHBoxLayout()
        self.target = QLineEdit()
        self.target.setPlaceholderText("/var/lib/libvirt/pools/fast-nvme")
        browse = QPushButton("Browse…")
        browse.setProperty("class", "GhostButton")
        browse.clicked.connect(self._pick)
        row.addWidget(self.target, 1)
        row.addWidget(browse)
        tbox.addLayout(row)
        box.addWidget(self._target_widget)

        # every field any pool type might need, shown as required
        self._fields: dict[str, tuple[QWidget, QLabel, QLineEdit]] = {}
        for attr in ("host", "export", "source_device", "source_name",
                     "initiator", "auth_user", "secret_uuid"):
            holder = QWidget()
            hbox = QVBoxLayout(holder)
            hbox.setContentsMargins(0, 0, 0, 0)
            hbox.setSpacing(4)
            label = _field_label(attr)
            edit = QLineEdit()
            hbox.addWidget(label)
            hbox.addWidget(edit)
            holder.hide()
            box.addWidget(holder)
            self._fields[attr] = (holder, label, edit)
            edit.textChanged.connect(self._check)

        box.addSpacing(6)
        box.addLayout(_buttons(self, "Create pool"))
        self._ok_button.setEnabled(False)
        self.name.textChanged.connect(self._check)
        self.target.textChanged.connect(self._check)
        self.ptype.currentIndexChanged.connect(self._type_changed)
        self._type_changed(0)

    def _spec(self):
        return self._types[self.ptype.currentIndex()]

    def pool_type(self) -> str:
        return self._spec()[0]

    def _type_changed(self, _index: int) -> None:
        _key, spec = self._spec()
        self._target_widget.setVisible(bool(spec["target"]))
        wanted = {name: (label, placeholder) for name, label, placeholder in spec["fields"]}
        for attr, (holder, label, edit) in self._fields.items():
            show = attr in wanted
            holder.setVisible(show)
            if show:
                text, placeholder = wanted[attr]
                label.setText(text.upper())
                edit.setPlaceholderText(placeholder)
        self._check()

    def _check(self) -> None:
        _key, spec = self._spec()
        ok = bool(self.name.text().strip())
        if spec["target"]:
            ok = ok and bool(self.target.text().strip())
        for name, label, _placeholder in spec["fields"]:
            if "optional" in label or "only to" in label:
                continue
            ok = ok and bool(self._fields[name][2].text().strip())
        self._ok_button.setEnabled(ok)

    def options(self) -> dict:
        return {
            attr: edit.text().strip()
            for attr, (_holder, _label, edit) in self._fields.items()
        }

    def _pick(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose target directory")
        if path:
            self.target.setText(path)
