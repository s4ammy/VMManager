"""The XML we hand libvirt: device targets, boot order, domains, cloud-init.

This is the code that decides what hardware a machine has. A wrong disk target
means the attach fails; a malformed element means libvirt rejects the whole
definition. Everything here is a pure function over strings and element trees,
so it can be checked directly, and where the fake driver will accept the result
it gets defined for real.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from vmmanager.core.xmlutil import (
    _boot_entries,
    _find_device_element,
    _hostdev_ident,
    _next_disk_target,
)


def domain_with(disks: list[tuple[str, str]] = (), nics: list[str] = ()) -> ET.Element:
    """A minimal domain tree: disks as (target dev, bus), nics as MACs."""
    parts = ["<domain type='kvm'><name>probe</name><devices>"]
    for dev, bus in disks:
        parts.append(
            f"<disk type='file' device='disk'><source file='/pool/{dev}.qcow2'/>"
            f"<target dev='{dev}' bus='{bus}'/></disk>"
        )
    for mac in nics:
        parts.append(
            f"<interface type='network'><mac address='{mac}'/>"
            "<source network='default'/></interface>"
        )
    parts.append("</devices></domain>")
    return ET.fromstring("".join(parts))


# ---------------------------------------------------------------- disk targets


@pytest.mark.parametrize("bus,expected", [
    ("virtio", "vda"),
    ("sata", "sda"),
    ("scsi", "sda"),
    ("ide", "hda"),
    ("usb", "sda"),
])
def test_first_target_follows_the_bus(bus, expected):
    assert _next_disk_target(domain_with(), bus) == expected


def test_targets_skip_what_is_taken():
    root = domain_with([("vda", "virtio"), ("vdb", "virtio")])
    assert _next_disk_target(root, "virtio") == "vdc"


def test_a_gap_in_the_middle_is_reused():
    """vdb is free, so use it rather than running to the end."""
    root = domain_with([("vda", "virtio"), ("vdc", "virtio")])
    assert _next_disk_target(root, "virtio") == "vdb"


def test_buses_do_not_collide():
    """A sata sda must not stop virtio getting vda."""
    root = domain_with([("sda", "sata")])
    assert _next_disk_target(root, "virtio") == "vda"


def test_targets_continue_past_the_alphabet():
    """26 disks is unusual but the naming has to keep working."""
    taken = []
    root = domain_with()
    for _ in range(27):
        dev = _next_disk_target(root, "virtio")
        taken.append(dev)
        root = domain_with([(d, "virtio") for d in taken])
    assert taken[25] == "vdz"
    assert taken[26] == "vdaa"
    assert len(set(taken)) == 27


# ---------------------------------------------------------------- boot order


def test_os_level_boot_devices_are_read_in_order():
    root = ET.fromstring(
        "<domain><os><boot dev='cdrom'/><boot dev='hd'/></os><devices/></domain>"
    )
    assert _boot_entries(root) == ("cdrom", "hd")


def test_per_device_boot_order_wins_over_the_os_list():
    root = ET.fromstring(
        "<domain><os><boot dev='hd'/></os><devices>"
        "<disk device='disk'><target dev='vdb'/><boot order='2'/></disk>"
        "<disk device='disk'><target dev='vda'/><boot order='1'/></disk>"
        "</devices></domain>"
    )
    assert _boot_entries(root) == ("disk vda", "disk vdb")


def test_boot_order_covers_nics_too():
    root = ET.fromstring(
        "<domain><os/><devices>"
        "<disk device='disk'><target dev='vda'/><boot order='2'/></disk>"
        "<interface><mac address='52:54:00:aa:bb:cc'/><boot order='1'/></interface>"
        "</devices></domain>"
    )
    assert _boot_entries(root) == ("nic 52:54:00:aa:bb:cc", "disk vda")


def test_no_boot_configuration_at_all_is_empty_not_an_error():
    assert _boot_entries(domain_with()) == ()


# ---------------------------------------------------------------- finding devices


def test_a_disk_is_found_by_its_target():
    root = domain_with([("vda", "virtio"), ("vdb", "virtio")])
    found = _find_device_element(root, "disk", "vdb")
    assert found is not None
    assert found.find("target").get("dev") == "vdb"


def test_a_nic_is_found_by_its_mac_whatever_the_case():
    root = domain_with(nics=["52:54:00:AA:BB:CC"])
    assert _find_device_element(root, "nic", "52:54:00:aa:bb:cc") is not None


def test_looking_for_something_absent_returns_nothing():
    assert _find_device_element(domain_with(), "disk", "vdz") is None


@pytest.mark.parametrize("xml,expected", [
    ("<hostdev type='usb'><source><vendor id='0x1234'/>"
     "<product id='0x5678'/></source></hostdev>", ("usb", "1234:5678")),
    ("<hostdev type='pci'><source><address domain='0x0000' bus='0x03'"
     " slot='0x00' function='0x0'/></source></hostdev>", ("pci", "0000:03:00.0")),
])
def test_hostdev_identity_is_read_back_in_the_form_we_write_it(xml, expected):
    info = _hostdev_ident(ET.fromstring(xml))
    assert info is not None
    assert (info.kind, info.ident) == expected


def test_an_unrecognised_hostdev_is_skipped_rather_than_guessed():
    assert _hostdev_ident(ET.fromstring("<hostdev type='mdev'/>")) is None


# ---------------------------------------------------------------- whole domains


def test_a_created_domain_is_accepted_by_libvirt(testconn):
    """The strongest check available: define it and see."""
    from vmmanager.core.models import CreateSpec
    from vmmanager.libvirt_service import svc_create_vm

    # the test driver has no storage pool work to do here, so import a path
    spec = CreateSpec(
        name="xml-probe", vcpus=2, memory_mb=2048, network="default",
        uefi=False, import_path="/tmp/does-not-matter.qcow2",
        osinfo_id="http://debian.org/debian/12",
    )
    svc_create_vm(spec)
    dom = testconn.lookupByName("xml-probe")
    root = ET.fromstring(dom.XMLDesc(0))
    assert root.findtext("name") == "xml-probe"
    assert root.findtext("vcpu") == "2"
    assert root.find("devices/interface/source").get("network") == "default"


def test_the_created_domain_records_which_os_it_runs(testconn):
    """This is what the machine list reads to pick an icon."""
    from vmmanager.core.models import CreateSpec
    from vmmanager.core.poller import PollWorker
    from vmmanager.libvirt_service import svc_create_vm

    svc_create_vm(CreateSpec(
        name="os-probe", vcpus=1, memory_mb=512, network="default", uefi=False,
        import_path="/tmp/x.qcow2", osinfo_id="http://microsoft.com/win/11",
    ))
    dom = testconn.lookupByName("os-probe")
    _macs, _nets, osinfo_id, _disks = PollWorker._domain_facts(dom)
    assert osinfo_id == "http://microsoft.com/win/11"


def test_spare_pcie_ports_are_generated_so_hotplug_works(testconn):
    """A q35 machine with no free ports cannot accept a NIC later."""
    from vmmanager.core.models import CreateSpec
    from vmmanager.libvirt_service import svc_create_vm

    svc_create_vm(CreateSpec(
        name="port-probe", vcpus=1, memory_mb=512, network="default",
        uefi=False, import_path="/tmp/x.qcow2",
    ))
    root = ET.fromstring(testconn.lookupByName("port-probe").XMLDesc(0))
    ports = [
        c for c in root.findall("devices/controller")
        if c.get("model") == "pcie-root-port"
    ]
    assert len(ports) >= 8, "not enough spare ports for hotplug"
    roots = [c for c in root.findall("devices/controller")
             if c.get("model") == "pcie-root"]
    assert [c.get("index") for c in roots] == ["0"], "pcie-root must be index 0"


# ---------------------------------------------------------------- cloud-init


def test_cloud_init_user_data_is_valid_yaml_and_has_the_user():
    from vmmanager.core.create import _build_seed_iso
    from vmmanager.core.models import CloudInit

    # exercise the document builder without invoking xorrisofs
    import vmmanager.core.create as create

    captured = {}

    def fake_run(cmd, **kwargs):
        from pathlib import Path

        cwd = Path(kwargs["cwd"])
        captured["user-data"] = (cwd / "user-data").read_text()
        captured["meta-data"] = (cwd / "meta-data").read_text()

        class Result:
            returncode = 0
            stderr = ""

        (cwd / "seed.iso").write_bytes(b"fake")
        return Result()

    original = create.subprocess.run
    create.subprocess.run = fake_run
    try:
        _build_seed_iso("web-01", CloudInit(
            hostname="web", user="sam", password="hunter2",
            ssh_key="ssh-ed25519 AAAA", packages=("qemu-guest-agent", "vim"),
        ))
    finally:
        create.subprocess.run = original

    text = captured["user-data"]
    assert text.startswith("#cloud-config")
    assert "name: sam" in text
    assert "hostname: web" in text
    assert "qemu-guest-agent" in text
    assert "ssh-ed25519 AAAA" in text
    assert "local-hostname: web" in captured["meta-data"]


def test_cloud_init_omits_a_password_block_when_none_is_set():
    """chpasswd with an empty password would lock the account differently."""
    from vmmanager.core.create import _build_seed_iso
    from vmmanager.core.models import CloudInit
    import vmmanager.core.create as create

    captured = {}

    def fake_run(cmd, **kwargs):
        from pathlib import Path

        cwd = Path(kwargs["cwd"])
        captured["user-data"] = (cwd / "user-data").read_text()

        class Result:
            returncode = 0
            stderr = ""

        (cwd / "seed.iso").write_bytes(b"fake")
        return Result()

    original = create.subprocess.run
    create.subprocess.run = fake_run
    try:
        _build_seed_iso("web-01", CloudInit(
            hostname="", user="sam", password="", ssh_key="", packages=(),
        ))
    finally:
        create.subprocess.run = original

    assert "chpasswd" not in captured["user-data"]
    assert "local-hostname" not in captured.get("meta-data", "")
