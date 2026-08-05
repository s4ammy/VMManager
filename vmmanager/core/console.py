"""Console endpoints, key codes, and external viewer hand-off."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import libvirt

from .connection import _with_conn
from .devices import _APPLIED_CONFIG
from .models import DisplayHealth, GraphicsInfo

def svc_display_health(uuid: str) -> DisplayHealth:
    """What the machine's own definition does for its graphical console."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        model = root.find("devices/video/model")
        accel = model.find("acceleration") if model is not None else None
        return DisplayHealth(
            graphics=tuple(
                g.get("type", "?") for g in root.findall("devices/graphics")
            ),
            video_model=(model.get("type") or "") if model is not None else "",
            accel3d=accel is not None and accel.get("accel3d") == "yes",
            spice_agent_channel=any(
                t.get("name") == "com.redhat.spice.0"
                for t in root.findall("devices/channel/target")
            ),
            tablet=any(
                i.get("type") == "tablet" for i in root.findall("devices/input")
            ),
            running=bool(dom.isActive()),
        )

    return _with_conn(go)

def svc_graphics_info(uuid: str) -> list[GraphicsInfo]:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(0))
        out: list[GraphicsInfo] = []
        for g in root.findall("devices/graphics"):
            host = g.get("listen") or ""
            socket = g.get("socket") or ""
            for listen in g.findall("listen"):
                if listen.get("type") == "socket":
                    socket = listen.get("socket") or socket
                elif listen.get("type") == "address":
                    host = listen.get("address") or host
            out.append(
                GraphicsInfo(
                    type=g.get("type", "?"),
                    host=host or "127.0.0.1",
                    port=int(g.get("port") or -1),
                    socket=socket,
                    has_password=bool(g.get("passwd")),
                    tls_port=int(g.get("tlsPort") or -1),
                )
            )
        return out

    return _with_conn(go)

def svc_add_display(uuid: str, gtype: str = "vnc") -> str:
    """Add a <graphics> device. libvirt refuses attachDeviceFlags for
    graphics, so edit the persistent XML and redefine."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        devices = root.find("devices")
        if devices is None:
            raise RuntimeError("Domain has no <devices> element")
        if any(g.get("type") == gtype for g in devices.findall("graphics")):
            return "Already configured."
        ET.SubElement(
            devices, "graphics",
            {"type": gtype, "autoport": "yes", "listen": "127.0.0.1"},
        )
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)

def svc_add_vnc_display(uuid: str) -> str:
    return svc_add_display(uuid, "vnc")

def svc_send_keys(uuid: str, keycodes: list[int]) -> None:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        dom.sendKey(libvirt.VIR_KEYCODE_SET_LINUX, 50, keycodes, len(keycodes), 0)

    _with_conn(go)
