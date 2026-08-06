"""Small helpers for reading and rewriting domain XML."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import libvirt

from .xmlesc import x
from .models import HostdevInfo

def _hostdev_ident(h: ET.Element) -> HostdevInfo | None:
    kind = h.get("type", "")
    src = h.find("source")
    if src is None:
        return None
    if kind == "usb":
        vendor = src.find("vendor")
        product = src.find("product")
        if vendor is None or product is None:
            return None
        return HostdevInfo(
            kind="usb",
            ident=f"{int(vendor.get('id', '0'), 16):04x}:{int(product.get('id', '0'), 16):04x}",
            startup_policy=src.get("startupPolicy", "mandatory"),
        )
    if kind == "pci":
        a = src.find("address")
        if a is None:
            return None
        rom = h.find("rom")
        return HostdevInfo(
            kind="pci",
            ident=(
                f"{int(a.get('domain', '0'), 16):04x}:{int(a.get('bus', '0'), 16):02x}:"
                f"{int(a.get('slot', '0'), 16):02x}.{int(a.get('function', '0'), 16):x}"
            ),
            rom_file=rom.get("file", "") if rom is not None else "",
            rom_bar=(rom.get("bar", "on") != "off") if rom is not None else True,
        )
    if kind == "mdev":
        a = src.find("address")
        if a is None or not a.get("uuid"):
            return None
        return HostdevInfo(kind="mdev", ident=a.get("uuid"))
    return None

def _boot_entries(root: ET.Element) -> tuple[str, ...]:
    """Per-device boot order when present, else the os-level dev list."""
    ordered: list[tuple[int, str]] = []
    for d in root.findall("devices/disk"):
        b = d.find("boot")
        if b is not None:
            target = d.find("target")
            dev = target.get("dev", "?") if target is not None else "?"
            ordered.append((int(b.get("order", 0)), f"{d.get('device', 'disk')} {dev}"))
    for n in root.findall("devices/interface"):
        b = n.find("boot")
        if b is not None:
            mac = n.find("mac")
            ordered.append((int(b.get("order", 0)), f"nic {mac.get('address', '?') if mac is not None else '?'}"))
    if ordered:
        ordered.sort()
        return tuple(entry for _, entry in ordered)
    return tuple(b.get("dev", "?") for b in root.findall("os/boot"))

_DEV_PREFIX = {"virtio": "vd", "sata": "sd", "scsi": "sd", "usb": "sd", "ide": "hd"}

def _next_disk_target(root: ET.Element, bus: str) -> str:
    prefix = _DEV_PREFIX.get(bus, "vd")
    used = {t.get("dev") for t in root.findall("devices/disk/target")}
    i = 0
    while True:
        n, suffix = i, ""
        while True:
            suffix = chr(ord("a") + n % 26) + suffix
            n = n // 26 - 1
            if n < 0:
                break
        if prefix + suffix not in used:
            return prefix + suffix
        i += 1

# Rows that are a property of the machine rather than a device in it, and
# the top-level elements each one is made of. Without an entry here the XML
# view goes looking for a <labels> device and tells you it is missing.
# Reading a definition in order to write it back is not the same as reading
# it to look at. libvirt leaves security-sensitive values out of XMLDesc
# unless asked for them - the display password, chiefly - so an edit that
# reads the plain form, changes one element and hands the whole thing to
# defineXML deletes every secret in it on the way past. Anything that
# redefines a domain reads through here.
def _editable_xml(dom, live: bool = False) -> ET.Element:
    """The definition to edit and hand back, secrets included."""
    flags = libvirt.VIR_DOMAIN_XML_SECURE
    if not live:
        flags |= libvirt.VIR_DOMAIN_XML_INACTIVE
    return ET.fromstring(dom.XMLDesc(flags))


_SYSTEM_ITEM_TAGS = {
    "cpu": ("vcpu", "cpu"),
    "mem": ("memory", "currentMemory", "memoryBacking"),
    "boot": ("os",),
    "labels": ("title", "description"),
    "tune": ("cputune", "memtune", "memoryBacking", "iothreads"),
    "features": ("features", "cpu"),
    "ports": ("devices/controller[@model='pcie-root-port']",),
}

# Devices a machine has at most one of, so the row carries no identity and
# the tag alone finds it. Without these the XML view of a watchdog - or a
# vsock, or the audio backend - reports the device as missing.
_SIMPLE_DEVICE_TAGS = {
    "watchdog": "watchdog", "vsock": "vsock", "redir": "redirdev",
    "panic": "panic", "smartcard": "smartcard", "audio": "audio",
    "dimm": "memory",
}


def _find_device_element(root: ET.Element, kind: str, ident: str) -> ET.Element | None:
    devices = root.find("devices")
    if devices is None:
        return None
    if kind in ("disk", "cdrom"):
        for d in devices.findall("disk"):
            t = d.find("target")
            if t is not None and t.get("dev") == ident:
                return d
    elif kind == "nic":
        for n in devices.findall("interface"):
            m = n.find("mac")
            if m is not None and m.get("address", "").lower() == ident.lower():
                return n
    elif kind == "video":
        return devices.find("video")
    elif kind == "gfx":
        for g in devices.findall("graphics"):
            if g.get("type") == ident:
                return g
    elif kind == "sound":
        for s in devices.findall("sound"):
            if s.get("model") == ident:
                return s
    elif kind == "input":
        itype, bus = ident.split("/")
        for i in devices.findall("input"):
            if i.get("type") == itype and i.get("bus") == bus:
                return i
    elif kind in ("usb", "pci", "mdev"):
        for h in devices.findall("hostdev"):
            info = _hostdev_ident(h)
            if info is not None and info.kind == kind and info.ident == ident:
                return h
    elif kind == "controller":
        ctype, _, index = ident.partition("/")
        for c in devices.findall("controller"):
            if c.get("type") == ctype and c.get("index", "0") == index:
                return c
    elif kind in _SIMPLE_DEVICE_TAGS:
        return devices.find(_SIMPLE_DEVICE_TAGS[kind])
    elif kind == "fs":
        for f in devices.findall("filesystem"):
            t = f.find("target")
            if t is not None and t.get("dir") == ident:
                return f
    return None

def _pretty_xml(el: ET.Element) -> str:
    import copy

    clone = copy.deepcopy(el)
    ET.indent(clone, space="  ")
    return ET.tostring(clone, encoding="unicode").strip()

def _network_xml(
    name: str,
    mode: str,
    subnet: str = "",
    dhcp_start: str = "",
    dhcp_end: str = "",
    bridge_dev: str = "",
) -> str:
    if mode == "bridge":
        return (
            f"<network><name>{x(name)}</name>"
            f"<forward mode='bridge'/><bridge name='{x(bridge_dev)}'/></network>"
        )
    import ipaddress

    net = ipaddress.ip_network(subnet, strict=False)
    gateway = str(next(net.hosts()))
    dhcp = ""
    if dhcp_start and dhcp_end:
        dhcp = f"<dhcp><range start='{x(dhcp_start)}' end='{x(dhcp_end)}'/></dhcp>"
    forward = "<forward mode='nat'/>" if mode == "nat" else ""
    return f"""<network>
  <name>{x(name)}</name>
  {forward}
  <ip address='{gateway}' netmask='{net.netmask}'>
    {dhcp}
  </ip>
</network>"""

def _backup_disks(root: ET.Element) -> list[str]:
    """Target devs of file-backed data disks - what we can back up."""
    out = []
    for d in root.findall("devices/disk"):
        if d.get("device") != "disk":
            continue
        src, target = d.find("source"), d.find("target")
        if src is not None and src.get("file") and target is not None:
            out.append(target.get("dev"))
    return out
