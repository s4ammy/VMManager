"""Mediated devices and SR-IOV: sysfs reading and the XML either way.

The sysfs walkers take a root directory, so these build a fake /sys and
check what comes back - the same shapes the real GVT-g and vGPU drivers
publish.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

from vmmanager.core.mdev import (
    mdev_nodedev_xml,
    parse_mdev_nodedev,
    read_mdev_types,
    read_sriov_pfs,
)
from vmmanager.core.xmlutil import _hostdev_ident


def _mk_mdev_sysfs(root, parent="0000:00:02.0"):
    tdir = root / "class/mdev_bus" / parent / "mdev_supported_types" / "i915-GVTg_V5_4"
    tdir.mkdir(parents=True)
    (tdir / "name").write_text("GVTg_V5_4\n")
    (tdir / "device_api").write_text("vfio-pci\n")
    (tdir / "available_instances").write_text("2\n")


def test_mdev_types_read_from_sysfs(tmp_path):
    _mk_mdev_sysfs(tmp_path)
    types = read_mdev_types(str(tmp_path))
    assert len(types) == 1
    t = types[0]
    assert t.parent == "0000:00:02.0"
    assert t.type_id == "i915-GVTg_V5_4"
    assert t.name == "GVTg_V5_4"
    assert t.api == "vfio-pci"
    assert t.available == 2


def test_host_without_mdev_bus_has_no_types(tmp_path):
    assert read_mdev_types(str(tmp_path)) == []


def test_sriov_pf_reports_its_vfs(tmp_path):
    dev = tmp_path / "bus/pci/devices/0000:05:00.0"
    dev.mkdir(parents=True)
    (dev / "sriov_totalvfs").write_text("64\n")
    (dev / "sriov_numvfs").write_text("2\n")
    (dev / "net/enp5s0").mkdir(parents=True)
    vf0 = tmp_path / "bus/pci/devices/0000:05:10.0"
    vf1 = tmp_path / "bus/pci/devices/0000:05:10.1"
    vf0.mkdir()
    vf1.mkdir()
    os.symlink("../0000:05:10.0", dev / "virtfn0")
    os.symlink("../0000:05:10.1", dev / "virtfn1")

    pfs = read_sriov_pfs(str(tmp_path))
    assert len(pfs) == 1
    pf = pfs[0]
    assert pf.address == "0000:05:00.0"
    assert pf.interface == "enp5s0"
    assert (pf.numvfs, pf.totalvfs) == (2, 64)
    assert pf.vfs == ("0000:05:10.0", "0000:05:10.1")
    # the VFs themselves are not PFs
    assert all(p.address != "0000:05:10.0" for p in pfs)


def test_nodedev_xml_names_the_parent_the_libvirt_way():
    root = ET.fromstring(mdev_nodedev_xml("0000:00:02.0", "i915-GVTg_V5_4"))
    assert root.findtext("parent") == "pci_0000_00_02_0"
    t = root.find("capability[@type='mdev']/type")
    assert t is not None and t.get("id") == "i915-GVTg_V5_4"


def test_nodedev_parse_round_trips():
    xml = """<device>
      <name>mdev_6a3c9dd2</name>
      <parent>pci_0000_00_02_0</parent>
      <capability type='mdev'>
        <type id='i915-GVTg_V5_4'/>
        <uuid>6a3c9dd2-0001-4b6e-9f1e-6f0c2f2b9d70</uuid>
      </capability>
    </device>"""
    info = parse_mdev_nodedev(xml)
    assert info is not None
    assert info.uuid == "6a3c9dd2-0001-4b6e-9f1e-6f0c2f2b9d70"
    assert info.parent == "0000:00:02.0"
    assert info.type_id == "i915-GVTg_V5_4"
    # a non-mdev node device is not one
    assert parse_mdev_nodedev("<device><name>usb_1</name></device>") is None


def test_mdev_hostdev_xml_round_trips():
    from vmmanager.core.hostdev import _hostdev_xml

    mdev_uuid = "6a3c9dd2-0001-4b6e-9f1e-6f0c2f2b9d70"
    el = ET.fromstring(_hostdev_xml("mdev", mdev_uuid))
    assert el.get("type") == "mdev"
    assert el.get("model") == "vfio-pci"
    info = _hostdev_ident(el)
    assert info is not None and (info.kind, info.ident) == ("mdev", mdev_uuid)


def test_an_mdev_hostdev_shows_up_in_the_hardware_list(testconn):
    from vmmanager.libvirt_service import svc_get_hardware

    mdev_uuid = "6a3c9dd2-0001-4b6e-9f1e-6f0c2f2b9d70"
    dom = testconn.defineXML(f"""
<domain type='test'>
  <name>vgpu-guest</name>
  <memory unit='MiB'>64</memory>
  <os><type>hvm</type></os>
  <devices>
    <hostdev mode='subsystem' type='mdev' model='vfio-pci' managed='no'>
      <source><address uuid='{mdev_uuid}'/></source>
    </hostdev>
  </devices>
</domain>""")
    try:
        hw = svc_get_hardware(dom.UUIDString())
        assert [(h.kind, h.ident) for h in hw.hostdevs] == [("mdev", mdev_uuid)]
    finally:
        dom.undefine()
