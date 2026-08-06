"""What about the host would stop a machine starting.

libvirt's own answer to a failed start is accurate and rarely useful. Each
check here reads one thing the definition assumes about the host; they take
their inputs as arguments so a test can describe a host rather than need one.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from vmmanager.core.startcheck import (
    check_disks,
    check_firmware,
    check_hostdevs,
    check_hugepages,
    check_memory,
    check_pinning,
)


def _domain(body: str, memory="4194304") -> ET.Element:
    return ET.fromstring(
        f"<domain><memory unit='KiB'>{memory}</memory>{body}</domain>"
    )


def test_a_missing_disk_blocks_and_a_missing_disc_only_cautions(tmp_path):
    there = tmp_path / "there.qcow2"
    there.write_bytes(b"")
    root = _domain(f"""<devices>
      <disk device='disk'><source file='{there}'/><target dev='vda'/></disk>
      <disk device='disk'><source file='/gone/x.qcow2'/><target dev='vdb'/></disk>
      <disk device='cdrom'><source file='/gone/y.iso'/><target dev='sda'/></disk>
    </devices>""")
    problems = check_disks(root)
    assert [(p.severity, p.what.split()[-1]) for p in problems] == [
        ("blocked", "missing"), ("caution", "missing"),
    ]
    assert "vdb" in problems[0].what and "sda" in problems[1].what
    assert "/gone/x.qcow2" in problems[0].why


def test_hugepages_that_nobody_reserved(tmp_path):
    root = _domain(
        "<memoryBacking><hugepages><page size='1048576' unit='KiB'/>"
        "</hugepages></memoryBacking>"
    )
    none_at_all = check_hugepages(root, free_pages=lambda _s: None)
    assert none_at_all and none_at_all[0].severity == "blocked"
    assert "no" in none_at_all[0].what.lower()

    # 4 GiB in 1 GiB pages is 4 pages; 2 free is not enough
    short = check_hugepages(root, free_pages=lambda _s: 2)
    assert short and "4 hugepages and 2 are free" in short[0].what
    assert check_hugepages(root, free_pages=lambda _s: 8) == []


def test_a_machine_without_hugepages_is_not_asked_about_them():
    assert check_hugepages(_domain("<devices/>"), free_pages=lambda _s: 0) == []


def test_a_device_the_host_took_back():
    root = _domain("""<devices>
      <hostdev type='pci'><source><address domain='0x0000' bus='0x01'
        slot='0x00' function='0x0'/></source></hostdev>
    </devices>""")
    gone = check_hostdevs(root, lambda _a: None)
    assert gone and gone[0].severity == "blocked"

    held = check_hostdevs(root, lambda _a: "nvidia")
    assert held and held[0].severity == "caution"
    assert "nvidia" in held[0].what

    ready = check_hostdevs(root, lambda _a: "vfio-pci")
    assert ready == []


def test_pinning_that_names_cpus_this_host_does_not_have():
    root = _domain("<cputune><vcpupin vcpu='0' cpuset='40'/></cputune>")
    on_a_big_host = check_pinning(root, host_cpus=64)
    assert on_a_big_host == []
    on_this_one = check_pinning(root, host_cpus=16)
    assert on_this_one and on_this_one[0].severity == "blocked"
    assert "0-15" in on_this_one[0].why


def test_memory_the_host_cannot_find_is_a_caution_not_a_block():
    """Linux usually still starts it by reclaiming cache."""
    root = _domain("<devices/>", memory=str(32 * 1024 * 1024))  # 32 GiB
    assert check_memory(root, host_free_mb=64 * 1024) == []
    tight = check_memory(root, host_free_mb=8 * 1024)
    assert tight and tight[0].severity == "caution"


def test_memory_is_read_whatever_unit_it_is_written_in():
    from vmmanager.core.startcheck import _memory_mb

    assert _memory_mb(ET.fromstring("<domain><memory unit='KiB'>4194304</memory></domain>")) == 4096
    assert _memory_mb(ET.fromstring("<domain><memory unit='MiB'>4096</memory></domain>")) == 4096
    assert _memory_mb(ET.fromstring("<domain><memory unit='GiB'>4</memory></domain>")) == 4096
    assert _memory_mb(ET.fromstring("<domain/>")) == 0


def test_a_uefi_machine_that_lost_its_variables(tmp_path):
    root = _domain(f"<os><nvram>{tmp_path / 'gone.fd'}</nvram></os>")
    problems = check_firmware(root)
    assert problems and problems[0].severity == "caution"
    assert "boot entries" in problems[0].why

    present = tmp_path / "there.fd"
    present.write_bytes(b"")
    assert check_firmware(_domain(f"<os><nvram>{present}</nvram></os>")) == []


def test_the_blocking_reasons_come_first(testconn, domain):
    """A caution above a blocker buries the thing that actually stopped it."""
    from vmmanager.libvirt_service import svc_start_problems

    problems = svc_start_problems(domain.UUIDString())
    severities = [p.severity for p in problems]
    assert severities == sorted(severities, key=lambda s: 0 if s == "blocked" else 1)
