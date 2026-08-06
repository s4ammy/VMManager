"""The service layer, against libvirt's fake hypervisor.

Same functions the UI calls, same XML, same error paths. No real machines.
"""

from __future__ import annotations

import libvirt
import pytest


def test_lists_the_fake_hosts_domain(testconn):
    from vmmanager.libvirt_service import svc_list_networks

    assert [n.name for n in svc_list_networks()] == ["default"]


def test_snapshot_create_list_and_delete(testconn, domain):
    from vmmanager.libvirt_service import (
        svc_create_snapshot,
        svc_delete_snapshot,
        svc_list_snapshots,
    )

    uuid = domain.UUIDString()
    svc_create_snapshot(uuid, "before-upgrade", "notes & more")
    names = [s.name for s in svc_list_snapshots(uuid)]
    assert "before-upgrade" in names

    svc_delete_snapshot(uuid, "before-upgrade")
    assert "before-upgrade" not in [s.name for s in svc_list_snapshots(uuid)]


def test_snapshot_description_with_markup_round_trips(testconn, domain):
    """Descriptions are free text and must come back exactly as typed."""
    from vmmanager.libvirt_service import svc_create_snapshot, svc_list_snapshots

    uuid = domain.UUIDString()
    text = "upgrade <kernel> & reboot"
    svc_create_snapshot(uuid, "snap-markup", text)
    got = next(s for s in svc_list_snapshots(uuid) if s.name == "snap-markup")
    assert got.description == text


def test_tags_round_trip_and_clear(testconn, domain):
    from vmmanager.core.domains import _read_vmm_meta, _write_vmm_meta

    _write_vmm_meta(domain, is_template=False, tags=("prod", "R&D"))
    assert _read_vmm_meta(domain)[1] == ("prod", "R&D")

    _write_vmm_meta(domain, is_template=False, tags=())
    assert _read_vmm_meta(domain) == (False, (), "")


def test_os_icon_override_clears_back_to_autodetect(testconn, domain):
    from vmmanager.core.domains import _read_vmm_meta, svc_set_os_icon

    uuid = domain.UUIDString()
    svc_set_os_icon(uuid, "gentoo")
    assert _read_vmm_meta(domain)[2] == "gentoo"

    svc_set_os_icon(uuid, "")
    assert _read_vmm_meta(domain)[2] == ""


def test_setting_an_icon_keeps_tags_and_template_flag(testconn, domain):
    """All three share one metadata element, so writing one mustn't drop the rest."""
    from vmmanager.core.domains import _read_vmm_meta, _write_vmm_meta, svc_set_os_icon

    _write_vmm_meta(domain, is_template=True, tags=("prod",))
    svc_set_os_icon(domain.UUIDString(), "debian")
    assert _read_vmm_meta(domain) == (True, ("prod",), "debian")


def test_hardware_reads_back_the_domains_devices(testconn, domain):
    from vmmanager.libvirt_service import svc_get_hardware

    hw = svc_get_hardware(domain.UUIDString())
    assert hw.vcpus >= 1
    assert hw.memory_mb > 0
    assert isinstance(hw.disks, tuple)


def test_volume_create_and_delete(testconn):
    from vmmanager.libvirt_service import (
        svc_create_volume,
        svc_delete_volume,
        svc_list_pools,
    )

    pool = svc_list_pools()[0].name
    svc_create_volume(pool, "unit-test.qcow2", 0.001, "qcow2")
    assert "unit-test.qcow2" in [
        v.name for p in svc_list_pools() if p.name == pool for v in p.volumes
    ]

    svc_delete_volume(pool, "unit-test.qcow2")
    assert "unit-test.qcow2" not in [
        v.name for p in svc_list_pools() if p.name == pool for v in p.volumes
    ]


def test_volume_name_with_an_ampersand(testconn):
    """The name goes into <volume><name>, so it needs escaping."""
    from vmmanager.libvirt_service import svc_create_volume, svc_delete_volume, svc_list_pools

    pool = svc_list_pools()[0].name
    svc_create_volume(pool, "R&D-disk.qcow2", 0.001, "qcow2")
    try:
        names = [v.name for p in svc_list_pools() if p.name == pool for v in p.volumes]
        assert "R&D-disk.qcow2" in names
    finally:
        svc_delete_volume(pool, "R&D-disk.qcow2")


def test_network_create_and_delete(testconn):
    from vmmanager.core.networks import NetworkSpec, svc_create_network_ex
    from vmmanager.libvirt_service import svc_delete_network, svc_list_networks

    svc_create_network_ex(
        NetworkSpec(
            name="unit-net", mode="nat", subnet="192.168.211.0/24",
            dhcp_start="192.168.211.10", dhcp_end="192.168.211.100",
            domain_name="lab.internal",
        )
    )
    assert "unit-net" in [n.name for n in svc_list_networks()]

    svc_delete_network("unit-net")
    assert "unit-net" not in [n.name for n in svc_list_networks()]


@pytest.mark.parametrize("spec_kwargs", [
    {"name": "lab&net"},
    {"name": "ok", "domain_name": "lab.R&D"},
    {"name": "ok", "dns_hosts": (("10.0.0.5", "web<1>"),)},
    {"name": "ok", "portgroups": (("a&b", False, 0, 0),)},
])
def test_names_libvirt_would_mangle_are_refused(spec_kwargs, testconn):
    """libvirt stores these, then reports them back unescaped.

    It takes our escaped XML, keeps the raw text, and writes the attribute out
    unquoted, so reading the network back fails on XML libvirt produced itself.
    Checked on the qemu driver too, not just test:///. Refusing the input is
    the only fix we have, and none of these are legal in a DNS name.
    """
    from vmmanager.core.networks import NetworkSpec, svc_create_network_ex

    kwargs = {"mode": "nat", "subnet": "192.168.213.0/24", **spec_kwargs}
    with pytest.raises(ValueError, match="cannot contain"):
        svc_create_network_ex(NetworkSpec(**kwargs))


def test_one_unreadable_network_does_not_break_the_list(testconn, monkeypatch):
    """One malformed definition mustn't empty the whole networks page."""
    import libvirt

    from vmmanager.libvirt_service import svc_list_networks

    real = libvirt.virNetwork.XMLDesc
    monkeypatch.setattr(
        libvirt.virNetwork, "XMLDesc",
        lambda self, flags=0: "<network><name>broken</name><domain name='a&b'/></network>",
    )
    assert svc_list_networks() == []          # skipped, not raised
    monkeypatch.setattr(libvirt.virNetwork, "XMLDesc", real)
    assert [n.name for n in svc_list_networks()] == ["default"]


def test_missing_domain_raises_rather_than_returning_junk(testconn):
    from vmmanager.libvirt_service import svc_get_hardware

    with pytest.raises(libvirt.libvirtError):
        svc_get_hardware("00000000-0000-0000-0000-000000000000")


def test_pool_type_table_and_dialog_fields_cannot_drift(testconn):
    """Every pool type we offer has to produce parseable XML."""
    import xml.etree.ElementTree as ET

    from vmmanager.core.storage import POOL_TYPES, _pool_source_xml

    opts = {
        "host": "nas.example", "export": "/export/vms",
        "source_device": "/dev/sdb1", "source_name": "tank/vms",
        "initiator": "iqn.2004-01.example:init", "auth_user": "admin",
        "secret_uuid": "b1f2c3d4-0000-0000-0000-000000000000",
    }
    for ptype in POOL_TYPES:
        xml = f"<pool type='{ptype}'><name>p</name>{_pool_source_xml(ptype, opts)}</pool>"
        ET.fromstring(xml)  # raises if the markup is malformed


# -- a guest that suspended itself is not a guest we paused


class _FakeDomain:
    """Enough of virDomain for the pmsuspended branch.

    Any method call is recorded rather than each being written out: the
    action table is built eagerly, so it touches every name on the domain
    whichever one is being run, and a fake that has to list them all goes
    stale the moment an action is added.
    """

    def __init__(self, state: int) -> None:
        self._state = state
        self.called: list[str] = []

    def state(self):
        return (self._state, 0)

    def __getattr__(self, name: str):
        def record(*_args, **_kwargs):
            self.called.append(name)

        return record


def _run_action(monkeypatch, dom, op):
    """svc_domain_action against a fake domain rather than a real one.

    libvirt's own test driver has no way to put a domain into pmsuspended,
    and the real call was verified against qemu:///system by hand.
    """
    import libvirt

    from vmmanager.core import domains

    monkeypatch.setattr(
        domains, "_with_conn",
        lambda go: go(type("C", (), {"lookupByUUIDString": lambda self, u: dom})()),
    )
    return domains.svc_domain_action("some-uuid", op), libvirt


def test_resume_wakes_a_machine_that_suspended_itself(monkeypatch):
    import libvirt

    dom = _FakeDomain(libvirt.VIR_DOMAIN_PMSUSPENDED)
    _run_action(monkeypatch, dom, "resume")
    # resume() answers "domain is pmsuspended"; waking it is a different call
    assert dom.called == ["pMWakeup"]


def test_resume_still_resumes_a_machine_we_paused(monkeypatch):
    import libvirt

    dom = _FakeDomain(libvirt.VIR_DOMAIN_PAUSED)
    _run_action(monkeypatch, dom, "resume")
    assert dom.called == ["resume"]


@pytest.mark.parametrize("op", ["shutdown", "reboot", "pause"])
def test_asking_a_suspended_machine_to_do_things_explains_itself(monkeypatch, op):
    import libvirt

    dom = _FakeDomain(libvirt.VIR_DOMAIN_PMSUSPENDED)
    with pytest.raises(RuntimeError, match="suspended itself"):
        _run_action(monkeypatch, dom, op)
    assert dom.called == [], "nothing should have been attempted"


def test_force_off_works_on_a_suspended_machine(monkeypatch):
    """The one thing that always works, and what the message points at."""
    import libvirt

    dom = _FakeDomain(libvirt.VIR_DOMAIN_PMSUSPENDED)
    _run_action(monkeypatch, dom, "force-off")
    assert dom.called == ["destroy"]


# -- taking a display off, when there is more than one to choose from


TWO_DISPLAYS = """
<domain type='test'>
  <name>twoheads</name>
  <memory unit='MiB'>64</memory>
  <os><type>hvm</type></os>
  <devices>
    <graphics type='vnc' port='-1' autoport='yes'/>
    <graphics type='spice' port='-1' autoport='yes'/>
    <video><model type='virtio'/></video>
  </devices>
</domain>
"""


@pytest.fixture
def twoheads(testconn):
    dom = testconn.defineXML(TWO_DISPLAYS)
    yield dom
    dom.undefine()


def _displays(dom):
    import xml.etree.ElementTree as ET

    import libvirt

    root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    return [g.get("type") for g in root.findall("devices/graphics")]


def test_removing_one_display_leaves_the_other(twoheads):
    """The whole reason this is not svc_remove_simple_device: that would
    take whichever came first, and which one you get decides whether the
    console runs over VNC or SPICE."""
    from vmmanager.libvirt_service import svc_remove_display

    assert _displays(twoheads) == ["vnc", "spice"]
    svc_remove_display(twoheads.UUIDString(), "vnc", "-1")
    assert _displays(twoheads) == ["spice"]


def test_removing_a_display_type_that_is_not_there_says_so(twoheads):
    from vmmanager.libvirt_service import svc_remove_display

    with pytest.raises(RuntimeError, match="no egl-headless display"):
        svc_remove_display(twoheads.UUIDString(), "egl-headless", "-1")
    assert _displays(twoheads) == ["vnc", "spice"], "nothing should have gone"


def test_a_stale_identity_still_removes_the_only_display_of_that_type(twoheads):
    """libvirt drops port='-1' from an autoport display as it defines it, so
    the identity a row was drawn with is often not the one on disk a moment
    later. With one display of that type there is nothing to be ambiguous
    about, and the click should still work."""
    from vmmanager.libvirt_service import svc_remove_display

    svc_remove_display(twoheads.UUIDString(), "spice", "a-stale-identity")
    assert _displays(twoheads) == ["vnc"]


def test_a_machine_may_end_up_with_no_display_at_all(twoheads):
    """Which is a passthrough machine, not a mistake."""
    from vmmanager.libvirt_service import svc_remove_display

    uuid = twoheads.UUIDString()
    svc_remove_display(uuid, "vnc", "-1")
    svc_remove_display(uuid, "spice", "-1")
    assert _displays(twoheads) == []


def test_removing_the_video_adapter_warns_while_a_display_remains(twoheads):
    import xml.etree.ElementTree as ET

    import libvirt

    from vmmanager.libvirt_service import svc_remove_display, svc_remove_video

    uuid = twoheads.UUIDString()
    message = svc_remove_video(uuid)
    assert "libvirt gives" in message, "should say why it may come back"

    svc_remove_display(uuid, "vnc", "-1")
    svc_remove_display(uuid, "spice", "-1")
    svc_remove_video(uuid)  # whatever libvirt put back
    root = ET.fromstring(twoheads.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    assert root.find("devices/video") is None
