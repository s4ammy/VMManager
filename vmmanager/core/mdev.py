"""Mediated devices (vGPU and friends) and SR-IOV.

A card whose driver supports mediation advertises types under
/sys/class/mdev_bus; an mdev is an instance of one, assigned to a guest
like a PCI device. Creating one needs root, so it goes through libvirt's
node-device API rather than writing sysfs ourselves.

SR-IOV is read-only here: which PCI devices are PFs, how many VFs are
enabled, and which VFs exist - the VFs themselves are ordinary PCI
passthrough. Setting the VF count is a host-configuration job (a udev rule
or the NIC driver's tooling), not something libvirt offers.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import libvirt

from .connection import _with_conn
from .models import MdevInfo, MdevType, SriovPf
from .xmlesc import x
from .xmlutil import _hostdev_ident

def read_mdev_types(root: str = "/sys") -> list[MdevType]:
    """Walk the mdev bus. Pure filesystem, so a test can hand it a fake."""
    base = os.path.join(root, "class/mdev_bus")
    out: list[MdevType] = []
    if not os.path.isdir(base):
        return out
    for parent in sorted(os.listdir(base)):
        types_dir = os.path.join(base, parent, "mdev_supported_types")
        if not os.path.isdir(types_dir):
            continue
        for type_id in sorted(os.listdir(types_dir)):
            tdir = os.path.join(types_dir, type_id)

            def field(name: str) -> str:
                try:
                    with open(os.path.join(tdir, name)) as f:
                        return f.read().strip()
                except OSError:
                    return ""

            out.append(MdevType(
                parent=parent,
                type_id=type_id,
                name=field("name") or type_id,
                api=field("device_api"),
                available=int(field("available_instances") or 0),
            ))
    return out

def read_sriov_pfs(root: str = "/sys") -> list[SriovPf]:
    """Every SR-IOV physical function, with its enabled VFs."""
    base = os.path.join(root, "bus/pci/devices")
    out: list[SriovPf] = []
    if not os.path.isdir(base):
        return out
    for address in sorted(os.listdir(base)):
        dev = os.path.join(base, address)
        try:
            with open(os.path.join(dev, "sriov_totalvfs")) as f:
                total = int(f.read().strip() or 0)
        except (OSError, ValueError):
            continue
        if total <= 0:
            continue
        try:
            with open(os.path.join(dev, "sriov_numvfs")) as f:
                num = int(f.read().strip() or 0)
        except (OSError, ValueError):
            num = 0
        interface = ""
        net_dir = os.path.join(dev, "net")
        if os.path.isdir(net_dir):
            names = sorted(os.listdir(net_dir))
            interface = names[0] if names else ""
        vfs = []
        for entry in sorted(os.listdir(dev)):
            if entry.startswith("virtfn"):
                link = os.path.join(dev, entry)
                if os.path.islink(link):
                    vfs.append(os.path.basename(os.readlink(link)))
        out.append(SriovPf(
            address=address, interface=interface,
            numvfs=num, totalvfs=total, vfs=tuple(sorted(vfs)),
        ))
    return out

def svc_mdev_types() -> list[MdevType]:
    return read_mdev_types()

def svc_sriov_pfs() -> list[SriovPf]:
    return read_sriov_pfs()

def mdev_nodedev_xml(parent: str, type_id: str) -> str:
    """What libvirt needs to create an mdev instance.

    The parent is a PCI address; node-device names replace the
    punctuation with underscores.
    """
    nodedev = "pci_" + parent.replace(":", "_").replace(".", "_")
    return (
        "<device>\n"
        f"  <parent>{x(nodedev)}</parent>\n"
        "  <capability type='mdev'>\n"
        f"    <type id='{x(type_id)}'/>\n"
        "  </capability>\n"
        "</device>"
    )

def parse_mdev_nodedev(xml: str) -> MdevInfo | None:
    """An MdevInfo out of a node device's XML, or None if it is not one."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    cap = root.find("capability[@type='mdev']")
    if cap is None:
        return None
    uuid = cap.findtext("uuid") or ""
    if not uuid:
        return None
    t = cap.find("type")
    parent = root.findtext("parent") or ""
    if parent.startswith("pci_"):
        p = parent[len("pci_"):].split("_")
        if len(p) == 4:
            parent = f"{p[0]}:{p[1]}:{p[2]}.{p[3]}"
    return MdevInfo(
        uuid=uuid,
        parent=parent,
        type_id=t.get("id", "") if t is not None else "",
        attached_to=None,
    )

def svc_list_mdevs() -> list[MdevInfo]:
    """Existing mdev instances, and which machine each is assigned to."""

    def go(conn):
        try:
            devices = conn.listAllDevices(
                libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_MDEV
            )
        except libvirt.libvirtError:
            return []
        in_use: dict[str, str] = {}
        for dom in conn.listAllDomains():
            try:
                root = ET.fromstring(
                    dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
                )
            except libvirt.libvirtError:
                continue
            for h in root.findall("devices/hostdev"):
                info = _hostdev_ident(h)
                if info is not None and info.kind == "mdev":
                    in_use[info.ident.lower()] = dom.name()
        out = []
        for dev in devices:
            info = parse_mdev_nodedev(dev.XMLDesc(0))
            if info is not None:
                out.append(MdevInfo(
                    uuid=info.uuid, parent=info.parent,
                    type_id=info.type_id,
                    attached_to=in_use.get(info.uuid.lower()),
                ))
        out.sort(key=lambda m: (m.parent, m.type_id))
        return out

    return _with_conn(go)

def svc_create_mdev(parent: str, type_id: str) -> str:
    """Create an mdev instance through libvirt; returns its UUID.

    Transient: it is gone after a host reboot. Persisting one needs
    mdevctl, which is out of scope here and said so in the dialog.
    """

    def go(conn):
        dev = conn.nodeDeviceCreateXML(mdev_nodedev_xml(parent, type_id), 0)
        info = parse_mdev_nodedev(dev.XMLDesc(0))
        if info is None:
            raise RuntimeError("libvirt created the device but reported no UUID")
        return info.uuid

    return _with_conn(go)

def svc_delete_mdev(uuid: str) -> None:
    def go(conn):
        try:
            devices = conn.listAllDevices(
                libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_MDEV
            )
        except libvirt.libvirtError as e:
            raise RuntimeError(str(e)) from None
        for dev in devices:
            info = parse_mdev_nodedev(dev.XMLDesc(0))
            if info is not None and info.uuid.lower() == uuid.lower():
                dev.destroy()
                return
        raise RuntimeError(f"No mediated device with UUID {uuid}")

    _with_conn(go)
