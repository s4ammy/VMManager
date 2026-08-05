"""Host devices and PCI passthrough diagnostics."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import libvirt

from .connection import _with_conn
from .devices import _APPLIED_CONFIG, _apply_device, _detach_element, _find_device
from .guest import _agent_cmd
from .models import HostDevice, IommuDevice, IommuReport
from .xmlesc import x
from .xmlutil import _hostdev_ident

def svc_list_host_devices() -> list[HostDevice]:
    def go(conn):
        flags = (
            libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_USB_DEV
            | libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_PCI_DEV
        )
        out: list[HostDevice] = []
        for dev in conn.listAllDevices(flags):
            try:
                cap = ET.fromstring(dev.XMLDesc(0)).find("capability")
            except libvirt.libvirtError:
                continue
            if cap is None:
                continue
            vendor = cap.find("vendor")
            product = cap.find("product")
            v_name = (vendor.text or "").strip() if vendor is not None else ""
            p_name = (product.text or "").strip() if product is not None else ""
            label = " ".join(x for x in (v_name, p_name) if x) or "unknown device"
            if cap.get("type") == "usb_device":
                if vendor is None or product is None:
                    continue
                ident = (
                    f"{int(vendor.get('id', '0'), 16):04x}:"
                    f"{int(product.get('id', '0'), 16):04x}"
                )
                out.append(HostDevice(kind="usb", ident=ident, label=label))
            else:
                ident = (
                    f"{int(cap.findtext('domain') or 0):04x}:"
                    f"{int(cap.findtext('bus') or 0):02x}:"
                    f"{int(cap.findtext('slot') or 0):02x}."
                    f"{int(cap.findtext('function') or 0):x}"
                )
                out.append(HostDevice(kind="pci", ident=ident, label=label))
        out.sort(key=lambda d: (d.kind, d.label.lower()))
        return out

    return _with_conn(go)

def _hostdev_xml(kind: str, ident: str) -> str:
    if kind == "usb":
        vendor, product = ident.split(":")
        return f"""<hostdev mode='subsystem' type='usb' managed='yes'>
  <source><vendor id='0x{vendor}'/><product id='0x{product}'/></source>
</hostdev>"""
    dom_part, bus, slotfn = ident.split(":")
    slot, function = slotfn.split(".")
    return f"""<hostdev mode='subsystem' type='pci' managed='yes'>
  <source>
    <address domain='0x{dom_part}' bus='0x{bus}' slot='0x{slot}' function='0x{function}'/>
  </source>
</hostdev>"""

def svc_attach_hostdev(uuid: str, kind: str, ident: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        return _apply_device(dom, _hostdev_xml(kind, ident), "attach")

    return _with_conn(go)

def svc_detach_hostdev(uuid: str, kind: str, ident: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)

        def match(h):
            info = _hostdev_ident(h)
            return info is not None and info.kind == kind and info.ident == ident

        el, live = _find_device(dom, "devices/hostdev", match)
        if el is None:
            raise RuntimeError(f"No attached {kind} device {ident}")
        return _detach_element(dom, el, live)

    return _with_conn(go)

def svc_attach_cdrom(uuid: str, iso_path: str) -> str:
    """Add another optical drive holding `iso_path` (e.g. virtio-win)."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        for d in root.findall("devices/disk"):
            src = d.find("source")
            if d.get("device") == "cdrom" and src is not None and src.get("file") == iso_path:
                return "That disc is already attached."
        used = {t.get("dev") for t in root.findall("devices/disk/target")}
        dev = next(
            (f"sd{chr(c)}" for c in range(ord("a"), ord("z") + 1) if f"sd{chr(c)}" not in used),
            None,
        )
        if dev is None:
            raise RuntimeError("No free SATA slot for another drive")
        xml = f"""<disk type='file' device='cdrom'>
  <driver name='qemu' type='raw'/>
  <source file='{x(iso_path)}'/>
  <target dev='{x(dev)}' bus='sata'/>
  <readonly/>
</disk>"""
        return _apply_device(dom, xml, "attach")

    return _with_conn(go)

def svc_windows_tooling_state(uuid: str) -> dict:
    """What the guest still needs for a first-class Windows experience."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        state = {
            "virtio_disk": any(
                t.get("bus") == "virtio" for t in root.findall("devices/disk/target")
            ),
            "virtio_net": any(
                m.get("type") == "virtio"
                for m in root.findall("devices/interface/model")
            ),
            "agent_channel": any(
                t.get("name") == "org.qemu.guest_agent.0"
                for t in root.findall("devices/channel/target")
            ),
            "spice_agent_channel": any(
                t.get("name") == "com.redhat.spice.0"
                for t in root.findall("devices/channel/target")
            ),
            "tablet": any(
                i.get("type") == "tablet" for i in root.findall("devices/input")
            ),
            "iso_attached": any(
                "virtio-win" in (s.get("file") or "")
                for s in root.findall("devices/disk/source")
            ),
        }
        agent_ok = False
        if dom.isActive():
            try:
                _agent_cmd(dom, "guest-ping")
                agent_ok = True
            except Exception:  # noqa: BLE001 - agent simply absent
                pass
        state["agent_responding"] = agent_ok
        return state

    return _with_conn(go)

def svc_add_spice_agent_channel(uuid: str) -> str:
    """virtio-serial channel spice-vdagent uses for clipboard and resize."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        for t in root.findall("devices/channel/target"):
            if t.get("name") == "com.redhat.spice.0":
                return "Already configured."
        xml = (
            "<channel type='spicevmc'>"
            "<target type='virtio' name='com.redhat.spice.0'/></channel>"
        )
        dom.attachDeviceFlags(xml, libvirt.VIR_DOMAIN_AFFECT_CONFIG)
        return _APPLIED_CONFIG

    return _with_conn(go)

def svc_add_agent_channel(uuid: str) -> str:
    """virtio-serial channel qemu-guest-agent talks over."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        for t in root.findall("devices/channel/target"):
            if t.get("name") == "org.qemu.guest_agent.0":
                return "Already configured."
        xml = (
            "<channel type='unix'>"
            "<target type='virtio' name='org.qemu.guest_agent.0'/></channel>"
        )
        dom.attachDeviceFlags(xml, libvirt.VIR_DOMAIN_AFFECT_CONFIG)
        return _APPLIED_CONFIG

    return _with_conn(go)

def svc_attach_input(uuid: str, itype: str, bus: str) -> str:
    """Add an input device. A tablet gives precise (absolute) mouse control."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        return _apply_device(dom, f"<input type='{x(itype)}' bus='{x(bus)}'/>", "attach")

    return _with_conn(go)

def svc_detach_input(uuid: str, itype: str, bus: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        el, live = _find_device(
            dom, "devices/input",
            lambda i: i.get("type") == itype and i.get("bus") == bus,
        )
        if el is None:
            raise RuntimeError(f"No {bus} {itype} input device")
        return _detach_element(dom, el, live)

    return _with_conn(go)

def _pci_label(conn, address: str) -> str:
    """Human name for a PCI address, via libvirt's node device list."""
    try:
        name = "pci_" + address.replace(":", "_").replace(".", "_")
        dev = conn.nodeDeviceLookupByName(name)
        cap = ET.fromstring(dev.XMLDesc(0)).find("capability")
        vendor = cap.findtext("vendor") or ""
        product = cap.findtext("product") or ""
        return " ".join(x for x in (vendor.strip(), product.strip()) if x) or address
    except (libvirt.libvirtError, ET.ParseError, AttributeError):
        return address

def svc_iommu_report() -> IommuReport:
    """IOMMU groups, bound drivers, and what each device is used by."""
    import os

    def go(conn):
        base = "/sys/kernel/iommu_groups"
        if not os.path.isdir(base) or not os.listdir(base):
            return IommuReport(enabled=False, devices=())

        in_use: dict[str, str] = {}
        for dom in conn.listAllDomains():
            try:
                root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
            except libvirt.libvirtError:
                continue
            for h in root.findall("devices/hostdev"):
                info = _hostdev_ident(h)
                if info is not None and info.kind == "pci":
                    in_use[info.ident] = dom.name()

        devices: list[IommuDevice] = []
        for group in sorted(os.listdir(base), key=lambda g: int(g)):
            dev_dir = os.path.join(base, group, "devices")
            if not os.path.isdir(dev_dir):
                continue
            for address in sorted(os.listdir(dev_dir)):
                sysfs = os.path.join("/sys/bus/pci/devices", address)
                driver = ""
                link = os.path.join(sysfs, "driver")
                if os.path.islink(link):
                    driver = os.path.basename(os.readlink(link))
                pci_class = ""
                try:
                    with open(os.path.join(sysfs, "class")) as f:
                        pci_class = f.read().strip()
                except OSError:
                    pass
                devices.append(
                    IommuDevice(
                        address=address,
                        group=int(group),
                        label=_pci_label(conn, address),
                        driver=driver or "(none)",
                        is_bridge=pci_class.startswith("0x0604"),
                        attached_to=in_use.get(address),
                    )
                )
        return IommuReport(enabled=True, devices=tuple(devices))

    return _with_conn(go)

def passthrough_verdict(report: IommuReport, dev: IommuDevice) -> tuple[str, str]:
    """(status, explanation) for passing this device through.

    status is "ready", "caution" or "blocked", which the UI colours on.
    """
    if not report.enabled:
        return "blocked", (
            "IOMMU is off. Enable VT-d/AMD-Vi in firmware and add "
            "intel_iommu=on (or amd_iommu=on) to the kernel command line."
        )
    if dev.attached_to:
        return "caution", f"Already assigned to '{dev.attached_to}'."
    mates = [
        m for m in report.group_members(dev.group)
        if m.address != dev.address and not m.is_bridge
    ]
    host_held = [
        m for m in mates
        if m.driver not in ("(none)", "vfio-pci", "pcieport")
    ]
    if host_held:
        names = ", ".join(f"{m.address} ({m.driver})" for m in host_held[:3])
        return "blocked", (
            f"IOMMU group {dev.group} also holds {names}. Everything in a "
            "group must be passed through together, so the host would lose "
            "those devices. Move the card to another slot or check for an "
            "ACS-capable board."
        )
    if dev.driver == "vfio-pci":
        return "ready", (
            f"Bound to vfio-pci and alone in group {dev.group}, ready to "
            "assign."
        )
    if mates:
        return "caution", (
            f"Group {dev.group} contains {len(mates)} other device(s), all "
            "free. They will be handed to the guest as well."
        )
    return "ready", (
        f"Alone in group {dev.group}. libvirt will rebind it from "
        f"{dev.driver} to vfio-pci when the guest starts."
    )
