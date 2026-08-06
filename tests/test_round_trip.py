"""Saving a faceplate without changing anything must not change the machine.

Every editable property is now a control that is filled in from the machine
and written back from what it holds. That makes one property worth checking
on its own: read, then write, has to be identity. When it is not, the field
is showing something the machine does not say - and the moment anything
else on that faceplate is saved, the wrong value goes with it.

This is the shape of two bugs already found by hand: a network card's link
state was never read, so every edit sent "connected" and quietly plugged a
deliberately-pulled cable back in; a USB device's startup policy was never
read, so applying anything reset it to mandatory.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import libvirt
import pytest

from vmmanager.pages.detail import DetailPage
from vmmanager.pages.detail import hardware as hardware_mod

# One machine carrying a non-default value for everything the faceplates
# can write. Defaults would prove nothing: a field that is never read comes
# up holding the default too, and writing it back changes nothing.
LOADED = """
<domain type='test'>
  <name>roundtrip</name>
  <title>A machine</title>
  <description>notes about it</description>
  <memory unit='MiB'>512</memory>
  <currentMemory unit='MiB'>256</currentMemory>
  <vcpu>4</vcpu>
  <cpu mode='host-model'>
    <topology sockets='2' cores='2' threads='1'/>
  </cpu>
  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <boot dev='hd'/>
    <boot dev='cdrom'/>
    <bootmenu enable='yes'/>
  </os>
  <memoryBacking><access mode='shared'/></memoryBacking>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' cache='writeback' discard='unmap'/>
      <source file='/default-pool/roundtrip.qcow2'/>
      <target dev='vda' bus='virtio'/>
      <serial>SER123</serial>
      <shareable/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw' cache='none'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>
    <interface type='network'>
      <mac address='52:54:00:11:22:33'/>
      <source network='default'/>
      <model type='e1000e'/>
      <link state='down'/>
    </interface>
    <controller type='usb' index='0' model='qemu-xhci'/>
    <video><model type='qxl' heads='2'/></video>
    <graphics type='spice' port='5905' autoport='no' listen='0.0.0.0'
              passwd='secret'>
      <listen type='address' address='0.0.0.0'/>
      <gl enable='yes'/>
    </graphics>
    <watchdog model='itco' action='poweroff'/>
    <hostdev mode='subsystem' type='usb' managed='yes'>
      <source startupPolicy='optional'>
        <vendor id='0x1234'/><product id='0x5678'/>
      </source>
    </hostdev>
  </devices>
</domain>
"""

# libvirt's test driver cannot update these in place, so writing one back is
# refused rather than applied. The read side is still checked below.
UNSUPPORTED = ("nic",)


@pytest.fixture
def loaded(qapp, testconn, monkeypatch):
    monkeypatch.setattr(
        hardware_mod, "run_task",
        lambda work, done=None, failed=None: done(work()) if done else work(),
    )
    dom = testconn.defineXML(LOADED)
    page = DetailPage()
    page.uuid = dom.UUIDString()
    page._load_hardware()
    qapp.processEvents()
    yield page, dom
    page.shutdown()
    dom.undefine()


def _definition(dom) -> str:
    return ET.tostring(
        ET.fromstring(dom.XMLDesc(
            libvirt.VIR_DOMAIN_XML_INACTIVE | libvirt.VIR_DOMAIN_XML_SECURE
        )),
        encoding="unicode",
    )


def _rows(page):
    tree = page.hw_tree
    for i in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(i)
        for j in range(group.childCount()):
            item = group.child(j)
            data = item.data(0, hardware_mod.Qt.ItemDataRole.UserRole)
            if data is not None:
                yield item, data


def test_writing_back_what_a_faceplate_read_changes_nothing(loaded, qapp):
    page, dom = loaded
    changed = []
    for item, (kind, payload) in _rows(page):
        if kind in UNSUPPORTED:
            continue
        page.hw_tree.setCurrentItem(item)
        page._show_hw_detail()
        qapp.processEvents()
        appliers = [(read, apply) for read, _o, apply in page._fields
                    if apply is not None]
        if not appliers:
            continue
        before = _definition(dom)
        for read, apply in appliers:
            apply(read())
        if _definition(dom) != before:
            changed.append(f"{kind} {page._hw_ident(kind, payload)}")
    assert changed == [], (
        "these faceplates rewrote the machine while saving what they read: "
        + ", ".join(changed)
    )


def test_the_machine_used_here_is_not_all_defaults(loaded):
    """A field that is never read comes up holding the default, so a
    machine of defaults would pass the test above without proving
    anything."""
    page, _dom = loaded
    hw = page._hw
    assert hw.cpu_mode == "host-model" and hw.topology == (2, 2, 1)
    assert hw.memory_mb == 256 and hw.max_memory_mb == 512
    assert hw.shared_memory and hw.boot_menu
    assert hw.title and hw.description
    assert hw.boot == ("hd", "cdrom")
    disk = [d for d in hw.disks if d.dev == "vda"][0]
    assert disk.cache == "writeback" and disk.discard == "unmap"
    assert disk.serial == "SER123" and disk.shareable
    assert [d for d in hw.disks if d.dev == "sda"][0].readonly
    assert hw.nics[0].model == "e1000e" and hw.nics[0].link_up is False
    assert hw.video == "qxl"
    display = hw.graphics[0]
    assert display.port == 5905 and not display.autoport
    assert display.address == "0.0.0.0" and display.password == "secret"
    assert display.gl
    assert hw.watchdog == ("itco", "poweroff")
    usb = [h for h in hw.hostdevs if h.kind == "usb"][0]
    assert usb.startup_policy == "optional"


def test_the_interface_faceplate_reads_every_field_it_writes(loaded):
    """The one the fake driver will not let us write back. Checked on the
    read side instead: what the controls hold has to match the machine,
    because that is what a save sends."""
    page, _dom = loaded
    for item, (kind, _payload) in _rows(page):
        if kind != "nic":
            continue
        page.hw_tree.setCurrentItem(item)
        page._show_hw_detail()
        held = [read() for read, _o, apply in page._fields if apply is not None]
        assert "52:54:00:11:22:33" in held
        assert "e1000e" in held
        assert False in held, "the link is down and the box must show that"


# ------------------------------------------------------- the editors that stay
#
# Tuning and Guest features are still dialogs. Applying one writes the whole
# set rather than the fields that moved, which makes the same property
# sharper for them: what the dialog hands back, given no interaction, has to
# be what it was given.

def test_the_features_dialog_hands_back_what_it_was_given(qapp):
    from vmmanager.core.features import FeatureSupport, GuestFeatures
    from vmmanager.dialogs import GuestFeaturesDialog

    support = FeatureSupport(
        hyperv=("relaxed", "vapic", "spinlocks"), secure_boot=True,
        secure_loader="/usr/share/edk2/OVMF_CODE.secboot.fd", machine="q35",
    )
    before = GuestFeatures(
        hyperv={"relaxed": True, "vapic": True, "spinlocks": True},
        vendor_id="KVMKVMKVM", spinlocks=8191, kvm_hidden=True, vmport=False,
        cpu_features={"invtsc": "require"}, shmem_mb=0, evdev=(),
        secure_boot=True,
    )
    dialog = GuestFeaturesDialog(None, "win11", before, support, [], machine="q35")
    assert dialog.result_features() == before


def test_an_enlightenment_this_host_does_not_advertise_is_kept(qapp):
    """A Windows machine brought from another host carries whatever it was
    given there. The boxes came only from this host's list, so anything
    outside it had no box - and applying, which writes the whole set,
    stripped it without saying so."""
    from vmmanager.core.features import FeatureSupport, GuestFeatures
    from vmmanager.dialogs import GuestFeaturesDialog

    support = FeatureSupport(hyperv=("relaxed", "vapic"), secure_boot=False,
                             secure_loader="", machine="q35")
    before = GuestFeatures(
        hyperv={"relaxed": True, "vapic": True, "stimer_direct": True},
    )
    dialog = GuestFeaturesDialog(None, "imported", before, support, [],
                                 machine="q35")

    assert "stimer_direct" in dialog.hyperv, "it needs a box to survive"
    assert dialog.hyperv["stimer_direct"].isChecked()
    assert dialog.result_features().hyperv.get("stimer_direct") is True
    assert "not advertised" in dialog.hyperv["stimer_direct"].text()


def test_one_that_is_off_and_unsupported_is_not_offered(qapp):
    """Only what the machine actually has gets carried over - the dialog is
    not a place to turn on something this host cannot do."""
    from vmmanager.core.features import FeatureSupport, GuestFeatures
    from vmmanager.dialogs import GuestFeaturesDialog

    support = FeatureSupport(hyperv=("relaxed",), secure_boot=False,
                             secure_loader="", machine="q35")
    before = GuestFeatures(hyperv={"relaxed": True, "stimer_direct": False})
    dialog = GuestFeaturesDialog(None, "m", before, support, [], machine="q35")

    assert "stimer_direct" not in dialog.hyperv
