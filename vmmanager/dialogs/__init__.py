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
from .hardware import AttachDiskDialog, AttachNicDialog, BootOrderDialog, ChoiceDialog, CpuDialog, DiskCacheDialog, DisplayFixDialog, GuestFeaturesDialog, HostDeviceDialog, HostdevOptionsDialog, LabelsDialog, MdevDialog, MemoryDialog, MoveDiskDialog, NicEditDialog, PassthroughDialog, ShareFolderDialog, TuningDialog, VideoDialog, VirtioIsoDialog, WindowsToolingDialog  # noqa: F401
from .machine import CatalogDialog, CloneDetailsDialog, CloneDialog, ConnectionDialog, OsIconDialog, DeleteVmDialog, MigrateDialog, ModesDialog, ScheduleDialog, UsbRulesDialog, WakeScheduleDialog  # noqa: F401
from .network import NetworkDetailsDialog, NetworkDialog, NwFiltersDialog  # noqa: F401
from .snapshot import SnapshotDialog  # noqa: F401
from .storage import NewPoolDialog, PoolDialog, ResizeVolumeDialog, VolumeDialog, VolumePickerDialog  # noqa: F401

__all__ = [
    "TuningDialog",
    "OsIconDialog",
    "NetworkDetailsDialog",
    "PoolDialog",
    "CloneDetailsDialog",
    "NicEditDialog",
    "LabelsDialog",
    "HostdevOptionsDialog",
    "ChoiceDialog",
    "ConnectionDialog",
    "AttachDiskDialog",
    "AttachNicDialog",
    "BootOrderDialog",
    "CatalogDialog",
    "CloneDialog",
    "ConfirmDialog",
    "CpuDialog",
    "DeleteVmDialog",
    "DiskCacheDialog",
    "MdevDialog",
    "MoveDiskDialog",
    "DisplayFixDialog",
    "ErrorDialog",
    "HostDeviceDialog",
    "MemoryDialog",
    "MigrateDialog",
    "NetworkDialog",
    "NwFiltersDialog",
    "UsbRulesDialog",
    "NewPoolDialog",
    "PassthroughDialog",
    "ResizeVolumeDialog",
    "ScheduleDialog",
    "ShareFolderDialog",
    "SnapshotDialog",
    "VideoDialog",
    "VirtioIsoDialog",
    "VncPasswordDialog",
    "VolumeDialog",
    "VolumePickerDialog",
    "WakeScheduleDialog",
    "WindowsToolingDialog",
]
