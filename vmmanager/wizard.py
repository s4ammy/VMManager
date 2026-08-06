"""New-machine wizard: install source, OS-aware defaults, cloud-init, review."""

from __future__ import annotations

import os
import shutil

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .dialogs import SizedDialog, VolumePickerDialog
from .core.unattend import EDITIONS, Unattend
from .libvirt_service import CloudInit, CreateSpec, current_uri
from .data.osinfo import OsVariant, detect_iso, list_os_variants
from .tasks import run_task
from .console.tunnel import is_remote_uri


def _field_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setProperty("class", "FieldLabel")
    return label


class NewVmDialog(SizedDialog):
    """Everything on one card, with libosinfo filling in sensible numbers."""

    def __init__(
        self,
        parent,
        networks: list[str],
        pools,  # list[PoolInfo]
        host_cpus: int,
        host_mem_mb: int,
        templates: list[tuple[str, str]] | None = None,  # (name, uuid)
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New machine")
        self.setMinimumWidth(520)
        self._variants: tuple[OsVariant, ...] = ()
        self._pools = pools
        self._templates = templates or []
        self._remote = is_remote_uri(current_uri())
        pool_names = [p.name for p in pools if p.active] or ["default"]

        box = QVBoxLayout(self)
        box.setContentsMargins(26, 24, 26, 20)
        box.setSpacing(10)

        title = QLabel("New machine")
        title.setObjectName("DialogTitle")
        box.addWidget(title)
        note = QLabel("virtio disk and network, q35 chipset, VNC display, guest-agent channel.")
        note.setProperty("class", "Dim")
        box.addWidget(note)
        box.addSpacing(4)

        name_row = QHBoxLayout()
        name_col = QVBoxLayout()
        name_col.addWidget(_field_label("name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("fedora-42")
        name_col.addWidget(self.name)
        os_col = QVBoxLayout()
        os_col.addWidget(_field_label("operating system"))
        self.os_combo = QComboBox()
        self.os_combo.setEditable(True)
        self.os_combo.addItem("detecting…")
        self.os_combo.setEnabled(False)
        os_col.addWidget(self.os_combo)
        name_row.addLayout(name_col, 1)
        name_row.addLayout(os_col, 1)
        box.addLayout(name_row)

        box.addWidget(_field_label("install from"))
        self.src_iso = QRadioButton("Install ISO")
        self.src_url = QRadioButton("Network install tree (URL)")
        self.src_import = QRadioButton(
            "Existing disk image (qcow2, raw, or VMware/VirtualBox/Hyper-V)"
        )
        self.src_empty = QRadioButton("Empty disk - boot from network or attach media later")
        self.src_template = QRadioButton(
            "A template - instant copy-on-write clone, shares the base image"
        )
        self.src_iso.setChecked(True)
        box.addWidget(self.src_iso)
        box.addWidget(self.src_url)
        box.addWidget(self.src_import)
        box.addWidget(self.src_empty)
        if self._templates:
            box.addWidget(self.src_template)

        self._template_row = QWidget()
        tmpl_box = QVBoxLayout(self._template_row)
        tmpl_box.setContentsMargins(0, 0, 0, 0)
        self.template = QComboBox()
        self.template.addItems([name for name, _uuid in self._templates])
        tmpl_box.addWidget(self.template)
        tmpl_note = QLabel(
            "The clone inherits the template's processor, memory and devices. "
            "Only its name and network are asked for here."
        )
        tmpl_note.setWordWrap(True)
        tmpl_note.setObjectName("ConsoleHint")
        tmpl_box.addWidget(tmpl_note)
        box.addWidget(self._template_row)

        self._url_row = QWidget()
        url_box = QVBoxLayout(self._url_row)
        url_box.setContentsMargins(0, 0, 0, 0)
        url_box.setSpacing(8)
        self.location_url = QLineEdit()
        self.location_url.setPlaceholderText(
            "https://deb.debian.org/debian/dists/stable/main/installer-amd64/"
        )
        url_box.addWidget(self.location_url)
        url_hint = QLabel(
            "A distro's install tree. The kernel and initrd are fetched from "
            "it, so the installer boots straight away, pick the matching "
            "operating system above."
        )
        url_hint.setWordWrap(True)
        url_hint.setObjectName("ConsoleHint")
        url_box.addWidget(url_hint)
        url_box.addWidget(_field_label("kernel arguments (optional)"))
        self.kernel_args = QLineEdit()
        self.kernel_args.setPlaceholderText("console=ttyS0 inst.ks=https://…/ks.cfg")
        url_box.addWidget(self.kernel_args)
        self._url_row.hide()
        box.addWidget(self._url_row)

        self._iso_row = QWidget()
        iso_box = QHBoxLayout(self._iso_row)
        iso_box.setContentsMargins(0, 0, 0, 0)
        self.iso = QLineEdit()
        self.iso.setPlaceholderText("/path/to/install.iso")
        iso_box.addWidget(self.iso, 1)
        iso_pool = QPushButton("From pool…")
        iso_pool.setProperty("class", "GhostButton")
        iso_pool.clicked.connect(lambda: self._pick_volume(self.iso))
        iso_box.addWidget(iso_pool)
        if not self._remote:
            browse_iso = QPushButton("Browse…")
            browse_iso.setProperty("class", "GhostButton")
            browse_iso.clicked.connect(self._pick_iso)
            iso_box.addWidget(browse_iso)
        box.addWidget(self._iso_row)

        self._import_row = QWidget()
        imp_box = QHBoxLayout(self._import_row)
        imp_box.setContentsMargins(0, 0, 0, 0)
        self.import_path = QLineEdit()
        self.import_path.setPlaceholderText("/path/to/image.qcow2")
        imp_box.addWidget(self.import_path, 1)
        catalog_btn = QPushButton("Catalog…")
        catalog_btn.setProperty("class", "PrimaryButton")
        catalog_btn.setToolTip("Download a fresh cloud image (Debian, Ubuntu, …)")
        catalog_btn.clicked.connect(self._open_catalog)
        imp_box.addWidget(catalog_btn)
        imp_pool = QPushButton("From pool…")
        imp_pool.setProperty("class", "GhostButton")
        imp_pool.clicked.connect(lambda: self._pick_volume(self.import_path))
        imp_box.addWidget(imp_pool)
        if not self._remote:
            browse_img = QPushButton("Browse…")
            browse_img.setProperty("class", "GhostButton")
            browse_img.clicked.connect(self._pick_image)
            imp_box.addWidget(browse_img)
        self._import_row.hide()
        box.addWidget(self._import_row)
        self.import_hint = QLabel("")
        self.import_hint.setObjectName("ConsoleHint")
        self.import_hint.setWordWrap(True)
        self.import_hint.hide()
        box.addWidget(self.import_hint)

        res_row = QHBoxLayout()
        res_row.setSpacing(14)
        cpu_col = QVBoxLayout()
        cpu_col.addWidget(_field_label("vcpus"))
        self.vcpus = QSpinBox()
        self.vcpus.setRange(1, host_cpus)
        self.vcpus.setValue(min(4, host_cpus))
        cpu_col.addWidget(self.vcpus)
        mem_col = QVBoxLayout()
        mem_col.addWidget(_field_label("memory (MiB)"))
        self.memory = QSpinBox()
        self.memory.setRange(128, host_mem_mb)
        self.memory.setSingleStep(1024)
        self.memory.setValue(min(4096, host_mem_mb))
        mem_col.addWidget(self.memory)
        res_row.addLayout(cpu_col)
        res_row.addLayout(mem_col)
        box.addLayout(res_row)

        self._disk_row = QWidget()
        disk_box = QHBoxLayout(self._disk_row)
        disk_box.setContentsMargins(0, 0, 0, 0)
        disk_box.setSpacing(14)
        pool_col = QVBoxLayout()
        pool_col.addWidget(_field_label("storage pool"))
        self.pool = QComboBox()
        self.pool.addItems(pool_names)
        pool_col.addWidget(self.pool)
        size_col = QVBoxLayout()
        size_col.addWidget(_field_label("disk (GB)"))
        self.disk = QDoubleSpinBox()
        self.disk.setRange(1, 65536)
        self.disk.setDecimals(0)
        self.disk.setValue(40)
        size_col.addWidget(self.disk)
        disk_box.addLayout(pool_col, 1)
        disk_box.addLayout(size_col)
        box.addWidget(self._disk_row)

        net_row = QHBoxLayout()
        net_row.setSpacing(14)
        net_col = QVBoxLayout()
        net_col.addWidget(_field_label("network"))
        self.network = QComboBox()
        self.network.addItems(networks or ["default"])
        net_col.addWidget(self.network)
        net_row.addLayout(net_col, 1)
        fw_col = QVBoxLayout()
        fw_col.addWidget(_field_label("firmware"))
        self.firmware = QComboBox()
        self.firmware.addItems(["UEFI", "BIOS"])
        fw_col.addWidget(self.firmware)
        net_row.addLayout(fw_col)
        tpm_col = QVBoxLayout()
        tpm_col.addWidget(_field_label("tpm 2.0"))
        self.tpm = QCheckBox("emulated")
        self._swtpm_available = shutil.which("swtpm") is not None
        if not self._swtpm_available:
            self.tpm.setEnabled(False)
            self.tpm.setToolTip("Install swtpm to add an emulated TPM (needed for Windows 11)")
        tpm_col.addWidget(self.tpm)
        net_row.addLayout(tpm_col)
        box.addLayout(net_row)

        self.cloud_init = QCheckBox(
            "Cloud-init - set up user, password and SSH key on first boot"
        )
        box.addWidget(self.cloud_init)
        self._ci_widget = QWidget()
        ci_box = QVBoxLayout(self._ci_widget)
        ci_box.setContentsMargins(0, 0, 0, 0)
        ci_box.setSpacing(8)
        ci_row = QHBoxLayout()
        u_col = QVBoxLayout()
        u_col.addWidget(_field_label("user"))
        self.ci_user = QLineEdit(os.environ.get("USER", "admin"))
        u_col.addWidget(self.ci_user)
        p_col = QVBoxLayout()
        p_col.addWidget(_field_label("password"))
        self.ci_password = QLineEdit()
        self.ci_password.setEchoMode(QLineEdit.EchoMode.Password)
        p_col.addWidget(self.ci_password)
        h_col = QVBoxLayout()
        h_col.addWidget(_field_label("hostname"))
        self.ci_hostname = QLineEdit()
        self.ci_hostname.setPlaceholderText("defaults to machine name")
        h_col.addWidget(self.ci_hostname)
        ci_row.addLayout(u_col)
        ci_row.addLayout(p_col)
        ci_row.addLayout(h_col)
        ci_box.addLayout(ci_row)
        self.ci_agent = QCheckBox(
            "Install qemu-guest-agent + spice-vdagent on first boot (needs network)"
        )
        self.ci_agent.setChecked(True)
        ci_box.addWidget(self.ci_agent)
        ci_box.addWidget(_field_label("ssh public key (optional)"))
        self.ci_key = QLineEdit()
        self.ci_key.setPlaceholderText("ssh-ed25519 AAAA…")
        default_key = os.path.expanduser("~/.ssh/id_ed25519.pub")
        if os.path.exists(default_key):
            try:
                with open(default_key) as f:
                    self.ci_key.setText(f.read().strip())
            except OSError:
                pass
        ci_box.addWidget(self.ci_key)
        self._ci_widget.hide()
        box.addWidget(self._ci_widget)
        self.cloud_init.toggled.connect(self._ci_widget.setVisible)

        # -- the same idea for Windows
        self.unattend = QCheckBox(
            "Unattended Windows install - answer Setup's questions in advance"
        )
        self.unattend.setToolTip(
            "Writes an autounattend.xml onto a small disc Setup reads. It "
            "also points Setup at the virtio storage driver, which is what "
            "otherwise leaves it showing an empty disk list."
        )
        box.addWidget(self.unattend)
        self._ua_widget = QWidget()
        ua_box = QVBoxLayout(self._ua_widget)
        ua_box.setContentsMargins(0, 0, 0, 0)
        ua_box.setSpacing(8)
        ua_row = QHBoxLayout()
        uu_col = QVBoxLayout()
        uu_col.addWidget(_field_label("user"))
        self.ua_user = QLineEdit(os.environ.get("USER", "admin"))
        uu_col.addWidget(self.ua_user)
        up_col = QVBoxLayout()
        up_col.addWidget(_field_label("password"))
        self.ua_password = QLineEdit()
        self.ua_password.setEchoMode(QLineEdit.EchoMode.Password)
        up_col.addWidget(self.ua_password)
        ue_col = QVBoxLayout()
        ue_col.addWidget(_field_label("edition"))
        self.ua_edition = QComboBox()
        self.ua_edition.addItems(list(EDITIONS))
        self.ua_edition.setEditable(True)
        self.ua_edition.setToolTip(
            "Must match what the ISO calls it, exactly - Setup matches on "
            "this name and stops to ask if it finds nothing."
        )
        ue_col.addWidget(self.ua_edition)
        ua_row.addLayout(uu_col)
        ua_row.addLayout(up_col)
        ua_row.addLayout(ue_col)
        ua_box.addLayout(ua_row)
        ua_note = QLabel(
            "The machine needs the virtio-win disc attached as well, which "
            "is where the driver comes from - add it from the hardware tab "
            "before starting, or the install will not find the disk."
        )
        ua_note.setWordWrap(True)
        ua_note.setObjectName("ConsoleHint")
        ua_box.addWidget(ua_note)
        self._ua_widget.hide()
        box.addWidget(self._ua_widget)
        self.unattend.toggled.connect(self._ua_widget.setVisible)

        box.addSpacing(8)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setProperty("class", "GhostButton")
        cancel.clicked.connect(self.reject)
        self.create = QPushButton("Create machine")
        self.create.setProperty("class", "PrimaryButton")
        self.create.setDefault(True)
        self.create.setEnabled(False)
        self.create.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.create)
        box.addLayout(buttons)

        self.name.textChanged.connect(
            lambda t: self.create.setEnabled(bool(t.strip()))
        )
        for radio in (self.src_iso, self.src_import, self.src_empty,
                      self.src_template):
            radio.toggled.connect(self._source_changed)
        self._source_changed(True)   # apply the initial choice's visibility
        self.os_combo.currentIndexChanged.connect(self._os_picked)

        run_task(list_os_variants, done=self._variants_loaded, failed=lambda _m: None)

    # -- os variants

    def _variants_loaded(self, variants: tuple[OsVariant, ...]) -> None:
        self._variants = variants
        current = self.os_combo.currentText()
        self.os_combo.blockSignals(True)
        self.os_combo.clear()
        for v in variants:
            self.os_combo.addItem(f"{v.name}  ({v.short_id})", v)
        self.os_combo.setEnabled(True)
        completer = QCompleter([f"{v.name}  ({v.short_id})" for v in variants])
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.os_combo.setCompleter(completer)
        generic = next(
            (i for i, v in enumerate(variants) if v.short_id == "generic"), 0
        )
        self.os_combo.setCurrentIndex(generic)
        self.os_combo.blockSignals(False)
        if current and current != "detecting…":
            self._select_variant(current)

    def _select_variant(self, short_id: str) -> None:
        for i in range(self.os_combo.count()):
            v = self.os_combo.itemData(i)
            if v is not None and v.short_id == short_id:
                self.os_combo.setCurrentIndex(i)
                return

    def _os_picked(self, index: int) -> None:
        v = self.os_combo.itemData(index)
        if v is None:
            return
        self.memory.setValue(min(max(v.rec_mem_mb, v.min_mem_mb), self.memory.maximum()))
        self.vcpus.setValue(min(max(v.rec_vcpus, 1), self.vcpus.maximum()))
        self.disk.setValue(max(v.rec_storage_gb, 1))
        if v.short_id.startswith("win"):
            self.tpm.setChecked(self.tpm.isEnabled())

    def selected_variant(self) -> OsVariant | None:
        return self.os_combo.currentData()

    def template_uuid(self) -> str:
        """The template to clone, or "" when building a machine from scratch."""
        if not self.src_template.isChecked():
            return ""
        index = self.template.currentIndex()
        if 0 <= index < len(self._templates):
            return self._templates[index][1]
        return ""

    # -- sources

    def _source_changed(self, _checked: bool) -> None:
        from_template = self.src_template.isChecked()
        self._iso_row.setVisible(self.src_iso.isChecked())
        self._url_row.setVisible(self.src_url.isChecked())
        self._import_row.setVisible(self.src_import.isChecked())
        self.import_hint.setVisible(
            self.src_import.isChecked() and bool(self.import_hint.text())
        )
        self._template_row.setVisible(from_template)
        self._disk_row.setVisible(
            not self.src_import.isChecked() and not from_template
        )
        # a clone inherits all of this from the template it copies
        for widget in (self.vcpus, self.memory, self.os_combo, self.firmware,
                       self.pool):
            widget.setEnabled(not from_template)
        if self._swtpm_available:
            self.tpm.setEnabled(not from_template)
        if from_template:
            self.cloud_init.setChecked(False)
        # a URL install runs the distro's own installer, so cloud-init and the
        # seed disk have nothing to do
        self.cloud_init.setEnabled(
            not self.src_url.isChecked() and not from_template
        )
        if self.src_url.isChecked():
            self.cloud_init.setChecked(False)
        # An answer file is read by Windows Setup, so it only means anything
        # for an install from an ISO.
        self.unattend.setEnabled(self.src_iso.isChecked() and not from_template)
        if not self.unattend.isEnabled():
            self.unattend.setChecked(False)

    def _pick_iso(self) -> None:
        settings = QSettings("vmmanager", "vmmanager")
        start_dir = settings.value("iso_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose install ISO", start_dir, "Disc images (*.iso);;All files (*)"
        )
        if not path:
            return
        self.iso.setText(path)
        settings.setValue("iso_dir", os.path.dirname(path))
        run_task(
            lambda: detect_iso(path),
            done=lambda sid: sid and self._select_variant(sid),
            failed=lambda _m: None,
        )

    def _pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose disk image", os.path.expanduser("~"),
            "Disk images (*.qcow2 *.img *.raw);;"
            "Other hypervisors (*.vmdk *.vhdx *.vhd *.vdi *.ova *.ovf);;"
            "All files (*)",
        )
        if not path:
            return
        self.import_path.setText(path)
        self._describe_foreign(path)

    def _describe_foreign(self, path: str) -> None:
        """An OVA/OVF pick fills in what the appliance asked for."""
        from .libvirt_service import describe_source, is_foreign_source

        if not is_foreign_source(path):
            self.import_hint.setText("")
            self.import_hint.hide()
            return
        self.import_hint.setText("converted to qcow2 in the pool on create")
        self.import_hint.show()

        def apply(info) -> None:
            if info is None:
                return
            if info.name and not self.name.text().strip():
                self.name.setText(info.name)
            if info.vcpus:
                self.vcpus.setValue(info.vcpus)
            if info.memory_mb:
                self.memory.setValue(info.memory_mb)
            extra = ""
            if len(info.disk_files) > 1:
                extra = (f" · only the first of its {len(info.disk_files)} "
                         "disks is imported")
            self.import_hint.setText(
                "appliance defaults applied - converted to qcow2 on create"
                + extra
            )

        run_task(
            lambda: describe_source(path),
            done=apply,
            failed=lambda m: self.import_hint.setText(f"unreadable: {m}"),
        )

    def _pick_volume(self, line_edit: QLineEdit) -> None:
        picker = VolumePickerDialog(self, self._pools)
        if picker.exec() == QDialog.DialogCode.Accepted and picker.selected_path():
            line_edit.setText(picker.selected_path())

    def _open_catalog(self) -> None:
        from .dialogs import CatalogDialog

        dialog = CatalogDialog(self, self._pools)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.volume_path:
            return
        self.src_import.setChecked(True)
        self.import_path.setText(dialog.volume_path)
        if dialog.image is not None:
            self._select_variant(dialog.image.osinfo_short_id)
            self.cloud_init.setChecked(True)
            if not self.name.text().strip():
                self.name.setText(dialog.image.osinfo_short_id)

    # -- result

    def spec(self) -> CreateSpec:
        variant = self.selected_variant()
        cloudinit = None
        if self.cloud_init.isChecked() and self.ci_user.text().strip():
            packages: tuple[str, ...] = ()
            if self.ci_agent.isChecked():
                packages = ("qemu-guest-agent", "spice-vdagent")
            cloudinit = CloudInit(
                hostname=self.ci_hostname.text().strip(),
                user=self.ci_user.text().strip(),
                password=self.ci_password.text(),
                ssh_key=self.ci_key.text().strip(),
                packages=packages,
            )
        unattend = None
        if self.unattend.isChecked() and self.ua_user.text().strip():
            variant = self.selected_variant()
            unattend = Unattend(
                user=self.ua_user.text().strip(),
                password=self.ua_password.text(),
                hostname=self.name.text().strip(),
                edition=self.ua_edition.currentText().strip(),
                windows_version=(
                    "w10" if variant and "win10" in (variant.short_id or "")
                    else "w11"
                ),
            )
        return CreateSpec(
            name=self.name.text().strip(),
            vcpus=self.vcpus.value(),
            memory_mb=self.memory.value(),
            network=self.network.currentText(),
            uefi=self.firmware.currentText() == "UEFI",
            tpm=self.tpm.isChecked() and self.tpm.isEnabled(),
            pool=self.pool.currentText(),
            disk_gb=self.disk.value(),
            import_path=(
                self.import_path.text().strip() or None
                if self.src_import.isChecked()
                else None
            ),
            iso_path=(
                self.iso.text().strip() or None if self.src_iso.isChecked() else None
            ),
            osinfo_id=variant.osinfo_id if variant else "",
            cloudinit=cloudinit,
            unattend=unattend,
            location_url=(
                self.location_url.text().strip() if self.src_url.isChecked() else ""
            ),
            osinfo_short_id=variant.short_id if variant else "",
            kernel_args=(
                self.kernel_args.text().strip() if self.src_url.isChecked() else ""
            ),
        )
