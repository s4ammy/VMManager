"""Network filters: libvirt's per-NIC firewall rules.

libvirt ships a set of these (clean-traffic, no-mac-spoofing, ...) and lets
you define more; a NIC opts in with a <filterref>. Only the qemu system
driver implements them - the session and test drivers do not, which
svc_nwfilter_names treats as "none" rather than an error so the NIC dialog
can simply not offer them.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import libvirt

from .connection import _with_conn
from .models import NwFilterInfo
from .xmlesc import x
from .xmlutil import _editable_xml

def svc_list_nwfilters() -> list[NwFilterInfo]:
    def go(conn):
        out = []
        for f in conn.listAllNWFilters():
            chain = ""
            rules = 0
            refs: tuple[str, ...] = ()
            try:
                root = ET.fromstring(f.XMLDesc(0))
                chain = root.get("chain") or ""
                rules = len(root.findall("rule"))
                refs = tuple(
                    r.get("filter") or "" for r in root.findall("filterref")
                )
            except ET.ParseError:
                pass
            out.append(NwFilterInfo(
                name=f.name(), uuid=f.UUIDString(), chain=chain,
                rules=rules, refs=refs,
            ))
        out.sort(key=lambda i: i.name)
        return out

    return _with_conn(go)

def svc_nwfilter_names() -> list[str]:
    """Filter names for the NIC dialog; [] where the driver has none."""

    def go(conn):
        try:
            return sorted(f.name() for f in conn.listAllNWFilters())
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_SUPPORT:
                return []
            raise

    return _with_conn(go)

def svc_get_nwfilter_xml(name: str) -> str:
    def go(conn):
        return conn.nwfilterLookupByName(name).XMLDesc(0)

    return _with_conn(go)

def svc_define_nwfilter(xml: str) -> str:
    """Define (or replace) a filter; libvirt validates the rules."""

    def go(conn):
        return conn.nwfilterDefineXML(xml).name()

    return _with_conn(go)

def svc_delete_nwfilter(name: str) -> None:
    def go(conn):
        try:
            conn.nwfilterLookupByName(name).undefine()
        except libvirt.libvirtError as e:
            if "in use" in str(e):
                raise RuntimeError(
                    f"'{name}' is still referenced by a machine's interface - "
                    "take it off the NIC first"
                ) from None
            raise

    _with_conn(go)

def svc_set_nic_filter(
    uuid: str, mac: str, filter_name: str, ip: str = ""
) -> str:
    """Set or clear the filter on one interface.

    clean-traffic and friends take an IP parameter so they can pin the
    guest to one address; libvirt learns it by snooping when not given.
    Applied live where the driver allows it, on next start otherwise.
    """

    def rewrite(root) -> str | None:
        """Apply the filterref change in place; the interface XML, or None."""
        for iface in root.findall("devices/interface"):
            m = iface.find("mac")
            if m is None or m.get("address", "").lower() != mac.lower():
                continue
            old = iface.find("filterref")
            if old is not None:
                iface.remove(old)
            if filter_name:
                ref = ET.SubElement(iface, "filterref")
                ref.set("filter", filter_name)
                if ip:
                    param = ET.SubElement(ref, "parameter")
                    param.set("name", "IP")
                    param.set("value", ip)
            return ET.tostring(iface, encoding="unicode")
        return None

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        if dom.isActive():
            live_root = _editable_xml(dom, live=True)
            iface_xml = rewrite(live_root)
            if iface_xml is not None:
                try:
                    dom.updateDeviceFlags(
                        iface_xml,
                        libvirt.VIR_DOMAIN_AFFECT_LIVE
                        | libvirt.VIR_DOMAIN_AFFECT_CONFIG,
                    )
                    return "applied"
                except libvirt.libvirtError:
                    pass  # fall through to the persistent config
        proot = _editable_xml(dom)
        iface_xml = rewrite(proot)
        if iface_xml is None:
            raise RuntimeError(f"No interface with MAC {mac}")
        try:
            dom.updateDeviceFlags(iface_xml, libvirt.VIR_DOMAIN_AFFECT_CONFIG)
        except libvirt.libvirtError:
            # not every driver can update an interface in place
            conn.defineXML(ET.tostring(proot, encoding="unicode"))
        return "applies on next start"

    return _with_conn(go)

NWFILTER_TEMPLATE = """<filter name='{name}' chain='root'>
  <!-- start from libvirt's own: reference filters combine -->
  <filterref filter='clean-traffic'/>
  <!-- then add rules; priority orders them, lower runs first -->
  <rule action='drop' direction='in' priority='500'>
    <tcp dstportstart='23' dstportend='23'/>
  </rule>
</filter>"""

def nwfilter_template(name: str) -> str:
    """A starting point for a new filter, valid as it stands."""
    return NWFILTER_TEMPLATE.format(name=x(name))
