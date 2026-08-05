"""Shared imports, constants and small widgets for the detail tabs."""

from __future__ import annotations

import datetime
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ... import theme
from ...dialogs import (
    ChoiceDialog,
    HostdevOptionsDialog,
    LabelsDialog,
    NicEditDialog,
    AttachDiskDialog,
    AttachNicDialog,
    BootOrderDialog,
    ConfirmDialog,
    CpuDialog,
    DiskCacheDialog,
    MdevDialog,
    MoveDiskDialog,
    DisplayFixDialog,
    ErrorDialog,
    HostDeviceDialog,
    MemoryDialog,
    PassthroughDialog,
    GuestFeaturesDialog,
    ShareFolderDialog,
    TuningDialog,
    SnapshotDialog,
    VideoDialog,
    VirtioIsoDialog,
    VncPasswordDialog,
    VolumePickerDialog,
    WindowsToolingDialog,
)
from ...data.history import data_extent, query_history
from ...libvirt_service import (
    AUDIO_BACKENDS,
    KEY_COMBOS,
    PANIC_MODELS,
    WATCHDOG_ACTIONS,
    DomainSnapshot,
    GraphicsInfo,
    Hardware,
    HostSnapshot,
    SnapshotInfo,
    current_uri,
    svc_add_agent_channel,
    svc_add_display,
    svc_add_sound,
    svc_add_spice_agent_channel,
    svc_add_vnc_display,
    svc_agent_action,
    svc_attach_disk,
    svc_attach_filesystem,
    svc_attach_cdrom,
    svc_attach_hostdev,
    svc_attach_input,
    svc_attach_nic,
    svc_backup,
    svc_change_media,
    svc_create_snapshot,
    svc_create_volume,
    svc_define_xml,
    svc_definition_diff,
    svc_delete_checkpoint,
    svc_delete_snapshot,
    svc_detach_disk,
    svc_detach_filesystem,
    svc_detach_hostdev,
    svc_detach_input,
    svc_detach_nic,
    svc_fetch_file,
    svc_get_device_xml,
    svc_get_hardware,
    svc_get_xml,
    svc_display_health,
    svc_graphics_info,
    svc_guest_exec,
    svc_guest_info,
    svc_iommu_report,
    svc_list_checkpoints,
    svc_list_host_devices,
    svc_list_network_names,
    svc_list_pools,
    svc_move_disk,
    svc_mdev_types,
    svc_list_mdevs,
    svc_create_mdev,
    svc_delete_mdev,
    svc_nwfilter_names,
    svc_set_nic_filter,
    svc_list_snapshots,
    svc_qemu_cmdline,
    svc_remove_sound,
    svc_restore_backup,
    svc_revert_snapshot,
    svc_screenshot,
    svc_send_file,
    svc_add_audio,
    svc_add_panic,
    svc_add_smartcard,
    svc_add_usb_redirection,
    svc_add_vsock,
    svc_add_watchdog,
    svc_add_memory_device,
    svc_machine_types,
    svc_remove_simple_device,
    svc_set_boot_menu,
    svc_set_controller_model,
    svc_set_hostdev_options,
    svc_set_cpu_pinning,
    svc_set_disk_throttle,
    svc_set_hugepages,
    svc_set_iothreads,
    svc_get_tuning,
    svc_get_features,
    svc_set_features,
    svc_feature_support,
    svc_list_evdev,
    svc_host_topology,
    svc_set_labels,
    svc_set_machine_type,
    svc_set_nic,
    svc_set_video_accel,
    svc_set_watchdog_action,
    svc_send_keys,
    svc_set_boot_order,
    svc_set_cpu,
    svc_set_device_xml,
    svc_set_disk_cache,
    svc_set_memory,
    svc_set_vcpus,
    svc_set_video,
    svc_upload_volume_from_file,
    svc_windows_tooling_state,
    open_external,
)
from ...console.serialterm import SerialSession, TerminalWidget
from ...console.spice import SpiceClient
from ...logs import log
from ...tasks import run_task
from ...console.tunnel import SSHTunnel, is_remote_uri, ssh_target_of
from ...console.vnc import VncClient
from ...widgets import Led, Sparkline, flow_row, fmt_bytes, fmt_mem

# console send-key combos as X11 keysyms for the in-app VNC path
_COMBO_KEYSYMS = {
    "Ctrl+Alt+Del": [0xFFE3, 0xFFE9, 0xFFFF],
    "Ctrl+Alt+Backspace": [0xFFE3, 0xFFE9, 0xFF08],
    "Ctrl+Alt+F1": [0xFFE3, 0xFFE9, 0xFFBE],
    "Ctrl+Alt+F2": [0xFFE3, 0xFFE9, 0xFFBF],
    "Ctrl+Alt+F3": [0xFFE3, 0xFFE9, 0xFFC0],
    "Ctrl+Alt+F7": [0xFFE3, 0xFFE9, 0xFFC4],
    "PrintScreen": [0xFF61],
}

# same combos as XT scancodes for the SPICE path
_COMBO_SCANCODES = {
    "Ctrl+Alt+Del": [0x1D, 0x38, 0x100 | 0x53],
    "Ctrl+Alt+Backspace": [0x1D, 0x38, 0x0E],
    "Ctrl+Alt+F1": [0x1D, 0x38, 0x3B],
    "Ctrl+Alt+F2": [0x1D, 0x38, 0x3C],
    "Ctrl+Alt+F3": [0x1D, 0x38, 0x3D],
    "Ctrl+Alt+F7": [0x1D, 0x38, 0x41],
    "PrintScreen": [0x100 | 0x37],
}

_RANGES = [
    ("live · 4 min", 0),
    ("30 minutes", 30 * 60),
    ("2 hours", 2 * 3600),
    ("12 hours", 12 * 3600),
    ("24 hours", 24 * 3600),
    ("7 days", 7 * 86400),
]


class ChartCard(QFrame):
    def __init__(self, title: str, max_value: float | None = None) -> None:
        super().__init__()
        self.setProperty("class", "ChartCard")
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 13, 16, 13)
        box.setSpacing(6)
        head = QHBoxLayout()
        t = QLabel(title.upper())
        t.setProperty("class", "ChartTitle")
        self.value = QLabel(" - ")
        self.value.setProperty("class", "ChartValue")
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(self.value)
        box.addLayout(head)
        self.spark = Sparkline(max_value=max_value)
        self.spark.setMinimumHeight(64)
        box.addWidget(self.spark, 1)


def _table(columns: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.horizontalHeader().setStretchLastSection(True)
    table.verticalHeader().hide()
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    return table




def _ghost(text: str, slot) -> QPushButton:
    btn = QPushButton(text)
    btn.setProperty("class", "GhostButton")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.clicked.connect(slot)
    return btn


def _scrolled(content: QWidget) -> QScrollArea:
    """Wrap tab content so a tall tab scrolls instead of squeezing.

    Otherwise a tab that doesn't fit forces its minimum height on the whole
    window, and the layout takes the missing pixels out of whatever yields
    first - always the text boxes. Width still tracks the viewport.
    """
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    scroll.setMinimumHeight(240)   # enough to stay usable when squeezed
    return scroll


class DetachedConsoleWindow(QWidget):
    """Console client in its own top-level window. F11 toggles fullscreen."""

    closed = Signal()

    def __init__(self, client: QWidget, title: str) -> None:
        super().__init__()
        # The hint line stays behind on the tab, so the one thing someone needs
        # to know while the keyboard is grabbed goes in the title instead.
        from ...console.grab import release_combo_name

        self.setWindowTitle(
            f"{title} - console (F11 fullscreen · {release_combo_name()} "
            "releases the keyboard)"
        )
        self.setObjectName("ConsoleWindow")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(client)
        self.resize(1100, 720)
        from PySide6.QtGui import QKeySequence, QShortcut

        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)


# This module exists to hand one shared set of imports and helpers to every
# tab module, so the star-import must carry the private names too.
__all__ = [_name for _name in dir() if not _name.startswith("__")]
