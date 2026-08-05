"""Network filters: the per-NIC filterref edit and the template.

The fake driver has no nwfilter support at all, which is itself a case the
UI has to survive - svc_nwfilter_names answers [] instead of erroring, so
the NIC dialog simply doesn't offer filters there.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from vmmanager.libvirt_service import (
    nwfilter_template,
    svc_get_hardware,
    svc_nwfilter_names,
    svc_set_nic_filter,
)


def test_no_driver_support_reads_as_no_filters(testconn):
    assert svc_nwfilter_names() == []


def test_setting_and_clearing_a_nic_filter(testconn, domain):
    uuid = domain.UUIDString()
    hw = svc_get_hardware(uuid)
    mac = hw.nics[0].mac
    assert hw.nics[0].filter == ""

    import libvirt

    svc_set_nic_filter(uuid, mac, "clean-traffic", ip="192.168.122.5")
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    ref = root.find("devices/interface/filterref")
    assert ref is not None and ref.get("filter") == "clean-traffic"
    param = ref.find("parameter")
    assert param is not None
    assert (param.get("name"), param.get("value")) == ("IP", "192.168.122.5")
    assert svc_get_hardware(uuid).nics[0].filter == "clean-traffic"

    svc_set_nic_filter(uuid, mac, "")
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    assert root.find("devices/interface/filterref") is None


def test_unknown_mac_is_refused(testconn, domain):
    with pytest.raises(RuntimeError, match="No interface"):
        svc_set_nic_filter(domain.UUIDString(), "52:54:00:00:00:99", "x")


def test_template_is_valid_xml_with_the_name_escaped():
    root = ET.fromstring(nwfilter_template("R&D"))
    assert root.get("name") == "R&D"
    assert root.find("filterref") is not None
    assert root.find("rule") is not None
