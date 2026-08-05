"""Typed-in text has to survive the trip into domain XML.

Names, titles, notes, tags and paths all get spliced into XML we build, and
`&`, `<`, `>` are markup. Unescaped, the document is malformed and libvirt
rejects it. A tag of "R&D" is enough to do it.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

# perfectly reasonable to type, hostile to naive XML building
NASTY = [
    "R&D",
    "prod <critical>",
    'say "hello"',
    "a > b",
    "both & <together>",
    "it's fine",
]


def parses(xml: str) -> ET.Element:
    """Assert the XML parses, and return the tree."""
    try:
        return ET.fromstring(xml)
    except ET.ParseError as exc:  # pragma: no cover - the failure message
        pytest.fail(f"malformed XML: {exc}\n{xml}")


@pytest.mark.parametrize("text", NASTY)
def test_tags_survive_metadata(text, domain):
    """Tags round-trip through the domain's metadata unchanged."""
    from vmmanager.core.domains import _read_vmm_meta, _write_vmm_meta

    _write_vmm_meta(domain, is_template=False, tags=(text,))
    _, tags, _ = _read_vmm_meta(domain)
    assert tags == (text,)


@pytest.mark.parametrize("text", NASTY)
def test_os_icon_override_survives(text, domain):
    from vmmanager.core.domains import _read_vmm_meta, _write_vmm_meta

    _write_vmm_meta(domain, is_template=True, tags=(), os_icon=text)
    is_template, _, icon = _read_vmm_meta(domain)
    assert (is_template, icon) == (True, text)


@pytest.mark.parametrize("text", NASTY)
def test_title_and_description_survive(text, domain):
    """Titles and notes land in <title>/<description>.

    These go through libvirt's setMetadata, which escapes for us; the test is
    here so we keep using it rather than building the XML ourselves. Written to
    the persistent config, so read that back rather than the live domain.
    """
    import libvirt

    from vmmanager.core.domains import svc_set_labels

    svc_set_labels(domain.UUIDString(), title=text, description=f"notes: {text}")
    root = parses(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    assert root.findtext("title") == text
    assert root.findtext("description") == f"notes: {text}"


def test_metadata_xml_is_well_formed_for_every_nasty_tag(domain):
    """The document parses, not just our reader's view of it."""
    from vmmanager.core.domains import _write_vmm_meta

    _write_vmm_meta(domain, is_template=False, tags=tuple(NASTY))
    parses(domain.XMLDesc(0))


@pytest.mark.parametrize("text", NASTY)
def test_network_free_text_fields_escape(text):
    """Networks carry a name, DNS domain, host names and portgroups."""
    from vmmanager.core.networks import NetworkSpec, _network_xml_ex

    xml = _network_xml_ex(
        NetworkSpec(
            name=text, mode="nat", subnet="192.168.90.0/24",
            dhcp_start="192.168.90.10", dhcp_end="192.168.90.100",
            domain_name=text,
            dns_hosts=(("192.168.90.5", text),),
            static_leases=(("52:54:00:11:22:33", "192.168.90.7", text),),
            portgroups=((text, True, 0, 0),),
        )
    )
    root = parses(xml)
    assert root.findtext("name") == text
    assert root.find("domain").get("name") == text
    assert root.find("dns/host/hostname").text == text
    assert root.find("portgroup").get("name") == text
    assert root.find("ip/dhcp/host").get("name") == text


@pytest.mark.parametrize("text", NASTY)
def test_bridged_network_escapes(text):
    from vmmanager.core.networks import NetworkSpec, _network_xml_ex

    root = parses(_network_xml_ex(NetworkSpec(name=text, mode="bridge", bridge_dev=text)))
    assert root.findtext("name") == text
    assert root.find("bridge").get("name") == text


@pytest.mark.parametrize("text", NASTY)
def test_pool_and_volume_names_escape(text):
    """Pool XML takes names and paths the user typed or picked."""
    from vmmanager.core.storage import _pool_source_xml

    root = parses(
        f"<pool type='netfs'>{_pool_source_xml('netfs', {'host': text, 'export': text})}</pool>"
    )
    assert root.find("source/host").get("name") == text
    assert root.find("source/dir").get("path") == text
