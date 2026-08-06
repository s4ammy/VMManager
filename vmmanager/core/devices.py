"""Hardware inventory and device add/remove/edit, including per-device XML."""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET

import libvirt

from .connection import _with_conn, current_uri
from .models import DiskInfo, FsShareInfo, GraphicsDetail, Hardware, NicInfo
from .xmlesc import x
from .xmlutil import _SYSTEM_ITEM_TAGS, _boot_entries, _editable_xml, _find_device_element, _hostdev_ident, _next_disk_target, _pretty_xml

def svc_get_hardware(uuid: str) -> Hardware:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        # Persistent config, not the live view: this is what edits change,
        # so pending (next-boot) devices show up right away. SECURE as well,
        # or libvirt redacts a display's password and the field that shows
        # it would always read empty and quietly clear it on save.
        root = ET.fromstring(dom.XMLDesc(
            libvirt.VIR_DOMAIN_XML_INACTIVE | libvirt.VIR_DOMAIN_XML_SECURE
        ))
        os_el = root.find("os")
        machine = os_el.find("type").get("machine", "?") if os_el is not None else "?"
        firmware = "UEFI" if (
            (os_el is not None and os_el.get("firmware") == "efi")
            or root.find("os/loader") is not None
        ) else "BIOS"
        cpu = root.find("cpu")
        cpu_mode = cpu.get("mode", "custom") if cpu is not None else "default"
        vcpu_el = root.find("vcpu")
        mem_el = root.find("memory")
        cur_el = root.find("currentMemory")
        disks = []
        for d in root.findall("devices/disk"):
            target = d.find("target")
            source = d.find("source")
            driver = d.find("driver")
            src = ""
            if source is not None:
                src = source.get("file") or source.get("dev") or source.get("name") or ""
            disks.append(
                DiskInfo(
                    dev=target.get("dev", "?") if target is not None else "?",
                    bus=target.get("bus", "?") if target is not None else "?",
                    source=src or f"({d.get('device', 'disk')}, empty)",
                    format=driver.get("type", "raw") if driver is not None else "raw",
                    device=d.get("device", "disk"),
                    cache=driver.get("cache", "default") if driver is not None else "default",
                    readonly=d.find("readonly") is not None,
                    shareable=d.find("shareable") is not None,
                    serial=d.findtext("serial") or "",
                    discard=driver.get("discard", "") if driver is not None else "",
                )
            )
        nics = []
        for n in root.findall("devices/interface"):
            mac = n.find("mac")
            model = n.find("model")
            src_el = n.find("source")
            src = ""
            if src_el is not None:
                src = (
                    src_el.get("network")
                    or src_el.get("bridge")
                    or src_el.get("dev")
                    or ""
                )
            fref = n.find("filterref")
            link = n.find("link")
            nics.append(
                NicInfo(
                    mac=mac.get("address", "?") if mac is not None else "?",
                    source=src,
                    model=model.get("type", "?") if model is not None else "?",
                    filter=fref.get("filter", "") if fref is not None else "",
                    link_up=link is None or link.get("state", "up") != "down",
                )
            )
        hostdevs = []
        for h in root.findall("devices/hostdev"):
            info = _hostdev_ident(h)
            if info is not None:
                hostdevs.append(info)
        filesystems = []
        for f in root.findall("devices/filesystem"):
            src_el = f.find("source")
            tgt_el = f.find("target")
            drv_el = f.find("driver")
            driver = drv_el.get("type", "") if drv_el is not None else ""
            filesystems.append(
                FsShareInfo(
                    tag=tgt_el.get("dir", "?") if tgt_el is not None else "?",
                    source=src_el.get("dir", "?") if src_el is not None else "?",
                    driver="virtiofs" if driver == "virtiofs" else "9p",
                )
            )
        graphics = []
        for g in root.findall("devices/graphics"):
            listen = g.find("listen")
            gl = g.find("gl")
            port_text = g.get("port") or "-1"
            graphics.append(GraphicsDetail(
                type=g.get("type", "?"),
                ident=display_ident(g),
                listen_type=(
                    listen.get("type", "address") if listen is not None
                    else ("socket" if g.get("socket") else "address")
                ),
                address=(
                    (listen.get("address") if listen is not None else None)
                    or g.get("listen") or ""
                ),
                port=int(port_text) if port_text.lstrip("-").isdigit() else -1,
                autoport=g.get("autoport", "yes") != "no",
                password=g.get("passwd") or "",
                gl=gl is not None and gl.get("enable") == "yes",
                socket=(
                    (listen.get("socket") if listen is not None else None)
                    or g.get("socket") or ""
                ),
            ))
        video_el = root.find("devices/video/model")
        video = video_el.get("type", "?") if video_el is not None else "none"
        mem_kb = int(mem_el.text or 0) if mem_el is not None else 0
        cur_kb = int(cur_el.text or 0) if cur_el is not None else mem_kb
        topo_el = root.find("cpu/topology")
        topology = None
        if topo_el is not None:
            topology = (
                int(topo_el.get("sockets", 1)),
                int(topo_el.get("cores", 1)),
                int(topo_el.get("threads", 1)),
            )
        sounds = tuple(
            s.get("model", "?") for s in root.findall("devices/sound")
        )
        inputs = tuple(
            (i.get("type", "?"), i.get("bus", "?"))
            for i in root.findall("devices/input")
        )
        menu_el = root.find("os/bootmenu")
        accel_el = root.find("devices/video/model/acceleration")
        wd_el = root.find("devices/watchdog")
        vsock_el = root.find("devices/vsock")
        vsock = ""
        if vsock_el is not None:
            cid = vsock_el.find("cid")
            vsock = (
                "auto" if cid is None or cid.get("auto") == "yes"
                else cid.get("address", "?")
            )
        panic_el = root.find("devices/panic")
        smartcard_el = root.find("devices/smartcard")
        audio_el = root.find("devices/audio")
        memory_devices = tuple(
            int(size.text or 0) // (1 if (size.get("unit") == "MiB") else 1024)
            for size in root.findall("devices/memory/target/size")
        )
        controllers = tuple(
            (c.get("type", "?"), int(c.get("index", 0)), c.get("model", "default"))
            for c in root.findall("devices/controller")
            if c.get("model")
        )
        tpm_el = root.find("devices/tpm")
        tpm_backend = tpm_el.find("backend") if tpm_el is not None else None
        rng_el = root.find("devices/rng")
        rng_backend = rng_el.find("backend") if rng_el is not None else None
        emulator = root.findtext("devices/emulator") or ""
        access = root.find("memoryBacking/access")
        return Hardware(
            uuid=root.findtext("uuid") or "",
            hypervisor=root.get("type", "?"),
            arch=(
                os_el.find("type").get("arch", "?")
                if os_el is not None and os_el.find("type") is not None else "?"
            ),
            emulator=emulator,
            shared_memory=access is not None and access.get("mode") == "shared",
            machine=machine,
            firmware=firmware,
            cpu_mode=cpu_mode,
            vcpus=int(vcpu_el.text or 0) if vcpu_el is not None else 0,
            memory_mb=cur_kb // 1024,
            max_memory_mb=mem_kb // 1024,
            disks=tuple(disks),
            nics=tuple(nics),
            hostdevs=tuple(hostdevs),
            filesystems=tuple(filesystems),
            graphics=tuple(graphics),
            video=video,
            boot=_boot_entries(root),
            topology=topology,
            sounds=sounds,
            inputs=inputs,
            title=root.findtext("title") or "",
            description=root.findtext("description") or "",
            boot_menu=menu_el is not None and menu_el.get("enable") == "yes",
            video_accel3d=accel_el is not None and accel_el.get("accel3d") == "yes",
            watchdog=(
                (wd_el.get("model", "?"), wd_el.get("action", "reset"))
                if wd_el is not None else None
            ),
            redirdevs=len(root.findall("devices/redirdev")),
            vsock=vsock,
            panic=panic_el.get("model", "isa") if panic_el is not None else "",
            smartcard=smartcard_el.get("mode", "?") if smartcard_el is not None else "",
            audio=audio_el.get("type", "?") if audio_el is not None else "",
            memory_devices=memory_devices,
            controllers=controllers,
            tpm=tpm_el.get("model", "tpm-crb") if tpm_el is not None else "",
            tpm_version=(
                tpm_backend.get("version", "2.0") if tpm_backend is not None else ""
            ),
            rng=(
                (rng_backend.text or "/dev/urandom").strip()
                if rng_backend is not None else ""
            ),
            rng_model=rng_el.get("model", "virtio") if rng_el is not None else "",
        )

    return _with_conn(go)

def svc_qemu_cmdline(uuid: str) -> str:
    def go(conn):
        xml = conn.lookupByUUIDString(uuid).XMLDesc(0)
        argv = conn.domainXMLToNative("qemu-argv", xml)
        return argv.replace(" -", " \\\n  -")

    return _with_conn(go)

_APPLIED_LIVE = "Applied to the running machine and saved to its configuration."

_APPLIED_CONFIG = "Saved to configuration - takes effect on next start."

def _apply_device(dom, xml: str, action: str) -> str:
    """attach/detach/update a device, live when the domain runs.

    On a machine that is not running, an attach through the device API and a
    rewrite of the definition are the same edit - and drivers differ in
    which device types they accept through the first. So a refused attach
    on a stopped machine falls back to writing the definition rather than
    reporting a device the app could perfectly well have added.
    """
    fn = {
        "attach": dom.attachDeviceFlags,
        "detach": dom.detachDeviceFlags,
        "update": dom.updateDeviceFlags,
    }[action]
    config = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if dom.isActive():
        try:
            fn(xml, config | libvirt.VIR_DOMAIN_AFFECT_LIVE)
            return _APPLIED_LIVE
        except libvirt.libvirtError:
            fn(xml, config)
            return _APPLIED_CONFIG
    try:
        fn(xml, config)
    except libvirt.libvirtError:
        if action != "attach":
            raise
        return _define_with_device(dom.connect(), dom, xml)
    return _APPLIED_CONFIG

def _find_device(dom, xpath: str, match):
    """Locate a device element, preferring the live XML; returns
    (element, was_in_live) or (None, False)."""
    if dom.isActive():
        for el in ET.fromstring(dom.XMLDesc(0)).findall(xpath):
            if match(el):
                return el, True
    for el in ET.fromstring(
        dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
    ).findall(xpath):
        if match(el):
            return el, False
    return None, False

def _detach_element(dom, el, in_live: bool) -> str:
    xml = ET.tostring(el, encoding="unicode")
    config = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if in_live:
        try:
            dom.detachDeviceFlags(xml, config | libvirt.VIR_DOMAIN_AFFECT_LIVE)
            return _APPLIED_LIVE
        except libvirt.libvirtError:
            pass
    dom.detachDeviceFlags(xml, config)
    return _APPLIED_CONFIG

def _detect_format(conn, path: str) -> str:
    try:
        vol = conn.storageVolLookupByPath(path)
        fmt = ET.fromstring(vol.XMLDesc(0)).find("target/format")
        if fmt is not None:
            return fmt.get("type", "raw")
    except libvirt.libvirtError:
        pass
    return "qcow2" if path.endswith(".qcow2") else "raw"

def svc_attach_disk(uuid: str, path: str, bus: str, fmt: str | None = None) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(0))
        dev = _next_disk_target(root, bus)
        disk_fmt = fmt or _detect_format(conn, path)
        xml = f"""<disk type='file' device='disk'>
  <driver name='qemu' type='{x(disk_fmt)}' discard='unmap'/>
  <source file='{x(path)}'/>
  <target dev='{x(dev)}' bus='{x(bus)}'/>
</disk>"""
        return _apply_device(dom, xml, "attach")

    return _with_conn(go)

def svc_detach_disk(uuid: str, target_dev: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        el, live = _find_device(
            dom, "devices/disk",
            lambda d: (t := d.find("target")) is not None and t.get("dev") == target_dev,
        )
        if el is None:
            raise RuntimeError(f"No disk with target '{target_dev}'")
        return _detach_element(dom, el, live)

    return _with_conn(go)

def svc_change_media(uuid: str, target_dev: str, iso_path: str | None) -> str:
    """Insert or eject cdrom media on an existing drive."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(0))
        for d in root.findall("devices/disk"):
            t = d.find("target")
            if d.get("device") == "cdrom" and t is not None and t.get("dev") == target_dev:
                source = f"<source file='{x(iso_path)}'/>" if iso_path else ""
                xml = f"""<disk type='file' device='cdrom'>
  <driver name='qemu' type='raw'/>
  {source}
  <target dev='{x(target_dev)}' bus='{x(t.get("bus", "sata"))}'/>
  <readonly/>
</disk>"""
                return _apply_device(dom, xml, "update")
        raise RuntimeError(f"No cdrom drive at '{target_dev}'")

    return _with_conn(go)

def svc_attach_nic(uuid: str, network: str, model: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        xml = f"""<interface type='network'>
  <source network='{x(network)}'/>
  <model type='{x(model)}'/>
</interface>"""
        return _apply_device(dom, xml, "attach")

    return _with_conn(go)

def svc_detach_nic(uuid: str, mac: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        el, live = _find_device(
            dom, "devices/interface",
            lambda n: (m := n.find("mac")) is not None
            and m.get("address", "").lower() == mac.lower(),
        )
        if el is None:
            raise RuntimeError(f"No interface with MAC {mac}")
        return _detach_element(dom, el, live)

    return _with_conn(go)

def svc_set_vcpus(uuid: str, count: int) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        config = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if dom.isActive():
            try:
                dom.setVcpusFlags(count, config | libvirt.VIR_DOMAIN_AFFECT_LIVE)
                return _APPLIED_LIVE
            except libvirt.libvirtError:
                pass
        # raise the config maximum first so the new count always fits
        try:
            dom.setVcpusFlags(count, config | libvirt.VIR_DOMAIN_VCPU_MAXIMUM)
        except libvirt.libvirtError:
            pass
        dom.setVcpusFlags(count, config)
        return _APPLIED_CONFIG

    return _with_conn(go)

def svc_set_memory(uuid: str, current_mb: int, max_mb: int) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        config = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        old_max_kb = int(root.findtext("memory") or 0)
        if max_mb * 1024 != old_max_kb:
            dom.setMemoryFlags(max_mb * 1024, config | libvirt.VIR_DOMAIN_MEM_MAXIMUM)
        if dom.isActive():
            try:
                dom.setMemoryFlags(current_mb * 1024, config | libvirt.VIR_DOMAIN_AFFECT_LIVE)
                return _APPLIED_LIVE
            except libvirt.libvirtError:
                pass
        dom.setMemoryFlags(current_mb * 1024, config)
        return _APPLIED_CONFIG

    return _with_conn(go)

def svc_set_boot_order(uuid: str, entries: list[str]) -> str:
    """Reorder boot entries as returned by Hardware.boot.

    Per-device entries look like "disk vda" / "nic 52:54:…"; plain entries
    ("hd", "cdrom", "network") are os-level boot devs. An empty list is
    refused: libvirt accepts it and the machine then boots from nothing,
    which looks like a broken disk rather than a setting.
    """
    if not entries:
        raise ValueError(
            "A machine has to be able to boot from something - leave at "
            "least one device in the boot order."
        )

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        per_device = any(" " in e for e in entries)
        os_el = root.find("os")
        if os_el is None:
            raise RuntimeError("Domain has no <os> element")
        if per_device:
            order = {e: i + 1 for i, e in enumerate(entries)}
            for d in root.findall("devices/disk"):
                t = d.find("target")
                key = f"{d.get('device', 'disk')} {t.get('dev', '?') if t is not None else '?'}"
                b = d.find("boot")
                if key in order:
                    if b is None:
                        b = ET.SubElement(d, "boot")
                    b.set("order", str(order[key]))
                elif b is not None:
                    d.remove(b)
            for n in root.findall("devices/interface"):
                m = n.find("mac")
                key = f"nic {m.get('address', '?') if m is not None else '?'}"
                b = n.find("boot")
                if key in order:
                    if b is None:
                        b = ET.SubElement(n, "boot")
                    b.set("order", str(order[key]))
                elif b is not None:
                    n.remove(b)
            for b in os_el.findall("boot"):
                os_el.remove(b)
        else:
            for b in os_el.findall("boot"):
                os_el.remove(b)
            for dev in entries:
                ET.SubElement(os_el, "boot", {"dev": dev})
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)

def svc_get_device_xml(uuid: str, kind: str, ident: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        if kind in _SYSTEM_ITEM_TAGS:
            parts = [
                _pretty_xml(el)
                for tag in _SYSTEM_ITEM_TAGS[kind]
                for el in root.findall(tag)
            ]
            if not parts:
                # Nothing set is a normal state for a title, or for tuning
                # nobody has touched. Saying so beats an error dialog.
                return f"<!-- nothing set on this machine for '{kind}' -->"
            return "\n".join(parts)
        el = _find_device_element(root, kind, ident)
        if el is None:
            raise RuntimeError(f"Device not found ({kind} {ident})")
        return _pretty_xml(el)

    return _with_conn(go)

def svc_set_device_xml(uuid: str, kind: str, ident: str, text: str) -> str:
    try:
        frag = ET.fromstring(f"<vmm-wrap>{text}</vmm-wrap>")
    except ET.ParseError as e:
        raise RuntimeError(f"Invalid XML: {e}") from e
    if not len(frag):
        raise RuntimeError("No XML element to apply")

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        if kind in _SYSTEM_ITEM_TAGS:
            allowed = set(_SYSTEM_ITEM_TAGS[kind])
            for child in frag:
                if child.tag not in allowed:
                    raise RuntimeError(
                        f"<{child.tag}> doesn't belong to this item "
                        f"(expected {', '.join(sorted(allowed))})"
                    )
            for child in frag:
                old = root.find(child.tag)
                if old is not None:
                    idx = list(root).index(old)
                    root.remove(old)
                    root.insert(idx, child)
                else:
                    root.append(child)
        else:
            old = _find_device_element(root, kind, ident)
            devices = root.find("devices")
            if old is None or devices is None:
                raise RuntimeError(f"Device not found ({kind} {ident})")
            idx = list(devices).index(old)
            devices.remove(old)
            for offset, child in enumerate(frag):
                devices.insert(idx + offset, child)
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return "Saved - applies on next start."

    return _with_conn(go)

def _ensure_shared_memory(conn, dom) -> bool:
    """virtiofs needs shared memory backing; add it to the config if missing."""
    root = _editable_xml(dom)
    mb = root.find("memoryBacking")
    access = mb.find("access") if mb is not None else None
    if access is not None and access.get("mode") == "shared":
        return False
    if mb is None:
        mb = ET.SubElement(root, "memoryBacking")
    if mb.find("source") is None:
        ET.SubElement(mb, "source", {"type": "memfd"})
    if access is None:
        ET.SubElement(mb, "access", {"mode": "shared"})
    else:
        access.set("mode", "shared")
    conn.defineXML(ET.tostring(root, encoding="unicode"))
    return True

def svc_attach_filesystem(uuid: str, source_dir: str, tag: str, driver: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        if driver == "virtiofs":
            memory_changed = _ensure_shared_memory(conn, dom)
            xml = f"""<filesystem type='mount' accessmode='passthrough'>
  <driver type='virtiofs'/>
  <source dir='{x(source_dir)}'/>
  <target dir='{x(tag)}'/>
</filesystem>"""
            if memory_changed and dom.isActive():
                dom.attachDeviceFlags(xml, libvirt.VIR_DOMAIN_AFFECT_CONFIG)
                return (
                    "Share saved. Shared memory backing was enabled too - "
                    "restart the machine to mount it."
                )
        else:
            xml = f"""<filesystem type='mount' accessmode='mapped'>
  <source dir='{x(source_dir)}'/>
  <target dir='{x(tag)}'/>
</filesystem>"""
        return _apply_device(dom, xml, "attach")

    return _with_conn(go)

def svc_detach_filesystem(uuid: str, tag: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        el, live = _find_device(
            dom, "devices/filesystem",
            lambda f: (t := f.find("target")) is not None and t.get("dir") == tag,
        )
        if el is None:
            raise RuntimeError(f"No share tagged '{tag}'")
        return _detach_element(dom, el, live)

    return _with_conn(go)

def svc_set_cpu(
    uuid: str, mode: str, sockets: int, cores: int, threads: int
) -> str:
    """CPU model mode + topology; vcpu count becomes the product."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        cpu = root.find("cpu")
        if cpu is None:
            cpu = ET.SubElement(root, "cpu")
        cpu.attrib.clear()
        for el in list(cpu):
            if el.tag in ("topology", "model"):
                cpu.remove(el)
        cpu.set("mode", mode)
        if mode == "custom":
            cpu.set("match", "exact")
            model = ET.SubElement(cpu, "model")
            model.set("fallback", "allow")
            model.text = "qemu64"
        total = max(1, sockets * cores * threads)
        ET.SubElement(
            cpu, "topology",
            {"sockets": str(sockets), "cores": str(cores), "threads": str(threads)},
        )
        vcpu = root.find("vcpu")
        if vcpu is None:
            vcpu = ET.SubElement(root, "vcpu")
        vcpu.text = str(total)
        vcpu.attrib.pop("current", None)
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)

# Attributes on <model> that mean the same thing whatever the adapter is,
# so they survive a change of model. The rest - ram, vram, vgamem - are
# sized for the model that was there and do not carry over.
_VIDEO_KEEP = ("heads", "primary")


def svc_set_video(uuid: str, model: str) -> str:
    """Change the video adapter, keeping how it was set up.

    Rebuilding the element from the model name alone loses everything else
    on it, and `heads` is the one that matters: a QXL adapter set up for
    two monitors quietly comes back as one. Same model means keep the lot;
    a different model keeps only what still applies to it.
    """

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        devices = root.find("devices")
        if devices is None:
            raise RuntimeError("Domain has no <devices> element")
        existing = devices.findall("video")
        if model == "none":
            for v in existing:
                devices.remove(v)
            conn.defineXML(ET.tostring(root, encoding="unicode"))
            return _APPLIED_CONFIG
        if not existing:
            video = ET.SubElement(devices, "video")
            ET.SubElement(video, "model", {"type": model})
            conn.defineXML(ET.tostring(root, encoding="unicode"))
            return _APPLIED_CONFIG

        video = existing[0]
        for extra in existing[1:]:
            devices.remove(extra)
        el = video.find("model")
        if el is None:
            el = ET.SubElement(video, "model")
        if el.get("type") != model:
            kept = {k: v for k, v in el.attrib.items() if k in _VIDEO_KEEP}
            el.attrib.clear()
            el.attrib.update(kept)
        el.set("type", model)
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)

def svc_add_sound(uuid: str, model: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        return _apply_device(dom, f"<sound model='{x(model)}'/>", "attach")

    return _with_conn(go)

def svc_remove_sound(uuid: str, model: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        el, live = _find_device(
            dom, "devices/sound", lambda s: s.get("model") == model
        )
        if el is None:
            raise RuntimeError(f"No {model} sound device")
        return _detach_element(dom, el, live)

    return _with_conn(go)

def svc_set_disk_cache(uuid: str, target_dev: str, cache: str) -> str:
    """Set the cache mode on a disk (config; applies on next start)."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        for d in root.findall("devices/disk"):
            t = d.find("target")
            if t is not None and t.get("dev") == target_dev:
                driver = d.find("driver")
                if driver is None:
                    driver = ET.SubElement(d, "driver", {"name": "qemu"})
                if cache == "default":
                    driver.attrib.pop("cache", None)
                else:
                    driver.set("cache", cache)
                conn.defineXML(ET.tostring(root, encoding="unicode"))
                return _APPLIED_CONFIG
        raise RuntimeError(f"No disk with target '{target_dev}'")

    return _with_conn(go)

def open_external(uuid: str, tool: str) -> None:
    """Launch virt-viewer / virt-manager detached for a domain."""
    if tool == "viewer":
        cmd = ["virt-viewer", "--connect", current_uri(), "--uuid", uuid, "--wait"]
    else:
        cmd = ["virt-manager", "--connect", current_uri(), "--show-domain-console", uuid]
    subprocess.Popen(cmd, start_new_session=True)


# ---------------------------------------------------------------- more devices
#
# libvirt supports a good deal more than the handful we grew up with. These
# follow the same live-then-config pattern as the rest, and each carries the
# one-line reason you would want it.

WATCHDOG_ACTIONS = ("reset", "poweroff", "shutdown", "pause", "none", "dump")
PANIC_MODELS = ("isa", "pvpanic", "hyperv", "s390")
AUDIO_BACKENDS = ("spice", "pipewire", "pulseaudio", "alsa", "none")

# What each controller type can be, for the faceplate to offer. A model
# outside its list is kept rather than replaced: QEMU knows more of them
# than this, and a machine that already works should not be quietly
# rewritten just because its controller is not listed here.
CONTROLLER_MODELS = {
    "usb": ["qemu-xhci", "nec-xhci", "ich9-ehci1", "piix3-uhci", "none"],
    "scsi": ["virtio-scsi", "lsilogic", "megasas"],
    "pci": ["pcie-root-port", "pcie-to-pci-bridge", "pci-bridge"],
}


def _has_spice(root: ET.Element) -> bool:
    return any(g.get("type") == "spice" for g in root.findall("devices/graphics"))


NEEDS_SPICE = (
    "{what} is carried over a SPICE channel, so the machine needs a SPICE "
    "display first - add one from Install hardware → Display."
)


def _define_with_device(conn, dom, xml: str) -> str:
    """Add a device libvirt refuses to attach through the device API.

    A few device types (graphics, panic, and others depending on version) can
    only be added by rewriting the persistent definition.
    """
    root = _editable_xml(dom)
    devices = root.find("devices")
    if devices is None:
        raise RuntimeError("Domain has no <devices> element")
    devices.append(ET.fromstring(xml))
    conn.defineXML(ET.tostring(root, encoding="unicode"))
    return _APPLIED_CONFIG


def svc_add_watchdog(uuid: str, model: str = "itco", action: str = "reset") -> str:
    """Reset (or stop) a guest whose kernel stops petting the watchdog.

    Some machine types ship one already, q35 has an itco watchdog, so an
    existing device is retargeted rather than treated as an error.
    """

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        existing = root.find("devices/watchdog")
        if existing is not None:
            existing.set("action", action)
            conn.defineXML(ET.tostring(root, encoding="unicode"))
            return (
                f"This machine already had a {existing.get('model', '?')} watchdog; "
                f"its action is now '{action}'."
            )
        return _apply_device(
            dom, f"<watchdog model='{x(model)}' action='{x(action)}'/>", "attach"
        )

    return _with_conn(go)


def svc_set_watchdog_action(uuid: str, action: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        wd = root.find("devices/watchdog")
        if wd is None:
            raise RuntimeError("This machine has no watchdog")
        wd.set("action", action)
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_add_usb_redirection(uuid: str) -> str:
    """A SPICE channel that forwards a host USB device into the guest.

    Unlike PCI/USB passthrough this needs no host device chosen up front;
    the viewer picks one at runtime, so it stays usable on any host.
    """

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        if not _has_spice(root):
            raise RuntimeError(NEEDS_SPICE.format(what="USB redirection"))
        return _apply_device(dom, "<redirdev bus='usb' type='spicevmc'/>", "attach")

    return _with_conn(go)


# What a TPM can be. tpm-crb is what Windows 11 expects on q35; tpm-tis is
# the older interface, and the one an i440fx machine has to use.
TPM_MODELS = ("tpm-crb", "tpm-tis")
TPM_VERSIONS = ("2.0", "1.2")

# Where a virtio-rng gets its entropy. /dev/urandom never blocks and is what
# you want; /dev/random can stall the guest on a host short of entropy.
RNG_SOURCES = ("/dev/urandom", "/dev/random", "/dev/hwrng")


def tpm_backends(conn, arch: str = "x86_64", machine: str | None = None) -> tuple[str, ...]:
    """Which TPM backends this host can actually provide.

    An emulated TPM is swtpm, a separate package libvirt starts one of per
    machine. Without it installed libvirt advertises only 'passthrough' -
    a real TPM chip handed to one guest - and refuses an emulated one with
    "TPM version '2.0' is not supported", which sends people looking at the
    version rather than at the missing package.
    """
    try:
        caps = ET.fromstring(conn.getDomainCapabilities(None, arch, machine, None))
    except libvirt.libvirtError:
        return ()
    tpm = caps.find("devices/tpm")
    if tpm is None or tpm.get("supported") == "no":
        return ()
    return tuple(
        v.text or "" for enum in tpm.findall("enum")
        if enum.get("name") == "backendModel" for v in enum.findall("value")
    )


def svc_tpm_available() -> bool:
    """Whether this host can give a machine an emulated TPM."""
    return _with_conn(lambda conn: "emulator" in tpm_backends(conn))


def svc_add_tpm(uuid: str, model: str = "tpm-crb", version: str = "2.0") -> str:
    """An emulated TPM, which is what Windows 11 refuses to install without.

    Needs swtpm on the host - libvirt starts one per machine and keeps its
    state alongside the definition. Cannot be hot-plugged: firmware looks
    for it at boot, so this lands on the next start.
    """
    if model not in TPM_MODELS:
        raise ValueError(f"A TPM is {' or '.join(TPM_MODELS)}")
    if version not in TPM_VERSIONS:
        raise ValueError(f"A TPM is version {' or '.join(TPM_VERSIONS)}")

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        if root.find("devices/tpm") is not None:
            raise RuntimeError(
                "This machine already has a TPM. A machine may only have one."
            )
        if "emulator" not in tpm_backends(conn):
            raise RuntimeError(
                "This host cannot emulate a TPM: libvirt is not offering an "
                "emulated backend, which means swtpm is not installed. "
                "Install the swtpm package and restart libvirtd, then try "
                "again - nothing about this machine needs changing."
            )
        # Not through the device API at all: firmware looks for a TPM at
        # boot, so there is no such thing as hot-plugging one.
        return _define_with_device(
            conn, dom,
            f"<tpm model='{x(model)}'>"
            f"<backend type='emulator' version='{x(version)}'/></tpm>",
        )

    return _with_conn(go)


def svc_set_tpm(uuid: str, model: str, version: str) -> str:
    """Change the TPM's interface or version in place."""
    if model not in TPM_MODELS or version not in TPM_VERSIONS:
        raise ValueError("Unknown TPM model or version")

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        tpm = root.find("devices/tpm")
        if tpm is None:
            raise RuntimeError("This machine has no TPM")
        tpm.set("model", model)
        backend = tpm.find("backend")
        if backend is None:
            backend = ET.SubElement(tpm, "backend")
        backend.set("type", "emulator")
        backend.set("version", version)
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_add_rng(uuid: str, source: str = "/dev/urandom") -> str:
    """virtio-rng: the host's entropy, handed to the guest.

    A freshly installed Linux guest with nothing else running can sit for
    a minute waiting for its random pool at first boot; this is the fix,
    and it costs nothing.
    """

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        if root.find("devices/rng") is not None:
            raise RuntimeError("This machine already has a random number source")
        return _apply_device(
            dom,
            "<rng model='virtio'>"
            f"<backend model='random'>{x(source)}</backend></rng>",
            "attach",
        )

    return _with_conn(go)


def svc_set_rng_source(uuid: str, source: str) -> str:
    """Point an existing virtio-rng at a different entropy source."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        rng = root.find("devices/rng")
        if rng is None:
            raise RuntimeError("This machine has no random number source")
        backend = rng.find("backend")
        if backend is None:
            backend = ET.SubElement(rng, "backend")
        backend.set("model", "random")
        backend.text = source
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_add_vsock(uuid: str, cid: int = 0) -> str:
    """Host/guest socket channel; cid 0 means let libvirt allocate one."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        address = (
            "<cid auto='yes'/>" if not cid else f"<cid auto='no' address='{x(cid)}'/>"
        )
        return _apply_device(dom, f"<vsock model='virtio'>{address}</vsock>", "attach")

    return _with_conn(go)


def svc_add_panic(uuid: str, model: str = "pvpanic") -> str:
    """Lets the guest tell the host it panicked, so on_crash can act."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        # libvirt rejects attachDevice for <panic>; rewrite the definition
        return _define_with_device(conn, dom, f"<panic model='{x(model)}'/>")

    return _with_conn(go)


def svc_add_smartcard(uuid: str, mode: str = "passthrough") -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        if mode == "passthrough":
            if not _has_spice(root):
                raise RuntimeError(NEEDS_SPICE.format(what="Smartcard passthrough"))
            xml = "<smartcard mode='passthrough' type='spicevmc'/>"
        else:
            xml = "<smartcard mode='host'/>"
        try:
            return _apply_device(dom, xml, "attach")
        except libvirt.libvirtError as e:
            if "smartcard" in str(e):
                raise RuntimeError(
                    "This QEMU build has no smartcard support "
                    f"({e.get_error_message()})"
                ) from e
            raise

    return _with_conn(go)


def svc_add_audio(uuid: str, backend: str = "spice") -> str:
    """Where the emulated sound card's audio actually goes on the host."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        devices = root.find("devices")
        if devices is None:
            raise RuntimeError("Domain has no <devices> element")
        if backend == "spice" and not _has_spice(root):
            raise RuntimeError(NEEDS_SPICE.format(what="SPICE audio"))
        for old in devices.findall("audio"):
            devices.remove(old)
        if backend != "none":
            ET.SubElement(devices, "audio", {"id": "1", "type": backend})
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_add_memory_device(uuid: str, size_mb: int, slots: int = 16) -> str:
    """Hot-pluggable DIMM. Needs <maxMemory> with free slots, so add it too."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        max_el = root.find("maxMemory")
        current_kb = int(root.findtext("memory") or 0)
        if max_el is None:
            # headroom for the DIMMs, and NUMA is required for memory hotplug
            max_el = ET.Element("maxMemory")
            max_el.set("slots", str(slots))
            max_el.set("unit", "KiB")
            max_el.text = str(current_kb + size_mb * 1024 * slots)
            root.insert(list(root).index(root.find("memory")), max_el)
        cpu = root.find("cpu")
        if cpu is None:
            cpu = ET.SubElement(root, "cpu")
        if cpu.find("numa") is None:
            numa = ET.SubElement(cpu, "numa")
            ET.SubElement(
                numa, "cell",
                {"id": "0", "cpus": f"0-{max(0, int(root.findtext('vcpu') or 1) - 1)}",
                 "memory": str(current_kb), "unit": "KiB"},
            )
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        dom = conn.lookupByUUIDString(uuid)
        xml = (
            "<memory model='dimm'><target>"
            f"<size unit='MiB'>{x(size_mb)}</size><node>0</node>"
            "</target></memory>"
        )
        return _apply_device(dom, xml, "attach")

    return _with_conn(go)


def display_ident(g: ET.Element) -> str:
    """How a <graphics> element is named in the hardware bay.

    Its port, or the address it listens on when the port is left to
    libvirt, or "auto". Both the reader and the remover go through this,
    so the name a row shows is the name that takes it off - they cannot
    drift into disagreeing about which display is which.
    """
    return g.get("port") or g.get("listen") or "auto"

def svc_set_disk_options(uuid: str, dev: str, readonly: bool | None = None,
                         shareable: bool | None = None,
                         serial: str | None = None,
                         discard: str | None = None) -> str:
    """The disk properties that are flags on the element rather than values.

    - readonly: the guest may not write to it at all.
    - shareable: two machines may hold it at once. Nothing coordinates
      their writes, so it is for a cluster filesystem or a disk both sides
      only read - anything else corrupts it.
    - serial: what the guest reads as the drive's serial number, which is
      how udev's /dev/disk/by-id names it.
    - discard='unmap': TRIM inside the guest frees the space in the host
      image too, which is what stops a thin image only ever growing.
    """
    if discard not in (None, "", "unmap", "ignore"):
        raise ValueError("discard is 'unmap', 'ignore', or unset")

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        for d in root.findall("devices/disk"):
            t = d.find("target")
            if t is None or t.get("dev") != dev:
                continue
            if readonly is not None:
                _flag_element(d, "readonly", readonly)
            if shareable is not None:
                _flag_element(d, "shareable", shareable)
            if serial is not None:
                existing = d.find("serial")
                if existing is not None:
                    d.remove(existing)
                if serial.strip():
                    ET.SubElement(d, "serial").text = serial.strip()
            if discard is not None:
                driver = d.find("driver")
                if driver is None:
                    driver = ET.SubElement(d, "driver", {"name": "qemu"})
                if discard:
                    driver.set("discard", discard)
                elif "discard" in driver.attrib:
                    del driver.attrib["discard"]
            conn.defineXML(ET.tostring(root, encoding="unicode"))
            return _APPLIED_CONFIG
        raise RuntimeError(f"No disk '{dev}' on this machine")

    return _with_conn(go)


def _flag_element(parent: ET.Element, tag: str, on: bool) -> None:
    """A child element whose presence is the setting, like <readonly/>."""
    existing = parent.find(tag)
    if on and existing is None:
        ET.SubElement(parent, tag)
    elif not on and existing is not None:
        parent.remove(existing)


def svc_set_graphics(uuid: str, gtype: str, ident: str, listen_type=None,
                     address=None, port=None, autoport=None, password=None,
                     gl=None) -> str:
    """Edit one <graphics> device.

    Graphics cannot hot-plug, so all of this lands on the next start. A
    port only means anything with autoport off; asking for both is the
    usual way to end up wondering why the port never changes, so setting
    an explicit port turns autoport off here rather than silently losing.
    """
    if listen_type not in (None, "address", "socket", "none"):
        raise ValueError("listen type is 'address', 'socket' or 'none'")

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        same_type = [
            g for g in root.findall("devices/graphics") if g.get("type") == gtype
        ]
        if not same_type:
            raise RuntimeError(f"This machine has no {gtype} display")
        exact = [g for g in same_type if display_ident(g) == str(ident)]
        g = exact[0] if exact else (same_type[0] if len(same_type) == 1 else None)
        if g is None:
            raise RuntimeError(
                f"More than one {gtype} display and none is '{ident}' - "
                "refresh the hardware list and try again"
            )
        if port is not None:
            if int(port) > 0:
                g.set("port", str(int(port)))
                g.set("autoport", "no")  # or the port is ignored
            else:
                g.attrib.pop("port", None)
        if autoport is not None:
            g.set("autoport", "yes" if autoport else "no")
            if autoport:
                g.attrib.pop("port", None)
        if password is not None:
            if password:
                g.set("passwd", password)
            else:
                g.attrib.pop("passwd", None)
        if listen_type is not None or address is not None:
            for old in g.findall("listen"):
                g.remove(old)
            g.attrib.pop("listen", None)
            g.attrib.pop("socket", None)
            kind = listen_type or "address"
            if kind == "address":
                where = address if address is not None else "127.0.0.1"
                ET.SubElement(g, "listen", {"type": "address", "address": where})
                g.set("listen", where)
            elif kind == "socket":
                ET.SubElement(g, "listen", {"type": "socket"})
            else:
                ET.SubElement(g, "listen", {"type": "none"})
        if gl is not None:
            for old in g.findall("gl"):
                g.remove(old)
            if gl:
                ET.SubElement(g, "gl", {"enable": "yes"})
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_set_display_type(uuid: str, old: str, new: str) -> str:
    """Turn a SPICE display into a VNC one, or the other way round.

    This is not a rename: the two protocols do not carry the same
    attributes, so the ones that only mean something to the type being
    left behind are dropped rather than kept as dead weight. What is
    common to both - where it listens, its port, its password - stays.
    """
    if new not in ("spice", "vnc"):
        raise ValueError("A display is 'spice' or 'vnc'")
    if old == new:
        # Saving a faceplate sends every field that moved, and "the type it
        # already is" has to mean nothing rather than collide with the
        # duplicate check below.
        return _APPLIED_CONFIG

    # Attributes and children that belong to one protocol only.
    only = {
        "spice": (("defaultMode", "compression", "streaming"),
                  ("image", "jpeg", "zlib", "playback", "streaming",
                   "clipboard", "mouse", "filetransfer")),
        "vnc": (("sharePolicy", "powerControl", "websocket"), ()),
    }

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(
            libvirt.VIR_DOMAIN_XML_INACTIVE | libvirt.VIR_DOMAIN_XML_SECURE
        ))
        same = [g for g in root.findall("devices/graphics")
                if g.get("type") == old]
        if not same:
            raise RuntimeError(f"This machine has no {old} display")
        if any(g.get("type") == new for g in root.findall("devices/graphics")):
            raise RuntimeError(
                f"This machine already has a {new} display. Remove that one "
                "first - two displays of the same type fight over the port."
            )
        g = same[0]
        attrs, children = only.get(old, ((), ()))
        for name in attrs:
            g.attrib.pop(name, None)
        for tag in children:
            for el in g.findall(tag):
                g.remove(el)
        if new == "vnc":
            # OpenGL is a SPICE-only path to the local host's GPU.
            for el in g.findall("gl"):
                g.remove(el)
        g.set("type", new)
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_set_shared_memory(uuid: str, on: bool) -> str:
    """<memoryBacking><access mode='shared'/>.

    What virtiofs and Looking Glass both need: the guest's memory has to
    be mappable by another process on the host. Off by default because it
    stops the memory being backed by anything private.
    """

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        backing = root.find("memoryBacking")
        if on:
            if backing is None:
                backing = ET.SubElement(root, "memoryBacking")
            access = backing.find("access")
            if access is None:
                access = ET.SubElement(backing, "access")
            access.set("mode", "shared")
        elif backing is not None:
            for access in backing.findall("access"):
                backing.remove(access)
            if len(backing) == 0:
                root.remove(backing)
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_grow_disk(uuid: str, dev: str, new_gb: float) -> str:
    """Make one of a machine's disks bigger.

    Growing only. Shrinking a disk from here is not offered: qcow2 will not
    do it, and on a raw image it silently throws away whatever was past the
    new end - by the time the guest notices, the filesystem is already
    short. The Storage page still resizes a volume directly for anyone who
    means it.

    The volume is grown, and a running machine is told about it through
    blockResize so it sees the new size without a restart. What the guest
    does with the space is its own business - the partition and the
    filesystem on it still have to be extended inside.
    """

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(
            dom.XMLDesc(0 if dom.isActive() else libvirt.VIR_DOMAIN_XML_INACTIVE)
        )
        disk = next(
            (
                d for d in root.findall("devices/disk")
                if (t := d.find("target")) is not None and t.get("dev") == dev
            ),
            None,
        )
        if disk is None:
            raise RuntimeError(f"No disk '{dev}' on this machine")
        src = disk.find("source")
        if src is None or not src.get("file"):
            raise RuntimeError(
                f"'{dev}' is not a file-backed disk, so there is no volume "
                "to grow"
            )
        path = src.get("file")
        try:
            vol = conn.storageVolLookupByPath(path)
        except libvirt.libvirtError:
            raise RuntimeError(
                f"{path} is not in a storage pool this connection knows "
                "about, so its size cannot be changed from here"
            ) from None
        _t, capacity, _alloc = vol.info()
        new_bytes = int(new_gb * 1024**3)
        if new_bytes <= capacity:
            raise RuntimeError(
                f"'{dev}' is already {capacity / 1024**3:.1f} GB. This only "
                "grows a disk - shrinking one loses whatever was past the "
                "new end."
            )
        vol.resize(new_bytes, 0)
        if dom.isActive():
            try:
                # bytes, explicitly: the default unit here is KiB, and a
                # size passed in the wrong one is a disk 1024 times the
                # wrong size rather than an error
                dom.blockResize(
                    dev, new_bytes, libvirt.VIR_DOMAIN_BLOCK_RESIZE_BYTES
                )
                return (
                    f"{dev} grown to {new_gb:.1f} GB and the running machine "
                    "told about it - the guest still has to extend the "
                    "partition and filesystem on it."
                )
            except libvirt.libvirtError:
                pass
        return (
            f"{dev} grown to {new_gb:.1f} GB. The guest sees it after a "
            "restart, and still has to extend the partition and filesystem."
        )

    return _with_conn(go)


def svc_remove_display(uuid: str, gtype: str, ident: str) -> str:
    """Remove one <graphics> element, named rather than guessed at.

    A machine can have both a VNC and a SPICE display, and this app uses
    the VNC one - so taking the right one off is how you move the console
    onto SPICE. Removing "the first display" would be a coin toss, which
    is why this takes the type and the identity the bay is showing.

    Graphics cannot hot-plug, so this always lands on the next start; a
    machine left with no display at all is a legitimate passthrough setup,
    not a mistake, so it is allowed.
    """

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        devices = root.find("devices")
        if devices is None:
            raise RuntimeError("This machine has no devices at all")
        same_type = [g for g in devices.findall("graphics")
                     if g.get("type") == gtype]
        if not same_type:
            raise RuntimeError(
                f"This machine has no {gtype} display in its definition"
            )
        # libvirt rewrites these attributes as it defines a machine - it drops
        # port='-1' from an autoport display, for one - so the identity a row
        # was drawn with does not always survive to the click. Type alone
        # settles the case that matters, VNC against SPICE; the identity is
        # only needed to tell two displays of the same type apart.
        exact = [g for g in same_type if display_ident(g) == str(ident)]
        if exact:
            target = exact[0]
        elif len(same_type) == 1:
            target = same_type[0]
        else:
            found = ", ".join(f"'{display_ident(g)}'" for g in same_type)
            raise RuntimeError(
                f"This machine has {len(same_type)} {gtype} displays ({found}) "
                f"and none of them is '{ident}' - refresh the hardware list "
                "and try again"
            )
        devices.remove(target)
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)

def svc_remove_video(uuid: str) -> str:
    """Remove the emulated video adapter.

    Worth having for a passthrough machine, which wants the card it was
    given and nothing else. libvirt adds a video device back to any
    machine that still has a display, so take the displays off first or
    this will look like it did nothing.
    """

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        devices = root.find("devices")
        video = devices.find("video") if devices is not None else None
        if devices is None or video is None:
            raise RuntimeError("This machine has no video adapter to remove")
        devices.remove(video)
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        if root.find("devices/graphics") is not None:
            return (
                "Removed - but this machine still has a display, and libvirt "
                "gives a machine with a display a video adapter back. Remove "
                "the displays too if you meant to leave it with none."
            )
        return _APPLIED_CONFIG

    return _with_conn(go)

def svc_remove_simple_device(uuid: str, tag: str) -> str:
    """Detach the first <tag> device, for the single-instance kinds."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        el, live = _find_device(dom, f"devices/{tag}", lambda _e: True)
        if el is None:
            raise RuntimeError(f"No {tag} device to remove")
        return _detach_element(dom, el, live)

    return _with_conn(go)


# ---------------------------------------------------------------- config fields


def svc_set_nic(
    uuid: str, mac: str, new_mac: str | None = None,
    model: str | None = None, link_up: bool | None = None,
) -> str:
    """Edit an interface in place: MAC, model, or link state."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        live = dom.isActive()
        root = ET.fromstring(
            dom.XMLDesc(0 if live else libvirt.VIR_DOMAIN_XML_INACTIVE)
        )
        for iface in root.findall("devices/interface"):
            m = iface.find("mac")
            if m is None or m.get("address", "").lower() != mac.lower():
                continue
            if new_mac:
                m.set("address", new_mac)
            if model:
                model_el = iface.find("model")
                if model_el is None:
                    model_el = ET.SubElement(iface, "model")
                model_el.set("type", model)
            if link_up is not None:
                link = iface.find("link")
                if link is None:
                    link = ET.SubElement(iface, "link")
                link.set("state", "up" if link_up else "down")
            # link state alone can go live; MAC and model cannot
            only_link = link_up is not None and not new_mac and not model
            xml = ET.tostring(iface, encoding="unicode")
            flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
            if only_link and live:
                try:
                    dom.updateDeviceFlags(xml, flags | libvirt.VIR_DOMAIN_AFFECT_LIVE)
                    return _APPLIED_LIVE
                except libvirt.libvirtError:
                    pass
            dom.updateDeviceFlags(xml, flags)
            return _APPLIED_CONFIG
        raise RuntimeError(f"No interface with MAC {mac}")

    return _with_conn(go)


def svc_set_video_accel(uuid: str, accel3d: bool) -> str:
    """OpenGL passthrough for the virtio GPU (needs a local SPICE/EGL viewer)."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        model = root.find("devices/video/model")
        if model is None:
            raise RuntimeError("This machine has no video device")
        accel = model.find("acceleration")
        if accel is None:
            if not accel3d:
                return _APPLIED_CONFIG  # no element already means no 3D
            accel = ET.SubElement(model, "acceleration")
        accel.set("accel3d", "yes" if accel3d else "no")
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_set_machine_type(uuid: str, machine: str) -> str:
    """Chipset / machine type, e.g. q35 vs i440fx or a pinned version."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        os_type = root.find("os/type")
        if os_type is None:
            raise RuntimeError("Domain has no <os><type>")
        os_type.set("machine", machine)
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_machine_types(arch: str = "x86_64") -> list[str]:
    """Machine types this hypervisor will accept, newest first."""

    def go(conn):
        try:
            caps = ET.fromstring(conn.getCapabilities())
        except libvirt.libvirtError:
            return []
        names: list[str] = []
        for guest in caps.findall("guest"):
            if (guest.findtext("arch/../arch") or guest.find("arch").get("name")) != arch:
                continue
            for machine in guest.findall("arch/machine"):
                text = (machine.text or "").strip()
                if text and text not in names:
                    names.append(text)
        # canonical names first (q35, i440fx), then the pinned versions
        names.sort(key=lambda n: (any(c.isdigit() for c in n), n))
        return names

    return _with_conn(go)


def svc_set_boot_menu(uuid: str, enabled: bool, timeout_ms: int = 3000) -> str:
    """The firmware's "press a key for the boot menu" prompt."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        os_el = root.find("os")
        if os_el is None:
            raise RuntimeError("Domain has no <os> element")
        menu = os_el.find("bootmenu")
        was_on = menu is not None and menu.get("enable") == "yes"
        if menu is None:
            menu = ET.SubElement(os_el, "bootmenu")
        menu.set("enable", "yes" if enabled else "no")
        if not enabled:
            menu.attrib.pop("timeout", None)
        elif not was_on and not menu.get("timeout"):
            # Only as part of actually turning it on. A machine that already
            # has the menu is using whatever wait it was given - the
            # firmware's own, if it has no timeout - and that is not this
            # call's to overwrite.
            menu.set("timeout", str(timeout_ms))
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_set_hostdev_options(
    uuid: str, kind: str, ident: str,
    rombar: bool | None = None, startup_policy: str | None = None,
    rom_file: str | None = None,
) -> str:
    """ROM BAR visibility, video BIOS file (PCI) and missing-device policy (USB).

    `rom_file=""` clears the file and leaves the card's own ROM in play.
    """

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        for h in root.findall("devices/hostdev"):
            info = _hostdev_ident(h)
            if info is None or info.kind != kind or info.ident != ident:
                continue
            if rombar is not None:
                rom = h.find("rom")
                if rom is None:
                    rom = ET.SubElement(h, "rom")
                rom.set("bar", "on" if rombar else "off")
            if rom_file is not None:
                rom = h.find("rom")
                if rom is None:
                    rom = ET.SubElement(h, "rom")
                if rom_file:
                    rom.set("file", rom_file)
                elif "file" in rom.attrib:
                    del rom.attrib["file"]
            if startup_policy:
                source = h.find("source")
                if source is not None:
                    source.set("startupPolicy", startup_policy)
            conn.defineXML(ET.tostring(root, encoding="unicode"))
            return _APPLIED_CONFIG
        raise RuntimeError(f"No attached {kind} device {ident}")

    return _with_conn(go)


def svc_set_controller_model(uuid: str, ctype: str, index: int, model: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        for c in root.findall("devices/controller"):
            if c.get("type") == ctype and int(c.get("index", -1)) == index:
                c.set("model", model)
                conn.defineXML(ET.tostring(root, encoding="unicode"))
                return _APPLIED_CONFIG
        raise RuntimeError(f"No {ctype} controller with index {index}")

    return _with_conn(go)
