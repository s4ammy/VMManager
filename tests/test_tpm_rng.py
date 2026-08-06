"""A TPM and a random number source, as devices rather than creation options.

Windows 11 refuses to install without a TPM 2.0, and until now one could
only be asked for when the machine was made - so a machine created without
one could never gain it here. virtio-rng is the other half of the same gap:
a freshly installed Linux guest can sit for a minute at first boot waiting
for its random pool, and nothing in the app offered the fix.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import libvirt
import pytest

# Bound at import, before the fixture below replaces it: the two tests that
# check the reader itself need the real one.
from vmmanager.core.devices import tpm_backends as real_tpm_backends
from vmmanager.libvirt_service import (
    svc_add_rng,
    svc_add_tpm,
    svc_get_hardware,
    svc_set_rng_source,
    svc_set_tpm,
)

DOMAIN = """
<domain type='test'>
  <name>devices</name>
  <memory unit='MiB'>64</memory>
  <os><type arch='x86_64' machine='q35'>hvm</type></os>
  <devices><emulator>/usr/bin/qemu-system-x86_64</emulator></devices>
</domain>
"""


@pytest.fixture
def machine(testconn):
    dom = testconn.defineXML(DOMAIN)
    yield dom
    dom.undefine()


def _xml(dom):
    return ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))


@pytest.fixture(autouse=True)
def _swtpm_present(monkeypatch):
    """The fake hypervisor advertises no TPM backends, and a real host only
    has an emulated one when swtpm is installed. The tests about that check
    it explicitly; everything else assumes it is there."""
    from vmmanager.core import devices

    monkeypatch.setattr(devices, "tpm_backends",
                        lambda *a, **k: ("passthrough", "emulator"))


def test_a_machine_can_be_given_a_tpm_after_it_was_made(machine):
    assert svc_get_hardware(machine.UUIDString()).tpm == ""

    svc_add_tpm(machine.UUIDString())

    tpm = _xml(machine).find("devices/tpm")
    assert tpm is not None and tpm.get("model") == "tpm-crb"
    assert tpm.find("backend").get("version") == "2.0"
    assert tpm.find("backend").get("type") == "emulator"


def test_the_tpm_reads_back_onto_the_hardware_tab(machine):
    svc_add_tpm(machine.UUIDString(), "tpm-tis", "1.2")
    hw = svc_get_hardware(machine.UUIDString())
    assert (hw.tpm, hw.tpm_version) == ("tpm-tis", "1.2")


def test_a_second_tpm_is_refused(machine):
    """libvirt takes a definition with two and the machine then will not
    start, which reads as a broken TPM rather than a mistake here."""
    svc_add_tpm(machine.UUIDString())
    with pytest.raises(RuntimeError, match="already has a TPM"):
        svc_add_tpm(machine.UUIDString())


def test_the_tpm_interface_and_version_can_be_changed(machine):
    svc_add_tpm(machine.UUIDString(), "tpm-crb", "2.0")
    svc_set_tpm(machine.UUIDString(), "tpm-tis", "1.2")

    tpm = _xml(machine).find("devices/tpm")
    assert tpm.get("model") == "tpm-tis"
    assert tpm.find("backend").get("version") == "1.2"


def test_an_unknown_tpm_is_refused_before_libvirt_sees_it(machine):
    with pytest.raises(ValueError, match="TPM is"):
        svc_add_tpm(machine.UUIDString(), "tpm-made-up")
    with pytest.raises(ValueError, match="version"):
        svc_add_tpm(machine.UUIDString(), "tpm-crb", "3.0")


def test_a_machine_can_be_given_a_random_source(machine):
    assert svc_get_hardware(machine.UUIDString()).rng == ""

    svc_add_rng(machine.UUIDString())

    rng = _xml(machine).find("devices/rng")
    assert rng is not None and rng.get("model") == "virtio"
    assert (rng.find("backend").text or "").strip() == "/dev/urandom"


def test_the_entropy_source_can_be_pointed_somewhere_else(machine):
    svc_add_rng(machine.UUIDString())
    svc_set_rng_source(machine.UUIDString(), "/dev/hwrng")

    assert (_xml(machine).find("devices/rng/backend").text or "").strip() == "/dev/hwrng"
    assert svc_get_hardware(machine.UUIDString()).rng == "/dev/hwrng"


def test_a_second_random_source_is_refused(machine):
    svc_add_rng(machine.UUIDString())
    with pytest.raises(RuntimeError, match="already has a random"):
        svc_add_rng(machine.UUIDString())


def test_both_appear_in_the_component_bay(qapp, testconn, monkeypatch):
    from vmmanager.pages.detail import DetailPage
    from vmmanager.pages.detail import hardware as hardware_mod

    monkeypatch.setattr(
        hardware_mod, "run_task",
        lambda work, done=None, failed=None: done(work()) if done else work(),
    )
    dom = testconn.defineXML(DOMAIN.replace("<name>devices</name>", "<name>bay</name>"))
    page = DetailPage()
    try:
        page.uuid = dom.UUIDString()
        svc_add_tpm(page.uuid)
        svc_add_rng(page.uuid)
        page._load_hardware()
        qapp.processEvents()

        kinds = []
        tree = page.hw_tree
        for i in range(tree.topLevelItemCount()):
            group = tree.topLevelItem(i)
            for j in range(group.childCount()):
                data = group.child(j).data(0, hardware_mod.Qt.ItemDataRole.UserRole)
                if data:
                    kinds.append(data[0])
        assert "tpm" in kinds and "rng" in kinds
    finally:
        page.shutdown()
        dom.undefine()


def test_the_install_menu_stops_offering_them_once_they_are_there(
    qapp, testconn, monkeypatch
):
    from vmmanager.pages.detail import DetailPage
    from vmmanager.pages.detail import hardware as hardware_mod

    monkeypatch.setattr(
        hardware_mod, "run_task",
        lambda work, done=None, failed=None: done(work()) if done else work(),
    )
    dom = testconn.defineXML(DOMAIN.replace("<name>devices</name>", "<name>menu</name>"))
    page = DetailPage()

    def labels():
        def walk(menu):
            for action in menu.actions():
                if action.menu() is not None:
                    yield from walk(action.menu())
                elif action.text():
                    yield action.text()
        return list(walk(page._build_install_menu()))

    try:
        page.uuid = dom.UUIDString()
        page._load_hardware()
        qapp.processEvents()
        assert any("TPM" in x for x in labels())
        assert any("virtio-rng" in x for x in labels())

        svc_add_tpm(page.uuid)
        svc_add_rng(page.uuid)
        page._load_hardware()
        qapp.processEvents()
        assert not any("TPM" in x for x in labels())
        assert not any("virtio-rng" in x for x in labels())
    finally:
        page.shutdown()
        dom.undefine()


# ------------------------------------------------------------- host support

def test_a_host_without_swtpm_is_told_what_is_missing(machine, monkeypatch):
    """libvirt's own answer is "TPM version '2.0' is not supported", which
    sends people looking at the version rather than at the package they
    have not installed. Verified against a real host with no swtpm."""
    from vmmanager.core import devices

    monkeypatch.setattr(devices, "tpm_backends", lambda *a, **k: ("passthrough",))
    with pytest.raises(RuntimeError, match="swtpm is not installed"):
        svc_add_tpm(machine.UUIDString())


def test_an_emulated_backend_is_enough(machine, monkeypatch):
    from vmmanager.core import devices

    monkeypatch.setattr(devices, "tpm_backends",
                        lambda *a, **k: ("passthrough", "emulator"))
    svc_add_tpm(machine.UUIDString())
    assert svc_get_hardware(machine.UUIDString()).tpm == "tpm-crb"


def test_the_backends_are_read_from_the_host_rather_than_assumed(testconn):
    class _Caps:
        def getDomainCapabilities(self, *_a):   # noqa: N802 - libvirt's name
            return """<domainCapabilities><devices>
              <tpm supported='yes'>
                <enum name='model'><value>tpm-crb</value></enum>
                <enum name='backendModel'>
                  <value>passthrough</value><value>emulator</value>
                </enum>
              </tpm>
            </devices></domainCapabilities>"""

    assert real_tpm_backends(_Caps()) == ("passthrough", "emulator")


def test_a_host_that_reports_nothing_is_not_guessed_at(testconn):
    class _Silent:
        def getDomainCapabilities(self, *_a):   # noqa: N802 - libvirt's name
            raise libvirt.libvirtError("not supported")

    assert real_tpm_backends(_Silent()) == ()
