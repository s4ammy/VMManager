"""vfio-pci binding, boot persistence, and video BIOS ROMs.

The sysfs readers take a root directory, so these build a fake /sys the
shape the kernel publishes. The ROM parser is checked against option ROMs
assembled here byte by byte to the PCI Firmware Specification layout -
0x55 0xAA, a pointer at 0x18 to a "PCIR" block holding the length, the
code type and the last-image bit.
"""

from __future__ import annotations

import os
import struct

import pytest

from vmmanager.core.elevate import check_address, check_name
from vmmanager.core.vfio import (
    cmdline_snippet,
    function_siblings,
    initramfs_command,
    iommu_advice,
    modprobe_conf,
    parse_rom_images,
    persisted_ids,
    read_device_ids,
    rom_matches_device,
    trim_rom_to_legacy,
)


def _mk_device(root, address, vendor="0x10de", device="0x2705",
               driver="nvidia", cls="0x030000"):
    base = root / "bus/pci/devices" / address
    base.mkdir(parents=True)
    (base / "vendor").write_text(vendor + "\n")
    (base / "device").write_text(device + "\n")
    (base / "class").write_text(cls + "\n")
    if driver:
        target = root / "bus/pci/drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        os.symlink(target, base / "driver")
    return base


# -- reading the card


def test_device_ids_read_from_sysfs(tmp_path):
    _mk_device(tmp_path, "0000:01:00.0")
    ids = read_device_ids("0000:01:00.0", root=str(tmp_path))
    assert (ids.vendor, ids.device) == ("10de", "2705")
    assert ids.ident == "10de:2705"
    assert ids.driver == "nvidia"
    assert ids.is_display and not ids.is_audio


def test_all_functions_of_a_card_move_together(tmp_path):
    """A GPU is graphics plus audio; binding one and not the other is the
    mistake that leaves a machine refusing to start."""
    _mk_device(tmp_path, "0000:01:00.0")
    _mk_device(tmp_path, "0000:01:00.1", device="0x22bb",
               driver="snd_hda_intel", cls="0x040300")
    _mk_device(tmp_path, "0000:02:00.0")  # a different card entirely
    assert function_siblings("0000:01:00.0", root=str(tmp_path)) == [
        "0000:01:00.0", "0000:01:00.1",
    ]
    audio = read_device_ids("0000:01:00.1", root=str(tmp_path))
    assert audio.is_audio and not audio.is_display


def test_an_unbound_device_reports_no_driver(tmp_path):
    _mk_device(tmp_path, "0000:01:00.0", driver="")
    assert read_device_ids("0000:01:00.0", root=str(tmp_path)).driver == ""


# -- what reaches a command line


@pytest.mark.parametrize("bad", [
    "0000:01:00", "01:00.0", "0000:01:00.9", "0000:01:00.0; rm -rf /",
    "$(reboot)", "",
])
def test_a_pci_address_that_is_not_one_is_refused(bad):
    with pytest.raises(ValueError):
        check_address(bad)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "a b", "win;11", "", "a" * 65])
def test_a_machine_name_that_cannot_go_in_a_path_is_refused(bad):
    with pytest.raises(ValueError):
        check_name(bad)


def test_a_good_address_comes_back_lowercased():
    assert check_address("0000:0A:00.0") == "0000:0a:00.0"


# -- binding at boot


def test_modprobe_conf_claims_the_ids_and_orders_the_drivers():
    text = modprobe_conf(["10de:2705", "10DE:22BB"])
    assert "options vfio-pci ids=10de:2705,10de:22bb" in text
    # without the softdeps the options line is read after nvidia has bound
    assert "softdep nvidia pre: vfio-pci" in text
    assert "softdep amdgpu pre: vfio-pci" in text


def test_an_id_that_is_not_one_is_refused():
    with pytest.raises(ValueError):
        modprobe_conf(["10de:2705", "not-an-id"])
    with pytest.raises(ValueError):
        cmdline_snippet(["; reboot"])


def test_cmdline_snippet_is_the_kernel_form():
    assert cmdline_snippet(["10de:2705", "10de:22bb"]) == (
        "vfio-pci.ids=10de:2705,10de:22bb"
    )


def test_persisted_ids_reads_back_what_was_written(tmp_path):
    conf = tmp_path / "vfio.conf"
    conf.write_text(modprobe_conf(["10de:2705", "10de:22bb"]))
    assert persisted_ids(str(conf)) == ["10de:2705", "10de:22bb"]
    assert persisted_ids(str(tmp_path / "absent.conf")) == []


def test_initramfs_command_follows_the_distribution(tmp_path):
    assert initramfs_command(root=str(tmp_path)) is None
    (tmp_path / "usr/bin").mkdir(parents=True)
    (tmp_path / "usr/bin/mkinitcpio").write_text("")
    argv, distro = initramfs_command(root=str(tmp_path))
    assert argv[0] == "mkinitcpio" and distro == "Arch"


def test_iommu_advice_names_the_right_flag_for_the_cpu(tmp_path):
    (tmp_path / "proc").mkdir()
    (tmp_path / "proc/cpuinfo").write_text("vendor_id\t: AuthenticAMD\n")
    (tmp_path / "etc/default").mkdir(parents=True)
    (tmp_path / "etc/default/grub").write_text("")
    advice = iommu_advice(root=str(tmp_path))
    assert "amd_iommu=on" in advice and "grub-mkconfig" in advice

    intel = tmp_path / "intel"
    (intel / "proc").mkdir(parents=True)
    (intel / "proc/cpuinfo").write_text("vendor_id\t: GenuineIntel\n")
    (intel / "boot/loader/entries").mkdir(parents=True)
    advice = iommu_advice(root=str(intel))
    assert "intel_iommu=on" in advice and "loader/entries" in advice


# -- option ROMs


def _rom_image(code_type: int, blocks: int, last: bool,
               vendor: int = 0x10DE, device: int = 0x2705) -> bytes:
    """One PCI option-ROM image, laid out the way the spec says."""
    size = blocks * 512
    image = bytearray(size)
    image[0:2] = b"\x55\xAA"
    pcir = 0x40
    struct.pack_into("<H", image, 0x18, pcir)
    image[pcir:pcir + 4] = b"PCIR"
    struct.pack_into("<HH", image, pcir + 4, vendor, device)
    struct.pack_into("<H", image, pcir + 0x10, blocks)
    image[pcir + 0x14] = code_type
    image[pcir + 0x15] = 0x80 if last else 0x00
    return bytes(image)


def test_a_rom_with_only_an_x86_image_is_left_alone():
    rom = _rom_image(0, 2, last=True)
    assert [i.code_type for i in parse_rom_images(rom)] == [0]
    assert trim_rom_to_legacy(rom) == rom


def test_an_efi_first_rom_is_trimmed_to_its_legacy_image():
    """The hex-editor trim people do by hand, done by walking the images."""
    efi = _rom_image(3, 4, last=False)
    legacy = _rom_image(0, 2, last=True)
    rom = efi + legacy
    images = parse_rom_images(rom)
    assert [(i.code_type, i.offset) for i in images] == [(3, 0), (0, len(efi))]
    assert [i.kind for i in images] == ["EFI", "x86 BIOS"]
    trimmed = trim_rom_to_legacy(rom)
    assert trimmed == legacy
    assert trimmed[:2] == b"\x55\xAA"


def test_a_rom_with_no_x86_image_says_so_rather_than_mangling_it():
    rom = _rom_image(3, 2, last=True)
    with pytest.raises(RuntimeError, match="no legacy x86 image"):
        trim_rom_to_legacy(rom)


def test_an_empty_or_junk_rom_is_refused():
    with pytest.raises(RuntimeError, match="0x55"):
        trim_rom_to_legacy(b"\x00" * 4096)
    with pytest.raises(RuntimeError, match="0x55"):
        trim_rom_to_legacy(b"")


def test_a_rom_from_the_wrong_card_is_noticed(tmp_path):
    _mk_device(tmp_path, "0000:01:00.0")
    ids = read_device_ids("0000:01:00.0", root=str(tmp_path))
    assert rom_matches_device(_rom_image(0, 2, True), ids)
    other = _rom_image(0, 2, True, vendor=0x1002, device=0x164E)
    assert not rom_matches_device(other, ids)
