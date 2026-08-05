"""Guest features: Hyper-V, hiding, CPU flags, Looking Glass, evdev.

The dependency rules here were checked against libvirt 12.6 by defining each
pairing and reading the error, not taken from documentation or memory. One of
them turned out to be wrong when checked: synic does not require vpindex.
"""

from __future__ import annotations

import pytest

from vmmanager.core.features import (
    FeatureSupport,
    GuestFeatures,
    shmem_for_resolution,
)

SUPPORT = FeatureSupport(
    hyperv=("relaxed", "vapic", "spinlocks", "vpindex", "runtime", "synic",
            "stimer", "vendor_id", "frequencies", "tlbflush", "ipi"),
    secure_boot=True,
    secure_loader="/usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd",
)


@pytest.mark.parametrize("width,height,expected", [
    (1920, 1080, 32),
    (2560, 1440, 64),
    (3840, 2160, 128),
])
def test_looking_glass_size_covers_two_frames_plus_headroom(width, height, expected):
    assert shmem_for_resolution(width, height) == expected


def test_looking_glass_sizes_are_powers_of_two():
    """ivshmem requires it, so the arithmetic must not produce 96 or 160."""
    for width, height in ((1280, 720), (1920, 1080), (3440, 1440), (7680, 4320)):
        size = shmem_for_resolution(width, height)
        assert size & (size - 1) == 0, f"{size} is not a power of two"


def test_features_round_trip_through_a_domain(testconn, domain):
    from vmmanager.libvirt_service import svc_get_features, svc_set_features

    uuid = domain.UUIDString()
    wanted = GuestFeatures(
        hyperv={"relaxed": True, "vapic": True, "synic": True, "stimer": True,
                "vendor_id": True, "spinlocks": True},
        vendor_id="AuthenticAMD", spinlocks=8191,
        kvm_hidden=True, vmport=False,
        cpu_features={"topoext": "require", "hypervisor": "disable"},
    )
    svc_set_features(uuid, wanted, SUPPORT)
    got = svc_get_features(uuid)
    assert set(got.hyperv_on) == {"relaxed", "vapic", "synic", "stimer",
                                  "vendor_id", "spinlocks"}
    assert got.vendor_id == "AuthenticAMD"
    assert got.spinlocks == 8191
    assert got.kvm_hidden is True
    assert got.vmport is False
    assert got.cpu_features == {"topoext": "require", "hypervisor": "disable"}


def test_stimer_brings_the_hypervclock_timer_with_it(testconn, domain):
    """libvirt refuses stimer without it, and only says so when defining."""
    import xml.etree.ElementTree as ET

    import libvirt

    from vmmanager.libvirt_service import svc_set_features

    svc_set_features(
        domain.UUIDString(),
        GuestFeatures(hyperv={"synic": True, "stimer": True}),
        SUPPORT,
    )
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    assert root.findall("clock/timer[@name='hypervclock']")


def test_vendor_id_without_a_value_is_not_written(testconn, domain):
    """An empty vendor_id element is invalid, so it is dropped rather than
    written and rejected."""
    from vmmanager.libvirt_service import svc_get_features, svc_set_features

    svc_set_features(
        domain.UUIDString(),
        GuestFeatures(hyperv={"relaxed": True, "vendor_id": True}, vendor_id=""),
        SUPPORT,
    )
    got = svc_get_features(domain.UUIDString())
    assert "vendor_id" not in got.hyperv_on
    assert "relaxed" in got.hyperv_on


def test_turning_everything_off_removes_the_elements(testconn, domain):
    import xml.etree.ElementTree as ET

    import libvirt

    from vmmanager.libvirt_service import svc_get_features, svc_set_features

    uuid = domain.UUIDString()
    svc_set_features(uuid, GuestFeatures(
        hyperv={"relaxed": True}, kvm_hidden=True, vmport=False,
        cpu_features={"topoext": "require"},
    ), SUPPORT)
    svc_set_features(uuid, GuestFeatures(), SUPPORT)

    got = svc_get_features(uuid)
    assert got.hyperv_on == ()
    assert got.kvm_hidden is False
    assert got.cpu_features == {}
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    assert root.find("features/hyperv") is None
    assert root.find("features/kvm") is None


def test_looking_glass_device_is_written_and_removed(testconn, domain):
    import xml.etree.ElementTree as ET

    import libvirt

    from vmmanager.libvirt_service import svc_get_features, svc_set_features

    uuid = domain.UUIDString()
    svc_set_features(uuid, GuestFeatures(shmem_mb=128), SUPPORT)
    assert svc_get_features(uuid).shmem_mb == 128
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    shmem = root.find("devices/shmem")
    assert shmem is not None and shmem.get("name") == "looking-glass"
    assert shmem.find("model").get("type") == "ivshmem-plain"

    svc_set_features(uuid, GuestFeatures(), SUPPORT)
    assert svc_get_features(uuid).shmem_mb == 0


def test_evdev_devices_are_written_with_a_grab_on_the_first(testconn, domain):
    import xml.etree.ElementTree as ET

    import libvirt

    from vmmanager.libvirt_service import svc_get_features, svc_set_features

    paths = ("/dev/input/by-id/a-event-kbd", "/dev/input/by-id/b-event-mouse")
    svc_set_features(domain.UUIDString(), GuestFeatures(evdev=paths), SUPPORT)
    assert svc_get_features(domain.UUIDString()).evdev == paths
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    sources = root.findall("devices/input[@type='evdev']/source")
    assert [s.get("dev") for s in sources] == list(paths)
    assert sources[0].get("grab") == "all", "the first carries the release hotkey"
    assert sources[1].get("grab") is None
