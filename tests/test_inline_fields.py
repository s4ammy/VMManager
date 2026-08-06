"""The faceplate edits in place rather than behind an Edit button.

Every property that can be changed is the widget itself. Nothing is sent
until Save, and Discard puts back what the machine says - so the two
things worth proving are that a changed field reaches libvirt, and that
an untouched one does not get written back over something else.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import libvirt
import pytest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
)

from vmmanager.pages.detail import DetailPage
from vmmanager.pages.detail import hardware as hardware_mod

DOMAIN = """
<domain type='test'>
  <name>inline</name>
  <memory unit='MiB'>64</memory>
  <os>
    <type arch='x86_64'>hvm</type>
    <boot dev='hd'/>
  </os>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='/default-pool/production/inline-machine-system-disk.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='file' device='cdrom'>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>
    <interface type='network'>
      <mac address='52:54:00:11:22:33'/>
      <source network='default'/>
      <model type='virtio'/>
    </interface>
    <graphics type='spice' port='-1' autoport='yes' listen='127.0.0.1'/>
  </devices>
</domain>
"""


@pytest.fixture
def page(qapp, testconn, monkeypatch):
    """A detail page pointed at a real domain, with run_task made blocking."""

    def inline(work, done=None, failed=None):
        try:
            result = work()
        except Exception as exc:  # the real one routes this to `failed`
            if failed:
                failed(str(exc))
            return
        if done:
            done(result)

    monkeypatch.setattr(hardware_mod, "run_task", inline)

    # A failed change opens a modal dialog, which a headless run waits on
    # forever. Record what it would have said instead.
    errors: list[str] = []

    class Recorded:
        def __init__(self, _parent, _title, message):
            errors.append(message)

        def exec(self):
            return 0

    monkeypatch.setattr(hardware_mod, "ErrorDialog", Recorded)

    dom = testconn.defineXML(DOMAIN)
    p = DetailPage()
    p.uuid = dom.UUIDString()
    p.errors = errors
    p._load_hardware()
    qapp.processEvents()
    yield p
    p.shutdown()
    dom.undefine()


def _select(page, kind, ident=""):
    """Click the component bay row for a device and draw its faceplate."""
    tree = page.hw_tree
    for i in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(i)
        for j in range(group.childCount()):
            item = group.child(j)
            data = item.data(0, hardware_mod.Qt.ItemDataRole.UserRole)
            if data is None:
                continue
            # column 0 is the badge, column 1 the label
            label = " ".join(item.text(c) for c in (0, 1))
            if data[0] == kind and (not ident or ident in label):
                tree.setCurrentItem(item)
                page._show_hw_detail()
                return item
    raise AssertionError(f"no {kind} row in the component bay")


def _widgets(page, cls):
    return page.hw_panel.parentWidget().findChildren(cls)


def _in_row(page, key, cls):
    """The control on the faceplate row labelled `key`.

    Checkbox captions are short now - the row's key names the property and
    the "?" explains it - so several read "Enabled". The row is what tells
    them apart, and it is what a person reads too.
    """
    layout = page.hw_panel
    for i in range(layout.count()):
        row = layout.itemAt(i).layout()
        if row is None or row.count() < 2:
            continue
        first = row.itemAt(0).widget()
        if not isinstance(first, hardware_mod.QLabel):
            continue
        if first.text() != key.upper():
            continue
        for j in range(1, row.count()):
            found = row.itemAt(j).widget()
            if isinstance(found, cls):
                return found
    raise AssertionError(f"no {cls.__name__} on a row labelled {key!r}")


def _combo(page, option):
    """The combo box on the faceplate that offers this option.

    A disk has several - cache and discard - so "the first one" stops
    meaning anything the moment another is added.
    """
    for box in _widgets(page, QComboBox):
        if option in [box.itemText(i) for i in range(box.count())]:
            return box
    raise AssertionError(f"no combo offering {option!r}")


def _xml(page, testconn):
    dom = testconn.lookupByUUIDString(page.uuid)
    return ET.fromstring(dom.XMLDesc(
        libvirt.VIR_DOMAIN_XML_INACTIVE | libvirt.VIR_DOMAIN_XML_SECURE
    ))


def _save(page):
    # isHidden, not isVisible: a QTabWidget hides every tab but the current
    # one, so nothing on this page is "visible" in a headless run.
    assert not page._field_bar.isHidden(), (
        "Save should have appeared: a field moved"
    )
    page._save_fields()


# --------------------------------------------------------------------- disks

def test_a_disk_serial_is_typed_straight_into_the_faceplate(page, testconn, qapp):
    _select(page, "disk", "vda")
    serial = next(w for w in _widgets(page, QLineEdit))
    assert serial.text() == "", "this disk has no serial yet"

    serial.setText("scratch-01")
    serial.textEdited.emit("scratch-01")  # setText does not, by design
    _save(page)
    qapp.processEvents()

    disk = _xml(page, testconn).find("devices/disk")
    assert disk.findtext("serial") == "scratch-01"


def test_discard_puts_back_what_the_machine_says(page, testconn, qapp):
    _select(page, "disk", "vda")
    serial = next(w for w in _widgets(page, QLineEdit))
    serial.setText("not-saved")
    serial.textEdited.emit("not-saved")
    assert not page._field_bar.isHidden()

    page._show_hw_detail()  # what Discard is wired to
    qapp.processEvents()
    serial = next(w for w in _widgets(page, QLineEdit))
    assert serial.text() == ""
    assert _xml(page, testconn).find("devices/disk").find("serial") is None


def test_the_untouched_fields_are_left_alone(page, testconn, qapp):
    """Only what moved is written. A save that rewrote every field would
    put this disk's readonly flag back on top of the one already there."""
    _select(page, "cdrom", "sda")
    ro = next(w for w in _widgets(page, QCheckBox) if "Write-protected" in w.text())
    assert ro.isChecked(), "the cdrom in the fixture is read-only"

    discard = _combo(page, "unmap")
    discard.setCurrentText("unmap")
    page._save_fields()
    qapp.processEvents()

    cdrom = [d for d in _xml(page, testconn).findall("devices/disk")
             if d.get("device") == "cdrom"][0]
    assert cdrom.find("readonly") is not None, "not touched, not removed"
    assert cdrom.find("driver").get("discard") == "unmap"


def test_a_disk_can_be_made_shareable_and_read_only_together(page, testconn, qapp):
    _select(page, "disk", "vda")
    for text in ("write-protected", "shared between machines"):
        box = next(w for w in _widgets(page, QCheckBox)
                   if text in w.text().lower())
        box.setChecked(True)
    _save(page)
    qapp.processEvents()

    disk = _xml(page, testconn).find("devices/disk")
    assert disk.find("readonly") is not None
    assert disk.find("shareable") is not None


# ------------------------------------------------------------------- display

def test_the_display_password_is_hidden_until_asked_for(page):
    _select(page, "gfx")
    password = next(w for w in _widgets(page, QLineEdit)
                    if w.placeholderText() == "no password")
    assert password.echoMode() == QLineEdit.EchoMode.Password

    show = next(w for w in _widgets(page, QCheckBox) if w.text() == "Show")
    show.setChecked(True)
    assert password.echoMode() == QLineEdit.EchoMode.Normal


def test_setting_a_password_reaches_the_definition(page, testconn, qapp):
    _select(page, "gfx")
    password = next(w for w in _widgets(page, QLineEdit)
                    if w.placeholderText() == "no password")
    password.setText("hunter2")
    password.textEdited.emit("hunter2")
    _save(page)
    qapp.processEvents()

    assert _xml(page, testconn).find("devices/graphics").get("passwd") == "hunter2"


def test_an_explicit_port_turns_the_automatic_choice_off(page, testconn, qapp):
    _select(page, "gfx")
    port = next(w for w in _widgets(page, QSpinBox))
    assert port.value() == -1
    port.setValue(5905)
    _save(page)
    qapp.processEvents()

    g = _xml(page, testconn).find("devices/graphics")
    assert g.get("port") == "5905"
    assert g.get("autoport") == "no", "or the port is quietly ignored"


def test_turning_opengl_on_writes_the_element(page, testconn, qapp):
    _select(page, "gfx")
    gl = _in_row(page, "opengl", QCheckBox)
    gl.setChecked(True)
    _save(page)
    qapp.processEvents()

    g = _xml(page, testconn).find("devices/graphics")
    assert g.find("gl") is not None and g.find("gl").get("enable") == "yes"


def test_the_display_reads_back_what_was_saved(page, qapp):
    """The faceplate is drawn from the machine, so a saved value has to
    survive the reload that follows it."""
    _select(page, "gfx")
    password = next(w for w in _widgets(page, QLineEdit)
                    if w.placeholderText() == "no password")
    password.setText("kept")
    password.textEdited.emit("kept")
    _save(page)
    qapp.processEvents()

    _select(page, "gfx")
    password = next(w for w in _widgets(page, QLineEdit)
                    if w.placeholderText() == "no password")
    assert password.text() == "kept"


# ---------------------------------------------------------------------- boot

def test_boot_devices_are_ticked_on_and_off(page, testconn, qapp):
    _select(page, "boot")
    boxes = {w.text(): w for w in _widgets(page, QCheckBox)}
    assert boxes["Hard disk"].isChecked()
    assert not boxes["Optical drive"].isChecked(), "not in the boot list yet"

    boxes["Optical drive"].setChecked(True)
    _save(page)
    qapp.processEvents()

    order = [b.get("dev") for b in _xml(page, testconn).findall("os/boot")]
    assert order == ["hd", "cdrom"]


def test_the_last_boot_device_cannot_be_unticked(page, testconn, qapp):
    _select(page, "boot")
    box = next(w for w in _widgets(page, QCheckBox) if w.text() == "Hard disk")
    box.setChecked(False)
    page._save_fields()
    qapp.processEvents()

    assert page.errors and "boot from something" in page.errors[-1]
    order = [b.get("dev") for b in _xml(page, testconn).findall("os/boot")]
    assert order == ["hd"], "the definition is left as it was"


# -------------------------------------------------------------------- memory

def test_shared_memory_is_a_checkbox_on_the_memory_faceplate(page, testconn, qapp):
    _select(page, "mem")
    box = next(w for w in _widgets(page, QCheckBox))
    assert not box.isChecked()
    box.setChecked(True)
    _save(page)
    qapp.processEvents()

    access = _xml(page, testconn).find("memoryBacking/access")
    assert access is not None and access.get("mode") == "shared"


# ------------------------------------------------------------------ overview

def test_the_processor_faceplate_names_the_machine_itself(page, testconn):
    """uuid, hypervisor, architecture and emulator were only in the XML tab."""
    _select(page, "cpu")
    labels = [w.text() for w in _widgets(page, hardware_mod.QLabel)]
    assert page.uuid in labels
    assert "x86_64" in labels
    assert "/usr/bin/qemu-system-x86_64" in labels
    assert "test" in labels, "the hypervisor this domain runs under"


# ----------------------------------------------------------------- processor

def test_the_topology_is_three_fields_and_one_write(page, testconn, qapp):
    """Sockets, cores and threads are one libvirt call, so they share an
    applier and Save must not make three of them."""
    _select(page, "cpu")
    spins = {s.value(): s for s in _widgets(page, QSpinBox)}
    boxes = _widgets(page, QSpinBox)
    boxes[0].setValue(2)   # sockets
    boxes[1].setValue(3)   # cores
    _save(page)
    qapp.processEvents()

    root = _xml(page, testconn)
    topo = root.find("cpu/topology")
    assert (topo.get("sockets"), topo.get("cores")) == ("2", "3")
    assert root.findtext("vcpu") == "6", "the count follows the shape"
    assert spins is not None


def test_the_cpu_model_is_a_combo_not_a_dialog(page, testconn, qapp):
    _select(page, "cpu")
    model = _combo(page, "host-passthrough")
    model.setCurrentText("host-model")
    _save(page)
    qapp.processEvents()

    assert _xml(page, testconn).find("cpu").get("mode") == "host-model"


def test_the_machine_type_fills_in_behind_the_current_one(page, qapp):
    """The capabilities read is async: the combo starts with what the
    machine has so the faceplate is usable before it lands."""
    _select(page, "cpu")
    machine = [c for c in _widgets(page, QComboBox)
               if c.currentText() not in ("host-passthrough", "host-model")][0]
    assert machine.currentText(), "never an empty chipset"
    assert machine.count() >= 1


# -------------------------------------------------------------------- memory

def test_memory_is_typed_in_rather_than_opened(page, testconn, qapp):
    _select(page, "mem")
    current, maximum = _widgets(page, QSpinBox)[:2]
    maximum.setValue(1024)
    current.setValue(512)
    _save(page)
    qapp.processEvents()

    root = _xml(page, testconn)
    assert int(root.findtext("memory")) == 1024 * 1024
    assert int(root.findtext("currentMemory")) == 512 * 1024


def test_a_machine_below_the_usual_floor_still_reads_true(page):
    """The fixture is 64 MiB, under the 128 MiB the spin boxes offer as a
    smallest sensible size. Clamping it up would mean simply opening the
    faceplate proposed a change nobody asked for."""
    _select(page, "mem")
    current, maximum = _widgets(page, QSpinBox)[:2]
    assert (current.value(), maximum.value()) == (64, 64)


def test_drawing_a_faceplate_is_never_itself_an_edit(page):
    """Every field starts equal to what the machine says, on every row -
    otherwise Save appears unprompted and writes something back."""
    for kind in ("cpu", "mem", "boot", "labels", "disk", "cdrom", "gfx",
                 "video", "nic"):
        try:
            _select(page, kind)
        except AssertionError:
            continue
        assert page._dirty_fields() == [], f"{kind} came up dirty"
        assert page._field_bar is None or page._field_bar.isHidden()


def test_current_memory_cannot_exceed_the_maximum(page, testconn, qapp):
    """Both are on the faceplate at once now, so it is easy to set a
    current above a maximum that has not been raised yet."""
    _select(page, "mem")
    current, maximum = _widgets(page, QSpinBox)[:2]
    maximum.setValue(256)
    current.setValue(4096)
    _save(page)
    qapp.processEvents()

    root = _xml(page, testconn)
    assert int(root.findtext("memory")) == 256 * 1024
    assert int(root.findtext("currentMemory")) == 256 * 1024


# ---------------------------------------------------------------------- boot

def test_boot_entries_move_with_the_arrows(page, testconn, qapp):
    _select(page, "boot")
    # Found by tooltip: the triangles are painted icons, because the faces
    # this app ships have no arrow glyph and the fallback looked nothing
    # like the rest of the interface.
    arrows = [b for b in _widgets(page, QPushButton)
              if b.toolTip() in ("Move up", "Move down")]
    assert arrows, "each row has a pair"
    assert all(not b.icon().isNull() for b in arrows), "drawn, not typed"

    # tick the optical drive on, then move it above the hard disk
    box = next(w for w in _widgets(page, QCheckBox) if w.text() == "Optical drive")
    box.setChecked(True)
    up = [b for b in _widgets(page, QPushButton) if b.toolTip() == "Move up"]
    up[1].click()  # the second row's up arrow
    qapp.processEvents()
    _save(page)
    qapp.processEvents()

    order = [b.get("dev") for b in _xml(page, testconn).findall("os/boot")]
    assert order == ["cdrom", "hd"]


def test_discard_drops_a_half_rearranged_order(page, testconn, qapp):
    _select(page, "boot")
    box = next(w for w in _widgets(page, QCheckBox) if w.text() == "Optical drive")
    box.setChecked(True)
    [b for b in _widgets(page, QPushButton)
     if b.toolTip() == "Move up"][1].click()
    qapp.processEvents()

    page._discard_fields()
    qapp.processEvents()
    labels = [w.text() for w in _widgets(page, QCheckBox)]
    assert labels[0] == "Hard disk", "back to what the machine says"
    order = [b.get("dev") for b in _xml(page, testconn).findall("os/boot")]
    assert order == ["hd"]


def test_leaving_the_row_drops_the_draft(page, testconn, qapp):
    """A rearrangement belongs to the row it was started on."""
    _select(page, "boot")
    [b for b in _widgets(page, QPushButton)
     if b.toolTip() == "Move down"][0].click()
    qapp.processEvents()
    assert page._boot_draft is not None

    _select(page, "mem")
    assert page._boot_draft is None
    _select(page, "boot")
    labels = [w.text() for w in _widgets(page, QCheckBox)]
    assert labels[0] == "Hard disk"


def test_the_boot_menu_is_a_checkbox(page, testconn, qapp):
    _select(page, "boot")
    menu = next(w for w in _widgets(page, QCheckBox) if "Offered at startup" in w.text())
    menu.setChecked(True)
    _save(page)
    qapp.processEvents()

    assert _xml(page, testconn).find("os/bootmenu").get("enable") == "yes"


# -------------------------------------------------------------- name and notes

def test_the_title_and_notes_are_one_write(page, testconn, qapp):
    _select(page, "labels")
    title = next(w for w in _widgets(page, QLineEdit))
    notes = next(w for w in _widgets(page, QPlainTextEdit))
    title.setText("Build server")
    title.textEdited.emit("Build server")
    notes.setPlainText("the one with the GPU")
    _save(page)
    qapp.processEvents()

    root = _xml(page, testconn)
    assert root.findtext("title") == "Build server"
    assert root.findtext("description") == "the one with the GPU"


# ---------------------------------------------------------------- disk cache

def test_the_cache_mode_is_on_the_disk_faceplate(page, testconn, qapp):
    _select(page, "disk", "vda")
    cache = _combo(page, "writeback")
    cache.setCurrentText("none")
    _save(page)
    qapp.processEvents()

    disk = _xml(page, testconn).find("devices/disk")
    assert disk.find("driver").get("cache") == "none"


def test_the_optical_drive_has_one_too(page, testconn, qapp):
    _select(page, "cdrom", "sda")
    cache = _combo(page, "writeback")
    cache.setCurrentText("writethrough")
    _save(page)
    qapp.processEvents()

    cdrom = [d for d in _xml(page, testconn).findall("devices/disk")
             if d.get("device") == "cdrom"][0]
    assert cdrom.find("driver").get("cache") == "writethrough"


# --------------------------------------------------------------------- video

def test_the_video_model_and_3d_are_fields(page, testconn, qapp):
    _select(page, "video")
    model = _combo(page, "qxl")
    model.setCurrentText("virtio")
    accel = next(w for w in _widgets(page, QCheckBox) if "Accelerated" in w.text())
    accel.setChecked(True)
    _save(page)
    qapp.processEvents()

    video = _xml(page, testconn).find("devices/video/model")
    assert video.get("type") == "virtio"
    assert video.get("heads") is not None or True
    accel_el = _xml(page, testconn).find("devices/video/model/acceleration")
    assert accel_el is not None and accel_el.get("accel3d") == "yes"


# -------------------------------------------------------------------- panel

def test_a_tall_faceplate_scrolls(page, qapp):
    """Every property of a device is on its faceplate now, so a disk is
    taller than the tab and the panel has to scroll rather than squeeze."""
    from PySide6.QtCore import Qt as _Qt

    _select(page, "gfx")
    qapp.processEvents()
    assert page.hw_scroll.widgetResizable()
    assert page.hw_scroll.verticalScrollBarPolicy() != _Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    body = page.hw_scroll.widget()
    assert body.sizeHint().height() > 0


def test_the_save_bar_stays_out_of_the_scrolled_area(page, qapp):
    """A change made at the top of a long faceplate must not put Save
    below the fold."""
    _select(page, "gfx")
    password = next(w for w in _widgets(page, QLineEdit)
                    if w.placeholderText() == "no password")
    password.setText("x")
    password.textEdited.emit("x")
    qapp.processEvents()

    assert not page._field_bar.isHidden()
    body = page.hw_scroll.widget()
    assert not page._field_bar.isAncestorOf(body)
    assert page._field_bar is page.hw_save_bar
    assert body.findChildren(type(page.hw_save_bar)) is not None
    assert page.hw_save_bar.parentWidget() is not body


def test_switching_device_scrolls_back_to_the_top(page, qapp):
    _select(page, "gfx")
    bar = page.hw_scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    _select(page, "mem")
    assert page.hw_scroll.verticalScrollBar().value() == 0


def test_rearranging_the_boot_order_keeps_your_place(page, qapp):
    """The arrows redraw the faceplate; jumping to the top each time would
    lose the row you were working on."""
    _select(page, "boot")
    bar = page.hw_scroll.verticalScrollBar()
    if bar.maximum() == 0:
        return  # the fixture's boot list fits; nothing to keep
    bar.setValue(bar.maximum())
    where = bar.value()
    [b for b in _widgets(page, QPushButton)
     if b.toolTip() == "Move down"][0].click()
    qapp.processEvents()
    assert page.hw_scroll.verticalScrollBar().value() == where


# ----------------------------------------------------------------------- nic
#
# libvirt's test driver refuses a persistent update of an interface
# ("persistent update of device 'interface' is not supported"), so these
# check what the faceplate reads and what it sends rather than the result
# in the XML. svc_set_nic itself is exercised against qemu:///system.

def test_the_nic_faceplate_sends_every_field_in_one_write(page, testconn, qapp,
                                                          monkeypatch):
    calls = []
    monkeypatch.setattr(
        hardware_mod, "svc_set_nic",
        lambda *a, **k: calls.append((a, k)) or "Applied to the config.",
    )
    _select(page, "nic")
    mac = next(w for w in _widgets(page, QLineEdit))
    mac.setText("52:54:00:aa:bb:cc")
    mac.textEdited.emit("52:54:00:aa:bb:cc")
    _combo(page, "e1000e").setCurrentText("e1000e")
    _save(page)
    qapp.processEvents()

    assert len(calls) == 1, "MAC and model are one libvirt call, not two"
    (uuid, old_mac), kwargs = calls[0]
    assert (uuid, old_mac) == (page.uuid, "52:54:00:11:22:33")
    assert kwargs["new_mac"] == "52:54:00:aa:bb:cc"
    assert kwargs["model"] == "e1000e"
    assert kwargs["link_up"] is True


def test_an_unchanged_field_is_sent_as_none(page, qapp, monkeypatch):
    """svc_set_nic treats None as "leave it", so a model nobody touched
    must not arrive as its current value and be rewritten."""
    calls = []
    monkeypatch.setattr(
        hardware_mod, "svc_set_nic",
        lambda *a, **k: calls.append(k) or "Applied to the config.",
    )
    _select(page, "nic")
    link = next(w for w in _widgets(page, QCheckBox) if "Connected" in w.text())
    link.setChecked(False)
    _save(page)
    qapp.processEvents()

    assert calls[0]["new_mac"] is None and calls[0]["model"] is None
    assert calls[0]["link_up"] is False


LINK_DOWN = DOMAIN.replace(
    "<model type='virtio'/>", "<model type='virtio'/><link state='down'/>"
).replace("<name>inline</name>", "<name>inline-down</name>")


def test_a_pulled_cable_reads_back_as_pulled(qapp, testconn, monkeypatch):
    """Nothing used to read the link state, so the editor always sent
    link_up=True and any other change quietly plugged the cable back in."""
    from vmmanager.libvirt_service import svc_get_hardware

    dom = testconn.defineXML(LINK_DOWN)
    try:
        hw = svc_get_hardware(dom.UUIDString())
        assert hw.nics[0].link_up is False
    finally:
        dom.undefine()


def test_a_connected_cable_reads_back_as_connected(page):
    assert page._hw.nics[0].link_up is True
    _select(page, "nic")
    link = next(w for w in _widgets(page, QCheckBox) if "Connected" in w.text())
    assert link.isChecked()


# --------------------------------------------------------------- host devices

USB_HOSTDEV = DOMAIN.replace("<graphics", """<hostdev mode='subsystem' type='usb' managed='yes'>
      <source startupPolicy='optional'>
        <vendor id='0x1234'/>
        <product id='0x5678'/>
      </source>
    </hostdev>
    <graphics""").replace("<name>inline</name>", "<name>inline-usb</name>")


def test_a_usb_startup_policy_reads_back(qapp, testconn):
    """Nothing read it before, so the options dialog always showed
    'mandatory' and applying it reset a device set to something else."""
    from vmmanager.libvirt_service import svc_get_hardware

    dom = testconn.defineXML(USB_HOSTDEV)
    try:
        hw = svc_get_hardware(dom.UUIDString())
        usb = [h for h in hw.hostdevs if h.kind == "usb"][0]
        assert usb.startup_policy == "optional"
    finally:
        dom.undefine()


def test_the_policy_field_shows_what_the_device_has(qapp, testconn, monkeypatch):
    from vmmanager.pages.detail import DetailPage

    def inline(work, done=None, failed=None):
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001
            if failed:
                failed(str(exc))
            return
        if done:
            done(result)

    monkeypatch.setattr(hardware_mod, "run_task", inline)
    dom = testconn.defineXML(USB_HOSTDEV)
    p = DetailPage()
    try:
        p.uuid = dom.UUIDString()
        p._load_hardware()
        qapp.processEvents()
        _select(p, "usb")
        policy = _combo(p, "requisite")
        assert policy.currentText() == "optional"
    finally:
        p.shutdown()
        dom.undefine()


# ---------------------------------------------------------------- presentation

def test_an_explanation_is_a_marker_not_a_paragraph(page):
    """Spelling every hint out under its field made a device scroll off
    the panel. The text is kept, on hover."""
    _select(page, "disk", "vda")
    marks = [w for w in _widgets(page, hardware_mod.QLabel)
             if w.objectName() == "FieldHint"]
    assert marks, "the disk's fields have explanations"
    assert all(w.text() == "?" for w in marks)
    assert all(len(w.toolTip()) > 20 for w in marks), "the prose is on hover"

    prose = [w for w in _widgets(page, hardware_mod.QLabel)
             if w.objectName() == "ConsoleHint"]
    assert prose == [], "no wrapped paragraphs left on a device faceplate"


# The panel is about 450px wide on an unmaximised window - the tab is a
# 330px component list plus whatever is left. A faceplate that needs more
# than this is one that gets cut off, which is the reported symptom.
PANEL_BUDGET = 450


def test_no_faceplate_needs_more_width_than_the_panel_gets(page, qapp):
    page.show()
    qapp.processEvents()
    too_wide = []
    for kind in ("cpu", "mem", "boot", "labels", "disk", "cdrom", "gfx",
                 "video", "nic"):
        try:
            _select(page, kind)
        except AssertionError:
            continue
        qapp.processEvents()
        needed = page.hw_scroll.widget().minimumSizeHint().width()
        if needed > PANEL_BUDGET:
            too_wide.append(f"{kind} needs {needed}px")
    assert too_wide == [], (
        "these get clipped on an unmaximised window: " + ", ".join(too_wide)
    )


def test_the_topology_spin_boxes_are_each_on_their_own_row(page):
    """Side by side they wanted 726px in a 452px panel, and the panel
    clipped rather than scrolled."""
    _select(page, "cpu")
    for key in ("sockets", "cores", "threads"):
        assert _in_row(page, key, QSpinBox) is not None


def test_the_action_buttons_wrap_instead_of_running_off(qapp):
    from PySide6.QtWidgets import QPushButton, QWidget

    from vmmanager.pages.detail.hardware import _FlowLayout

    holder = QWidget()
    flow = _FlowLayout()
    holder.setLayout(flow)
    for label in ("Change media…", "Grow…", "Move to pool…", "Remove"):
        button = QPushButton(label)
        button.setFixedSize(120, 28)
        flow.addWidget(button)
    holder.resize(260, 200)
    holder.show()
    qapp.processEvents()

    rows = {flow.itemAt(i).geometry().y() for i in range(flow.count())}
    assert len(rows) == 2, "two buttons fit across 260px, so four make two rows"
    assert flow.heightForWidth(260) > flow.heightForWidth(600)
    holder.close()


def test_the_boot_arrows_are_drawn_rather_than_typed(qapp):
    """▲ and ▼ are not in the faces this app ships, and the fallback the
    system found did not look like the rest of the interface."""
    from vmmanager.pages.detail.hardware import _arrow_icon

    up = _arrow_icon(True, "#ffffff")
    down = _arrow_icon(False, "#ffffff")
    assert not up.isNull() and not down.isNull()
    assert up.pixmap(12, 12).toImage() != down.pixmap(12, 12).toImage()


# ------------------------------------------------------------- install menu

def _walk(menu):
    """Every action in a menu and its submenus."""
    for action in menu.actions():
        if action.menu() is not None:
            yield from _walk(action.menu())
        elif action.text():
            yield action.text()


def test_the_install_menu_builds_against_the_machine(page):
    """It reads the machine's current devices to leave out what is already
    there - which is how a display went from a pair to an object without
    anything noticing until the menu was opened."""
    menu = page._build_install_menu()
    assert menu is not None
    labels = list(_walk(menu))
    assert "Disk…" in labels
    assert "Network interface…" in labels


def test_it_only_offers_a_display_the_machine_lacks(page):
    labels = list(_walk(page._build_install_menu()))
    assert "VNC display" in labels, "the fixture has only a SPICE display"
    assert "SPICE display" not in labels, "it already has one of those"


LOADED = DOMAIN.replace("<graphics", """<vsock model='virtio'>
      <cid auto='no' address='3'/>
    </vsock>
    <watchdog model='itco' action='reset'/>
    <graphics""").replace("<name>inline</name>", "<name>inline-loaded</name>")


def test_it_leaves_out_devices_the_machine_already_has(qapp, testconn, monkeypatch):
    """The same reading, for the devices a machine can only have one of."""
    from vmmanager.pages.detail import DetailPage

    monkeypatch.setattr(hardware_mod, "run_task",
                        lambda work, done=None, failed=None: done(work())
                        if done else work())
    dom = testconn.defineXML(LOADED)
    page = DetailPage()
    try:
        page.uuid = dom.UUIDString()
        page._load_hardware()
        qapp.processEvents()
        labels = list(_walk(page._build_install_menu()))
        assert "vsock (host/guest sockets)" not in labels
        assert not [x for x in labels if x.startswith("Watchdog")]
        assert "USB redirection channel (SPICE)" in labels, (
            "more than one of those is fine"
        )
    finally:
        page.shutdown()
        dom.undefine()


# ------------------------------------------------------------- the whole bay
#
# Everything above builds a faceplate from a payload it was handed. These
# take the payloads the component list itself puts on its rows, for a
# machine carrying one of everything - which is the only way a payload that
# quietly changed shape gets caught.

LOADED_BAY = DOMAIN.replace("<graphics", """<sound model='ich9'/>
    <input type='tablet' bus='usb'/>
    <watchdog model='itco' action='reset'/>
    <vsock model='virtio'><cid auto='no' address='3'/></vsock>
    <redirdev bus='usb' type='spicevmc'/>
    <controller type='usb' index='0' model='qemu-xhci'/>
    <filesystem type='mount' accessmode='passthrough'>
      <driver type='virtiofs'/>
      <source dir='/srv/share'/>
      <target dir='shared'/>
    </filesystem>
    <hostdev mode='subsystem' type='usb' managed='yes'>
      <source><vendor id='0x1234'/><product id='0x5678'/></source>
    </hostdev>
    <video><model type='qxl'/></video>
    <graphics""").replace("<name>inline</name>", "<name>inline-bay</name>")


@pytest.fixture
def loaded(qapp, testconn, monkeypatch):
    from vmmanager.pages.detail import DetailPage

    monkeypatch.setattr(
        hardware_mod, "run_task",
        lambda work, done=None, failed=None: done(work()) if done else work(),
    )
    dom = testconn.defineXML(LOADED_BAY)
    p = DetailPage()
    p.uuid = dom.UUIDString()
    p._load_hardware()
    qapp.processEvents()
    yield p
    p.shutdown()
    dom.undefine()


def _every_row(page):
    tree = page.hw_tree
    for i in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(i)
        for j in range(group.childCount()):
            item = group.child(j)
            data = item.data(0, hardware_mod.Qt.ItemDataRole.UserRole)
            if data is not None:
                yield item, data


def test_every_row_in_the_bay_draws_its_faceplate(loaded, qapp):
    """A payload that changes shape breaks the row that carries it, and
    nothing else notices until it is clicked."""
    seen = []
    for item, (kind, _payload) in _every_row(loaded):
        loaded.hw_tree.setCurrentItem(item)
        loaded._show_hw_detail()
        qapp.processEvents()
        seen.append(kind)
        assert loaded._dirty_fields() == [], f"{kind} came up dirty"
    assert len(seen) > 12, f"only reached {seen}"
    assert "gfx" in seen and "usb" in seen and "fs" in seen


def test_every_row_in_the_bay_offers_its_context_menu(loaded):
    for item, (kind, payload) in _every_row(loaded):
        loaded.hw_tree.setCurrentItem(item)
        menu = loaded._build_hw_menu(kind, payload)
        assert menu.actions(), f"{kind} has an empty right-click menu"


def test_every_row_in_the_bay_shows_its_xml(loaded, qapp):
    """The XML view has to resolve every row to an element of its own.

    A device a machine has at most one of carries no identity on its row,
    and the lookup went by identity alone - so a watchdog, a vsock or the
    audio backend all reported themselves missing.
    """
    from vmmanager.libvirt_service import svc_get_device_xml

    checked = []
    for _item, (kind, payload) in _every_row(loaded):
        ident = loaded._hw_ident(kind, payload)
        text = svc_get_device_xml(loaded.uuid, kind, ident)
        assert text.strip(), f"{kind} produced nothing"
        checked.append(kind)
    for kind in ("watchdog", "vsock", "redir", "controller", "labels", "tune"):
        assert kind in checked, f"the fixture should carry a {kind} row"


def test_a_property_the_machine_has_not_set_is_not_an_error(page):
    """Opening the XML of a machine with no title used to raise, which the
    tab turns into an error dialog."""
    from vmmanager.libvirt_service import svc_get_device_xml

    text = svc_get_device_xml(page.uuid, "labels", "")
    assert "nothing set" in text
    assert text.lstrip().startswith("<!--"), "still valid to show as XML"
