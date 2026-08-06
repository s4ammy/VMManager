"""The device properties that are flags and attributes rather than values.

Each one is a place the hardware bay used to show a reading and offer no
way to change it, so the only route was the XML tab.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import libvirt
import pytest

from vmmanager.libvirt_service import (
    svc_get_hardware,
    svc_set_disk_options,
    svc_set_graphics,
    svc_set_shared_memory,
)

DOMAIN = """
<domain type='test'>
  <name>fields</name>
  <memory unit='MiB'>64</memory>
  <os><type arch='x86_64'>hvm</type></os>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='/default-pool/fields.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <graphics type='spice' port='-1' autoport='yes' listen='127.0.0.1'/>
  </devices>
</domain>
"""


@pytest.fixture
def fields(testconn):
    dom = testconn.defineXML(DOMAIN)
    yield dom
    dom.undefine()


def _xml(dom) -> ET.Element:
    return ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))


# -- disks


def test_readonly_and_shareable_are_presence_not_values(fields):
    uuid = fields.UUIDString()
    svc_set_disk_options(uuid, "vda", readonly=True, shareable=True)
    disk = _xml(fields).find("devices/disk")
    assert disk.find("readonly") is not None
    assert disk.find("shareable") is not None

    svc_set_disk_options(uuid, "vda", readonly=False, shareable=False)
    disk = _xml(fields).find("devices/disk")
    assert disk.find("readonly") is None, "an absent element, not readonly='no'"
    assert disk.find("shareable") is None


def test_serial_and_discard_round_trip(fields):
    uuid = fields.UUIDString()
    svc_set_disk_options(uuid, "vda", serial="DISK-0001", discard="unmap")
    hw = svc_get_hardware(uuid)
    assert hw.disks[0].serial == "DISK-0001"
    assert hw.disks[0].discard == "unmap"

    svc_set_disk_options(uuid, "vda", serial="", discard="")
    hw = svc_get_hardware(uuid)
    assert (hw.disks[0].serial, hw.disks[0].discard) == ("", "")


def test_only_the_discard_modes_libvirt_takes(fields):
    with pytest.raises(ValueError, match="discard"):
        svc_set_disk_options(fields.UUIDString(), "vda", discard="sometimes")


def test_editing_a_disk_that_is_not_there(fields):
    with pytest.raises(RuntimeError, match="No disk 'vdz'"):
        svc_set_disk_options(fields.UUIDString(), "vdz", readonly=True)


# -- displays


def test_an_explicit_port_turns_autoport_off(fields):
    """Setting both is how people end up wondering why the port never
    changes - libvirt picks one and ignores what was asked for."""
    uuid = fields.UUIDString()
    svc_set_graphics(uuid, "spice", "-1", port=5920)
    g = _xml(fields).find("devices/graphics")
    assert g.get("port") == "5920"
    assert g.get("autoport") == "no"

    svc_set_graphics(uuid, "spice", "5920", autoport=True)
    g = _xml(fields).find("devices/graphics")
    assert g.get("autoport") == "yes"
    assert g.get("port") is None, "a fixed port left behind means nothing"


def test_the_listen_address_can_be_changed(fields):
    uuid = fields.UUIDString()
    svc_set_graphics(uuid, "spice", "127.0.0.1",
                     listen_type="address", address="0.0.0.0")
    g = _xml(fields).find("devices/graphics")
    assert [el.get("type") for el in g.findall("listen")] == ["address"], (
        "one listen element, not the new one stacked on the old"
    )
    assert g.find("listen").get("address") == "0.0.0.0"
    assert g.get("listen") == "0.0.0.0", "the attribute has to agree with it"


def test_listening_nowhere_replaces_the_address(fields):
    """A display with listen type none is reachable only through a
    channel - what a machine handing its GPU over wants."""
    uuid = fields.UUIDString()
    svc_set_graphics(uuid, "spice", "127.0.0.1", listen_type="none")
    g = _xml(fields).find("devices/graphics")
    assert [el.get("type") for el in g.findall("listen")] == ["none"]
    assert g.get("listen") is None

    # Going back the other way is not asserted here: libvirt's test driver
    # keeps <listen type='none'/> however the definition is rewritten, so
    # the check would be testing the fake rather than the code. Verified by
    # hand against qemu:///system instead.


def test_password_and_opengl(fields):
    uuid = fields.UUIDString()
    svc_set_graphics(uuid, "spice", "-1", password="hunter2", gl=True)
    hw = svc_get_hardware(uuid)
    assert hw.graphics[0].password == "hunter2"
    assert hw.graphics[0].gl is True

    svc_set_graphics(uuid, "spice", hw.graphics[0].ident, password="", gl=False)
    hw = svc_get_hardware(uuid)
    assert hw.graphics[0].password == ""
    assert hw.graphics[0].gl is False


def test_editing_a_display_type_that_is_not_there(fields):
    with pytest.raises(RuntimeError, match="no vnc display"):
        svc_set_graphics(fields.UUIDString(), "vnc", "-1", password="x")


# -- memory and identity


def test_shared_memory_is_added_and_taken_away_cleanly(fields):
    uuid = fields.UUIDString()
    svc_set_shared_memory(uuid, True)
    assert svc_get_hardware(uuid).shared_memory is True
    assert _xml(fields).find("memoryBacking/access").get("mode") == "shared"

    svc_set_shared_memory(uuid, False)
    assert svc_get_hardware(uuid).shared_memory is False
    # and the empty parent goes with it rather than being left behind
    assert _xml(fields).find("memoryBacking") is None


def test_the_overview_facts_are_read(fields):
    hw = svc_get_hardware(fields.UUIDString())
    assert hw.uuid == fields.UUIDString()
    assert hw.hypervisor == "test"
    assert hw.arch == "x86_64"
    assert hw.emulator == "/usr/bin/qemu-system-x86_64"
