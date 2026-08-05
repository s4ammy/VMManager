"""Images from other hypervisors: OVF parsing, OVA unpacking, qemu-img argv.

The OVF here is the shape VirtualBox and VMware actually export - rasd
namespaces, ResourceType 3/4, 'byte * 2^20' memory units.
"""

from __future__ import annotations

import os
import tarfile

import pytest

from vmmanager.core.convert import (
    convert_cmd,
    extract_ova,
    foreign_disk_files,
    foreign_format,
    is_foreign_source,
    ovf_from_ova,
    parse_ovf,
)

OVF = """<?xml version="1.0"?>
<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1"
          xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
          xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData">
  <References>
    <File ovf:href="web-disk1.vmdk" ovf:id="file1"/>
    <File ovf:href="web-disk2.vmdk" ovf:id="file2"/>
    <File ovf:href="manual.pdf" ovf:id="file3"/>
  </References>
  <DiskSection>
    <Info>Virtual disks</Info>
    <Disk ovf:capacity="21474836480" ovf:fileRef="file1"/>
    <Disk ovf:capacity="1073741824" ovf:fileRef="file2"/>
  </DiskSection>
  <VirtualSystem ovf:id="web-appliance">
    <Name>web-server</Name>
    <VirtualHardwareSection>
      <Item>
        <rasd:AllocationUnits>hertz * 10^6</rasd:AllocationUnits>
        <rasd:ResourceType>3</rasd:ResourceType>
        <rasd:VirtualQuantity>2</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:AllocationUnits>byte * 2^20</rasd:AllocationUnits>
        <rasd:ResourceType>4</rasd:ResourceType>
        <rasd:VirtualQuantity>4096</rasd:VirtualQuantity>
      </Item>
    </VirtualHardwareSection>
  </VirtualSystem>
</Envelope>
"""


def test_ovf_yields_name_cpu_memory_and_disks_in_order():
    info = parse_ovf(OVF)
    assert info.name == "web-server"
    assert info.vcpus == 2
    assert info.memory_mb == 4096
    # the PDF is referenced but is not a disk
    assert info.disk_files == ("web-disk1.vmdk", "web-disk2.vmdk")


def test_ovf_memory_in_gigabytes_converts():
    text = OVF.replace("byte * 2^20", "byte * 2^30").replace(
        "<rasd:VirtualQuantity>4096</rasd:VirtualQuantity>",
        "<rasd:VirtualQuantity>4</rasd:VirtualQuantity>", 1
    )
    # the CPU item comes first in the document, so only memory changed
    info = parse_ovf(text)
    assert info.memory_mb == 4 * 1024


def test_ovf_without_hardware_reports_zeros():
    info = parse_ovf("<Envelope xmlns='http://schemas.dmtf.org/ovf/envelope/1'>"
                     "<VirtualSystem/></Envelope>")
    assert (info.vcpus, info.memory_mb) == (0, 0)


def _mk_ova(path, disks=("web-disk1.vmdk", "web-disk2.vmdk")):
    base = os.path.dirname(path)
    ovf_path = os.path.join(base, "appliance.ovf")
    with open(ovf_path, "w") as f:
        f.write(OVF)
    with tarfile.open(path, "w") as tar:
        tar.add(ovf_path, arcname="appliance.ovf")
        for disk in disks:
            p = os.path.join(base, disk)
            with open(p, "wb") as f:
                f.write(b"KDMV" + b"\0" * 60)  # vmdk magic, enough for a fake
            tar.add(p, arcname=disk)


def test_ova_descriptor_reads_without_unpacking(tmp_path):
    ova = str(tmp_path / "web.ova")
    _mk_ova(ova)
    info = ovf_from_ova(ova)
    assert info.name == "web-server"
    assert info.vcpus == 2


def test_ova_extracts_only_its_disks(tmp_path):
    ova = str(tmp_path / "web.ova")
    _mk_ova(ova)
    dest = tmp_path / "unpacked"
    dest.mkdir()
    disks = extract_ova(ova, str(dest))
    assert [os.path.basename(d) for d in disks] == [
        "web-disk1.vmdk", "web-disk2.vmdk"
    ]
    assert all(os.path.exists(d) for d in disks)
    # the descriptor and pdf were not unpacked
    assert not os.path.exists(dest / "manual.pdf")


def test_ova_missing_a_named_disk_is_refused(tmp_path):
    ova = str(tmp_path / "broken.ova")
    _mk_ova(ova, disks=("web-disk1.vmdk",))  # descriptor names two
    with pytest.raises(RuntimeError, match="web-disk2.vmdk"):
        extract_ova(ova, str(tmp_path / "out"))


def test_ovf_next_to_missing_disk_says_which(tmp_path):
    ovf = tmp_path / "appliance.ovf"
    ovf.write_text(OVF)
    with pytest.raises(RuntimeError, match="web-disk1.vmdk"):
        foreign_disk_files(str(ovf), str(tmp_path))


def test_formats_and_the_convert_call():
    assert foreign_format("/x/disk.vmdk") == "vmdk"
    assert foreign_format("/x/disk.VHDX") == "vhdx"
    assert foreign_format("/x/disk.vhd") == "vpc"
    assert foreign_format("/x/disk.qcow2") is None
    assert is_foreign_source("/x/app.ova")
    assert is_foreign_source("/x/app.ovf")
    assert not is_foreign_source("/x/disk.raw")
    assert convert_cmd("/a.vmdk", "/b.qcow2", "vmdk") == [
        "qemu-img", "convert", "-f", "vmdk", "-O", "qcow2",
        "/a.vmdk", "/b.qcow2",
    ]
    # no source format: qemu-img probes
    assert convert_cmd("/a.img", "/b.qcow2") == [
        "qemu-img", "convert", "-O", "qcow2", "/a.img", "/b.qcow2",
    ]
