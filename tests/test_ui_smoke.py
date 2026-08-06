"""Every dialog and page can be constructed.

This is the failure that has actually happened here, twice: moving modules into
packages broke lazily-imported dialogs, and neither compileall nor a boot
screenshot noticed, because nothing imports a dialog until you click the thing
that opens it.

The assertions are deliberately shallow. The point is that the constructor runs
at all, with plausible arguments, and that a stray import or a renamed helper
gets caught here rather than under a user's cursor.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import vmmanager.dialogs as dialogs

PROJECT = Path(__file__).resolve().parent.parent
from vmmanager.core.hooks import GpuHandoff, HookState
from vmmanager.core.models import (
    DiskInfo,
    DisplayHealth,
    DomainDisk,
    DomainSnapshot,
    Hardware,
    HostDevice,
    HostdevInfo,
    IommuDevice,
    MdevInfo,
    MdevType,
    IommuReport,
    NicInfo,
    PoolInfo,
    VolumeInfo,
)

HARDWARE = Hardware(
    machine="q35", firmware="UEFI", cpu_mode="host-passthrough", vcpus=4,
    memory_mb=4096, max_memory_mb=8192,
    disks=(), nics=(), hostdevs=(), filesystems=(), graphics=(),
    video="virtio", boot=("hd", "cdrom"), topology=(1, 2, 2),
)
DISK = DomainDisk(dev="vda", path="/pool/a.qcow2", capacity_gb=2.0)
VOLUME = VolumeInfo(name="disk.qcow2", path="/pool/disk.qcow2",
                    capacity=2 * 1024**3, allocation=1024**3, format="qcow2")
POOL = PoolInfo(name="default", active=True, autostart=True, capacity=10**12,
                allocation=10**11, available=9 * 10**11,
                path="/var/lib/libvirt/images", volumes=(VOLUME,))
TOPOLOGY = None  # filled in lazily: it needs a libvirt connection
IOMMU = IommuReport(enabled=True, devices=(
    IommuDevice(address="0000:03:00.0", group=13, label="GPU", driver="vfio-pci",
                is_bridge=False, attached_to=None),
))

# One set of arguments per dialog. Anything not listed here is a dialog added
# without a smoke test, which the last test in this file catches.
ARGS = {
    "AttachDiskDialog": ([POOL],),
    "AttachNicDialog": (["default"],),
    "BootOrderDialog": (["hd", "cdrom"],),
    "CatalogDialog": ([POOL],),
    "ChoiceDialog": ("Pick", "value", ["a", "b"]),
    "CloneDetailsDialog": ("base", [DISK]),
    "CloneDialog": ("base",),
    "ConfirmDialog": ("Delete", "Are you sure?", "Delete"),
    "ConnectionDialog": (),
    "CpuDialog": (HARDWARE, 16),
    "DeleteVmDialog": ("web-01", [DISK]),
    "DiskCacheDialog": ("vda", "none"),
    "DisplayFixDialog": ("win11", DisplayHealth(
        graphics=("spice",), video_model="vga", running=True)),
    "ErrorDialog": ("libvirt error", "something went wrong"),
    "GuestFeaturesDialog": "needs host capabilities, built in its own test below",
    "HostDeviceDialog": ([HostDevice(kind="usb", ident="1234:5678", label="A stick")],),
    "HostdevOptionsDialog": (HostdevInfo(kind="pci", ident="0000:03:00.0"),),
    "LabelsDialog": ("web server", "runs the site"),
    "MdevDialog": ([MdevType(parent="0000:00:02.0", type_id="i915-GVTg_V5_4",
                             name="GVTg_V5_4", api="vfio-pci", available=2)],
                   [MdevInfo(uuid="6a3c9dd2-0001-4b6e-9f1e-6f0c2f2b9d70",
                             parent="0000:00:02.0", type_id="i915-GVTg_V5_4",
                             attached_to=None)]),
    "MemoryDialog": (HARDWARE, 65536),
    "DiffDialog": ("current vs prod", "--- current\n+++ prod\n-<vcpu>2</vcpu>\n+<vcpu>4</vcpu>"),
    "ModesDialog": ("win11", [], False),
    "MoveDiskDialog": ("vda", [POOL], "/somewhere/else/a.qcow2", True),
    "MigrateDialog": ("web-01", ["qemu+ssh://host/system"]),
    "NameDialog": ("Rename the theme", "name", "midnight", "Rename"),
    "NetworkDetailsDialog": (),
    "NetworkDialog": (),
    "NewPoolDialog": (),
    # With the message the page shows on a connection that has no filter
    # support - real prose, so the sizing test measures a label that has to
    # wrap rather than an empty one. This is the case CI caught.
    "NwFiltersDialog": (
        [],
        "This connection has no network-filter support - the qemu system "
        "driver does. (this function is not supported by the connection "
        "driver: virConnectListAllNWFilters)",
    ),
    "NicEditDialog": (NicInfo(mac="52:54:00:11:22:33", source="default",
                              model="virtio"), ["default"]),
    "OsIconDialog": ("win11", "windows", ""),
    "PassthroughDialog": (IOMMU,),
    "PoolDialog": (),
    "ResizeVolumeDialog": ("disk.qcow2", 2.0),
    "ScheduleDialog": ("web-01", None),
    "ShareFolderDialog": (),
    "SingleGpuDialog": (
        "win11",
        [IommuDevice(address="0000:01:00.0", group=13, label="GPU",
                     driver="nvidia", is_bridge=False, attached_to=None)],
        GpuHandoff(vm_name="win11", addresses=("0000:01:00.0",),
                   driver="nvidia", modules=("nvidia",),
                   display_manager="sddm.service"),
        HookState(),
        True, True,
    ),
    "SnapshotDialog": ("web-01",),
    "TuningDialog": "needs the host topology, built in its own test below",
    "VideoDialog": ("virtio",),
    "VirtioIsoDialog": ("/usr/share/virtio-win/virtio-win.iso",
                        ["/var/lib/libvirt/images/virtio-win.iso"], [POOL]),
    "UsbRulesDialog": ("win11",
                       [HostDevice(kind="usb", ident="1234:5678",
                                   label="A stick")],
                       ["1234:5678", "aaaa:bbbb"]),
    "VncPasswordDialog": (),
    "VolumeDialog": (["default"],),
    "VolumePickerDialog": ([POOL],),
    "WakeScheduleDialog": ("web-01", None),
    "WindowsToolingDialog": ("win11", {"virtio_iso": None, "agent": False}),
}


# SizedDialog is the base every dialog inherits, not a dialog anyone opens.
NOT_A_DIALOG = {"SizedDialog"}


def dialog_names() -> list[str]:
    return sorted(
        n for n in dir(dialogs)
        if n.endswith("Dialog") and n not in NOT_A_DIALOG
    )


@pytest.mark.parametrize("name", dialog_names())
def test_dialog_constructs(qapp, name):
    cls = getattr(dialogs, name)
    args = ARGS.get(name)
    if isinstance(args, str):
        pytest.skip(args)
    if args is None:
        pytest.fail(f"{name} has no smoke-test arguments; add them to ARGS")
    widget = cls(None, *args)
    widget.show()
    qapp.processEvents()
    assert widget.windowTitle(), f"{name} has no window title"
    widget.close()


def test_every_dialog_is_covered():
    """A new dialog should not slip in untested."""
    missing = [n for n in dialog_names() if n not in ARGS]
    assert missing == [], f"dialogs with no smoke test: {missing}"


# -- pages


def snap(name: str = "web-01", **kwargs) -> DomainSnapshot:
    base = dict(uuid=f"uuid-{name}", name=name, state="shutoff", vcpus=2,
                memory_mb=2048, autostart=False)
    base.update(kwargs)
    return DomainSnapshot(**base)


def test_machines_page_takes_domains(qapp):
    from vmmanager.core.models import HostSnapshot
    from vmmanager.pages.machines import MachinesPage

    page = MachinesPage()
    page.show()
    host = HostSnapshot(hostname="h", hypervisor="QEMU", hypervisor_version="11.0.0",
                        cpus=16, memory_mb=65536, running=0, total=1)
    page.update_from([snap()], host)
    qapp.processEvents()
    assert page._cards


def test_templates_page_constructs(qapp):
    from vmmanager.pages.templates import TemplatesPage

    page = TemplatesPage()
    page.show()
    page.set_domains([snap("base", is_template=True)], ["default"])
    qapp.processEvents()


def test_settings_page_constructs(qapp):
    from vmmanager.pages.settings import SettingsPage

    page = SettingsPage()
    page.show()
    page.set_event_status(True)
    qapp.processEvents()


def test_detail_page_builds_every_tab(qapp):
    """Eleven tab mixins compose into one widget; all of them run here."""
    from vmmanager.pages.detail import DetailPage

    page = DetailPage()
    page.show()
    page.update_from(snap())
    qapp.processEvents()
    assert page.tabs.count() == 8
    for i in range(page.tabs.count()):
        page.tabs.setCurrentIndex(i)
        qapp.processEvents()
    page.shutdown()


def test_wizard_constructs_and_produces_a_spec(qapp):
    from vmmanager.wizard import NewVmDialog

    dialog = NewVmDialog(None, ["default"], [POOL], host_cpus=16, host_mem_mb=65536)
    dialog.show()
    dialog.name.setText("new-machine")
    qapp.processEvents()
    spec = dialog.spec()
    assert spec.name == "new-machine"
    assert spec.vcpus >= 1 and spec.memory_mb >= 128


def test_guest_features_dialog_constructs(qapp, testconn):
    """Offers only what the host reports, so it needs a real capabilities read."""
    from vmmanager.libvirt_service import (svc_feature_support, svc_get_features,
                                           svc_list_evdev)

    domain = testconn.lookupByName("test")
    widget = dialogs.GuestFeaturesDialog(
        None, "web-01", svc_get_features(domain.UUIDString()),
        svc_feature_support(), svc_list_evdev(),
    )
    widget.show()
    qapp.processEvents()
    assert widget.windowTitle()


def test_tuning_dialog_constructs(qapp, testconn):
    """Needs a host topology, so it gets the fake driver rather than a literal."""
    from vmmanager.core.tuning import Tuning
    from vmmanager.libvirt_service import svc_host_topology

    disk = DiskInfo(dev="vda", bus="virtio", source="/pool/a.qcow2",
                    format="qcow2", device="disk")
    widget = dialogs.TuningDialog(
        None, "web-01", 4, svc_host_topology(), Tuning(), (disk,)
    )
    widget.show()
    qapp.processEvents()
    assert widget.windowTitle()


def test_command_palette_constructs(qapp):
    from vmmanager.palette import CommandPalette

    palette = CommandPalette(None, [("Start web-01", "start"), ("Open Storage", "nav")])
    palette.show()
    qapp.processEvents()


def test_no_dialog_module_is_unreachable():
    """Every module under dialogs/ should be reachable from the package."""
    import pkgutil

    import vmmanager.dialogs as package

    for info in pkgutil.iter_modules(package.__path__):
        module = importlib_import(f"vmmanager.dialogs.{info.name}")
        assert module is not None


def importlib_import(name: str):
    import importlib

    return importlib.import_module(name)


def test_hardware_group_headings_are_banded_and_inert(qapp):
    """The headings sat in the same visual plane as the rows they separate."""
    from PySide6.QtCore import Qt

    from vmmanager import theme
    from vmmanager.pages.detail import DetailPage

    page = DetailPage()
    heading = page._hw_group("SYSTEM")
    assert heading.background(0).color().name() == theme.BG_INSET
    assert heading.background(1).color().name() == theme.BG_INSET
    assert heading.sizeHint(1).height() > 20, "a band needs room to read as one"
    assert not (heading.flags() & Qt.ItemFlag.ItemIsSelectable)


def test_hardware_rows_are_not_banded(qapp):
    """Only the headings; banding a row would undo the distinction."""
    from vmmanager import theme
    from vmmanager.pages.detail import DetailPage

    page = DetailPage()
    row = page._hw_item("cpu", "4 vcpu", None)
    assert row.background(0).color().name() != theme.BG_INSET


# ---------------------------------------------------------------- mode button


def test_the_mode_button_hides_when_there_are_none(qapp):
    from vmmanager.pages.detail import DetailPage

    page = DetailPage()
    page.show()
    page.set_modes([])
    assert not page._mode_btn.isVisible()


def test_the_mode_button_names_the_active_mode(qapp):
    from vmmanager.core.modes import Mode
    from vmmanager.pages.detail import DetailPage

    page = DetailPage()
    page.show()
    page.set_modes([Mode("debug", "", "", 0, active=True, matches=True),
                    Mode("prod", "", "", 0)])
    assert page._mode_btn.isVisible()
    assert "debug" in page._mode_btn.text()


def test_the_mode_button_marks_a_definition_that_has_drifted(qapp):
    from vmmanager.core.modes import Mode
    from vmmanager.pages.detail import DetailPage

    page = DetailPage()
    page.show()
    page.set_modes([Mode("debug", "", "", 0, active=True, matches=False)])
    assert "*" in page._mode_btn.text()
    assert "changed" in page._mode_btn.toolTip()


@pytest.mark.parametrize("state,switchable", [("shutoff", True), ("running", False)])
def test_the_mode_menu_only_offers_switching_when_the_machine_is_off(
    qapp, state, switchable
):
    """Built without showing it: exec on a popup menu blocks offscreen."""
    from vmmanager.core.modes import Mode
    from vmmanager.pages.detail import DetailPage

    page = DetailPage()
    page.uuid = "u1"
    page._snap = snap(state=state)
    page.set_modes([Mode("debug", "", "", 0, active=True, matches=True),
                    Mode("prod", "", "", 0)])
    menu = page._build_mode_menu()
    entries = {a.text(): a.isEnabled() for a in menu.actions() if a.text()}
    assert entries["prod"] is switchable
    assert entries["debug   (current)"] is False, "already there"
    assert entries["Manage modes…"] is True


# ---------------------------------------------------------------- custom icon


def test_a_picked_file_wins_over_the_grid(qapp):
    from vmmanager.dialogs import OsIconDialog

    icon = str(PROJECT / "vmmanager" / "assets" / "icon.svg")
    dialog = OsIconDialog(None, "win11", "windows", "")
    dialog.auto.setChecked(False)
    assert dialog.chosen_key() == "windows"

    dialog._custom = icon
    dialog._refresh_state()
    assert dialog.chosen_key() == icon
    assert not dialog.grid.isEnabled(), "the grid should not compete with a file"

    dialog._drop_file()
    assert dialog.chosen_key() == "windows"


def test_reopening_on_a_custom_icon_keeps_it(qapp):
    from vmmanager.dialogs import OsIconDialog

    icon = str(PROJECT / "vmmanager" / "assets" / "icon.svg")
    dialog = OsIconDialog(None, "win11", "windows", icon)
    assert dialog.chosen_key() == icon


def test_auto_detect_still_beats_everything(qapp):
    from vmmanager.dialogs import OsIconDialog

    icon = str(PROJECT / "vmmanager" / "assets" / "icon.svg")
    dialog = OsIconDialog(None, "win11", "windows", icon)
    dialog.auto.setChecked(True)
    assert dialog.chosen_key() == ""


def test_themes_page_constructs(qapp):
    from vmmanager.pages.themes import ThemesPage

    page = ThemesPage()
    page.show()
    qapp.processEvents()
    assert page.list.count() >= 1, "the shipped theme should always be listed"


def test_every_sidebar_entry_goes_somewhere(qapp, testconn):
    """A nav button with no page behind it silently does nothing."""
    from vmmanager.main_window import MainWindow
    from vmmanager.widgets import Sidebar

    window = MainWindow()
    try:
        for label in Sidebar.NAV:
            window._navigate(label)
            qapp.processEvents()
            assert window.sidebar._buttons[label].property("active") == "true", (
                f"'{label}' is in the sidebar but did not navigate anywhere"
            )
    finally:
        window.worker.stop()
        window.detail.shutdown()


# -- right-click a hardware row to remove it


def hardware_page(qapp, testconn):
    """A detail page showing the fake driver's machine, hardware fully loaded.

    Waiting for the reads to finish matters: show_domain starts them on the
    thread pool, and when one lands it rebuilds the tree from scratch. A test
    holding a row from before that would be holding a replaced object - which
    failed about one run in three before this wait was here.
    """
    from PySide6.QtCore import QThreadPool

    from vmmanager.pages.detail import DetailPage

    page = DetailPage()
    page.show()
    domain = testconn.lookupByName("test")
    page.show_domain(snap("test", uuid=domain.UUIDString()))
    page.tabs.setCurrentIndex(page.TAB_HARDWARE)
    QThreadPool.globalInstance().waitForDone(10_000)
    for _ in range(3):  # the results arrive by queued signal
        qapp.processEvents()
    return page


@pytest.mark.parametrize("kind,payload,expected", [
    ("disk", None, "Remove disk"),
    ("cdrom", None, "Remove disc drive"),
    ("nic", None, "Remove network interface"),
    ("usb", None, "Remove USB device"),
    ("pci", None, "Remove PCI device"),
    ("fs", None, "Remove shared folder"),
    ("sound", "ich9", "Remove sound device"),
    ("input", ("tablet", "usb"), "Remove input device"),
    ("watchdog", ("itco", "reset"), "Remove watchdog"),
    ("redir", 0, "Remove USB redirection"),
    ("vsock", 3, "Remove vsock"),
    ("panic", "isa", "Remove panic notifier"),
    ("smartcard", "passthrough", "Remove smartcard"),
    ("dimm", 1024, "Remove memory device"),
    ("audio", "none", "Remove audio device"),
    # A machine can hold a VNC and a SPICE display at once, and which one is
    # taken off decides which protocol the console uses - so the row carries
    # the type and port, and the remover is told which to take.
    ("gfx", ("vnc", "-1"), "Remove display"),
    ("video", None, "Remove video adapter"),
])
def test_a_removable_row_offers_to_remove_it(qapp, testconn, kind, payload,
                                             expected):
    page = hardware_page(qapp, testconn)
    try:
        menu = page._build_hw_menu(kind, payload)
        labels = [a.text() for a in menu.actions()]
        assert labels == [expected]
        assert menu.actions()[0].isEnabled()
    finally:
        page.shutdown()


@pytest.mark.parametrize("kind", [
    "cpu", "mem", "boot", "labels", "tune", "features",
    "controller", "ports",
])
def test_a_row_that_is_not_a_device_says_so(qapp, testconn, kind):
    """A property of the machine, not a device: removing it means nothing.

    A menu that says so beats one that appears empty, or none at all - which
    reads as a broken right-click.
    """
    page = hardware_page(qapp, testconn)
    try:
        menu = page._build_hw_menu(kind, None)
        assert [a.text() for a in menu.actions()] == [
            "Nothing to remove on this row"
        ]
        assert not menu.actions()[0].isEnabled()
    finally:
        page.shutdown()


def test_every_removable_kind_has_a_name_and_a_remover(qapp, testconn):
    """The table and the dispatch have to agree, or a row offers 'Remove device'
    or offers nothing at all."""
    from vmmanager.pages.detail.hardware import HardwareMixin

    page = hardware_page(qapp, testconn)
    try:
        payloads = {"input": ("tablet", "usb"), "sound": "ich9",
                    "gfx": ("vnc", "-1")}
        for kind in HardwareMixin.HW_REMOVABLE:
            assert page._hw_remover(kind, payloads.get(kind)) is not None, (
                f"{kind} is listed as removable but nothing removes it"
            )
    finally:
        page.shutdown()


def test_right_clicking_selects_the_row_it_was_aimed_at(qapp, testconn):
    """The disk and NIC removers read the selection, so the click has to move it
    or a right-click removes whatever was selected before.

    Works on items rather than pixel positions: an earlier version aimed at
    coordinates, passed on its own, and failed in the middle of the suite where
    the tree had been laid out differently.
    """
    from PySide6.QtCore import Qt

    page = hardware_page(qapp, testconn)
    try:
        tree = page.hw_tree
        rows = [
            tree.topLevelItem(i).child(j)
            for i in range(tree.topLevelItemCount())
            for j in range(tree.topLevelItem(i).childCount())
        ]
        assert len(rows) >= 2, "need two rows to tell selection apart"
        tree.setCurrentItem(rows[0])
        target = rows[-1]

        aimed = page._aim_at_hw_item(target)
        qapp.processEvents()

        assert aimed is target
        assert tree.currentItem() is target
        assert page._selected_device() == target.data(0, Qt.ItemDataRole.UserRole)
    finally:
        page.shutdown()


def test_right_clicking_a_heading_or_empty_space_opens_nothing(qapp, testconn):
    """A group heading carries no device, and None is what Qt returns for a
    click below the last row. Either way there is nothing to open a menu for."""
    from PySide6.QtCore import Qt

    page = hardware_page(qapp, testconn)
    try:
        heading = page.hw_tree.topLevelItem(0)
        assert heading.data(0, Qt.ItemDataRole.UserRole) is None, (
            "the first top-level item should be a group heading"
        )
        assert page._aim_at_hw_item(heading) is None
        assert page._aim_at_hw_item(None) is None
    finally:
        page.shutdown()


def test_the_error_banner_can_actually_be_shown(qapp):
    """The path that reports a failed action must not fail itself.

    It used to: the banner interpolates theme.DANGER, machines.py never
    imported theme, and the NameError replaced the message with a crash
    dialog - so a machine that would not resume said nothing about why.
    """
    from vmmanager.pages.machines import MachinesPage

    page = MachinesPage()
    page.show()
    page.show_action_error("domain is pmsuspended")
    qapp.processEvents()
    page.show_error("could not reach libvirt")
    qapp.processEvents()
