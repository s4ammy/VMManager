"""Comparing two machines, and reusing the shape of one that works."""

from __future__ import annotations

import pytest

from vmmanager.core.compare import compare_hardware
from vmmanager.core.profiles import (
    Profile,
    apply_to_spec,
    from_json,
    profile_from,
    to_json,
)
from vmmanager.libvirt_service import (
    svc_capture_profile,
    svc_compare_definitions,
    svc_compare_machines,
    svc_get_hardware,
)

BASE = """
<domain type='test'>
  <name>{name}</name>
  <memory unit='MiB'>{mem}</memory>
  <vcpu>{vcpus}</vcpu>
  <cpu mode='{cpu}'/>
  <os><type arch='x86_64' machine='q35'>hvm</type></os>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' cache='{cache}'/>
      <source file='/default-pool/{name}.qcow2'/>
      <target dev='vda' bus='{bus}'/>
    </disk>
    <video><model type='{video}'/></video>
  </devices>
</domain>
"""


def _define(conn, **kwargs):
    fields = {"mem": 1024, "vcpus": 2, "cpu": "host-model", "cache": "none",
              "bus": "virtio", "video": "virtio"}
    fields.update(kwargs)
    return conn.defineXML(BASE.format(**fields))


@pytest.fixture
def two(testconn):
    left = _define(testconn, name="works", bus="virtio", cache="none")
    right = _define(testconn, name="slow", bus="sata", cache="writeback",
                    video="vga", vcpus=4)
    yield left, right
    left.undefine()
    right.undefine()


# ------------------------------------------------------------- comparison

def test_it_lines_the_two_up_property_by_property(two):
    left, right = two
    (names, rows) = svc_compare_machines(left.UUIDString(), right.UUIDString())

    assert names == ("works", "slow")
    by_label = {r.label: r for r in rows}
    assert by_label["vcpus"].left == "2" and by_label["vcpus"].right == "4"
    assert not by_label["vcpus"].same
    assert by_label["machine"].same, "both are q35"


def test_the_differences_are_the_ones_that_explain_the_behaviour(two):
    left, right = two
    _names, rows = svc_compare_machines(left.UUIDString(), right.UUIDString())
    differing = {r.label for r in rows if not r.same}

    assert "disks" in differing, "virtio against sata"
    assert "disk cache" in differing
    assert "video" in differing
    assert "vcpus" in differing


def test_comparing_a_machine_with_itself_is_refused(two):
    left, _right = two
    with pytest.raises(ValueError, match="two different"):
        svc_compare_machines(left.UUIDString(), left.UUIDString())


def test_identical_machines_show_nothing_differing(testconn):
    a = _define(testconn, name="twin-a")
    b = _define(testconn, name="twin-b")
    try:
        _names, rows = svc_compare_machines(a.UUIDString(), b.UUIDString())
        assert [r.label for r in rows if not r.same] == []
    finally:
        a.undefine()
        b.undefine()


def test_the_full_diff_is_available_when_the_summary_is_not_enough(two):
    left, right = two
    diff = svc_compare_definitions(left.UUIDString(), right.UUIDString())

    assert diff.startswith("---")
    assert "works" in diff and "slow" in diff
    assert any(line.startswith("-") and "virtio" in line
               for line in diff.splitlines())


def test_a_row_knows_whether_it_differs():
    hw = svc_get_hardware  # noqa: F841 - imported for the reader's benefit
    left = right = None
    rows = compare_hardware(
        type("H", (), dict(
            firmware="UEFI", machine="q35", cpu_mode="host-model", vcpus=2,
            topology=None, memory_mb=1024, max_memory_mb=1024,
            shared_memory=False, boot=(), boot_menu=False, video="virtio",
            video_accel3d=False, graphics=(), disks=(), nics=(), hostdevs=(),
            filesystems=(), tpm="", tpm_version="", rng="", watchdog=None,
            sounds=(), audio="", controllers=(),
        ))(),
        type("H", (), dict(
            firmware="BIOS", machine="q35", cpu_mode="host-model", vcpus=2,
            topology=None, memory_mb=1024, max_memory_mb=1024,
            shared_memory=False, boot=(), boot_menu=False, video="virtio",
            video_accel3d=False, graphics=(), disks=(), nics=(), hostdevs=(),
            filesystems=(), tpm="", tpm_version="", rng="", watchdog=None,
            sounds=(), audio="", controllers=(),
        ))(),
    )
    assert left is right is None
    firmware = next(r for r in rows if r.label == "firmware")
    assert not firmware.same and firmware.left == "UEFI"


# --------------------------------------------------------------- profiles

def test_a_profile_captures_the_shape_and_not_the_disk(two):
    left, _right = two
    profile = svc_capture_profile(left.UUIDString(), "standard linux")

    assert profile.name == "standard linux"
    assert profile.vcpus == 2 and profile.memory_mb == 1024
    assert profile.machine == "q35"
    assert profile.video == "virtio"
    assert not hasattr(profile, "disks"), "a profile carries no storage"


def test_a_profile_needs_a_name(two):
    left, _right = two
    with pytest.raises(ValueError, match="needs a name"):
        svc_capture_profile(left.UUIDString(), "   ")


def test_it_survives_a_round_trip_through_the_database():
    profile = Profile(
        name="windows gaming", firmware="UEFI", machine="q35",
        cpu_mode="host-passthrough", vcpus=12, topology=(1, 6, 2),
        memory_mb=16384, tpm="tpm-crb", secure_boot=True,
        hyperv={"relaxed": True, "vapic": True}, note="the one that works",
    )
    back = from_json(to_json(profile))
    assert back == profile
    assert back.topology == (1, 6, 2), "a tuple, not the list json makes"


def test_a_profile_from_a_newer_build_loads_with_what_is_understood():
    payload = '{"name": "future", "vcpus": 8, "quantum_entanglement": true}'
    profile = from_json(payload)
    assert profile.name == "future" and profile.vcpus == 8


def test_the_summary_says_enough_to_pick_between_two():
    profile = Profile(name="w11", vcpus=12, memory_mb=16384, tpm="tpm-crb",
                      secure_boot=True, hyperv={"relaxed": True})
    summary = profile.summary()
    assert "12 vcpu" in summary and "16 GB" in summary
    assert "TPM 2.0" in summary and "secure boot" in summary


def test_applying_one_leaves_the_name_and_the_disk_alone():
    import dataclasses

    @dataclasses.dataclass(frozen=True)
    class Spec:
        name: str
        disk_gb: int
        vcpus: int = 1
        memory_mb: int = 512
        machine: str = "pc"
        firmware: str = "BIOS"
        cpu_mode: str = "custom"
        video: str = "vga"
        tpm: bool = False
        secure_boot: bool = False

    spec = Spec(name="new-machine", disk_gb=40)
    profile = Profile(name="p", vcpus=12, memory_mb=16384, machine="q35",
                      firmware="UEFI", cpu_mode="host-passthrough",
                      video="virtio", tpm="tpm-crb", secure_boot=True)
    result = apply_to_spec(profile, spec)

    assert result.name == "new-machine", "the profile does not rename it"
    assert result.disk_gb == 40, "and carries no storage of its own"
    assert (result.vcpus, result.memory_mb) == (12, 16384)
    assert result.machine == "q35" and result.firmware == "UEFI"
    assert result.tpm is True and result.secure_boot is True


def test_capturing_from_hardware_without_features_still_works():
    hw = type("H", (), dict(
        firmware="UEFI", machine="q35", cpu_mode="host-passthrough", vcpus=8,
        topology=(1, 4, 2), memory_mb=8192, max_memory_mb=8192,
        video="qxl", video_accel3d=True, sounds=("ich9",), tpm="",
        tpm_version="", rng="/dev/urandom", shared_memory=True,
        boot_menu=False,
    ))()
    profile = profile_from("no features", hw)
    assert profile.hyperv == {} and profile.secure_boot is False
    assert profile.rng == "/dev/urandom" and profile.shared_memory
