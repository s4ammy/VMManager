"""Dialogs, grouped by what they act on."""

from __future__ import annotations

from .base import (  # noqa: F401
    ConfirmDialog,
    DiffDialog,
    ErrorDialog,
    NameDialog,
    SizedDialog,
    fit_to_content,
    _buttons,
    _field_label,
    _title,
)
from .console import VncPasswordDialog  # noqa: F401
from .hardware import AttachDiskDialog, AttachNicDialog, ChoiceDialog, DisplayFixDialog, GrowDiskDialog, GuestFeaturesDialog, HostDeviceDialog, MdevDialog, MoveDiskDialog, PassthroughDialog, ShareFolderDialog, SingleGpuDialog, TuningDialog, _tuning_cpu_limits, VirtioIsoDialog, WindowsToolingDialog  # noqa: F401
from .machine import CatalogDialog, CloneDetailsDialog, CloneDialog, ConnectionDialog, OsIconDialog, DeleteVmDialog, MigrateDialog, ModesDialog, ScheduleDialog, StartCheckDialog, UsbRulesDialog, WakeScheduleDialog  # noqa: F401
from .network import NetworkDetailsDialog, NetworkDialog, NwFiltersDialog  # noqa: F401
from .snapshot import SnapshotDialog  # noqa: F401
from .storage import NewPoolDialog, PoolDialog, ResizeVolumeDialog, VolumeDialog, VolumePickerDialog  # noqa: F401

__all__ = [
    "TuningDialog",
    "OsIconDialog",
    "NetworkDetailsDialog",
    "PoolDialog",
    "CloneDetailsDialog",
    "ChoiceDialog",
    "ConnectionDialog",
    "AttachDiskDialog",
    "AttachNicDialog",
    "CatalogDialog",
    "CloneDialog",
    "ConfirmDialog",
    "DeleteVmDialog",
    "GrowDiskDialog",
    "MdevDialog",
    "SingleGpuDialog",
    "MoveDiskDialog",
    "DisplayFixDialog",
    "ErrorDialog",
    "HostDeviceDialog",
    "MigrateDialog",
    "NetworkDialog",
    "NwFiltersDialog",
    "UsbRulesDialog",
    "NewPoolDialog",
    "PassthroughDialog",
    "ResizeVolumeDialog",
    "ScheduleDialog",
    "StartCheckDialog",
    "ShareFolderDialog",
    "SnapshotDialog",
    "VirtioIsoDialog",
    "VncPasswordDialog",
    "VolumeDialog",
    "VolumePickerDialog",
    "WakeScheduleDialog",
    "WindowsToolingDialog",
]
