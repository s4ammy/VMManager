"""Domain lifecycle, identity, metadata, templates and stacks."""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET

import libvirt

from .connection import VMM_NS, _with_conn, current_uri
from .devices import _APPLIED_CONFIG
from .models import DomainDisk
from .xmlesc import x
from .networks import svc_create_network

def svc_domain_action(uuid: str, op: str) -> None:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        ops = {
            "start": dom.create,
            "shutdown": dom.shutdown,
            "reboot": lambda: dom.reboot(0),
            "force-off": dom.destroy,
            "pause": dom.suspend,
            "resume": dom.resume,
            "autostart-on": lambda: dom.setAutostart(1),
            "autostart-off": lambda: dom.setAutostart(0),
            "managedsave": lambda: dom.managedSave(0),
            "discard-saved": lambda: dom.managedSaveRemove(0),
        }
        ops[op]()

    _with_conn(go)

def svc_get_xml(uuid: str) -> str:
    return _with_conn(
        lambda c: c.lookupByUUIDString(uuid).XMLDesc(
            libvirt.VIR_DOMAIN_XML_INACTIVE | libvirt.VIR_DOMAIN_XML_SECURE
        )
    )

def svc_define_xml(xml: str) -> None:
    _with_conn(lambda c: c.defineXML(xml))

def svc_clone(uuid: str, new_name: str) -> None:
    if not shutil.which("virt-clone"):
        raise RuntimeError("virt-clone is not installed")

    def go(conn):
        original = conn.lookupByUUIDString(uuid).name()
        result = subprocess.run(
            [
                "virt-clone",
                "--connect",
                current_uri(),
                "--original",
                original,
                "--name",
                new_name,
                "--auto-clone",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "virt-clone failed")

    _with_conn(go)

def svc_delete(uuid: str, delete_storage) -> None:
    """delete_storage: True (all file disks), False, or an explicit path list."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        sources: list[str] = []
        if delete_storage is True:
            root = ET.fromstring(dom.XMLDesc(0))
            for d in root.findall("devices/disk"):
                if d.get("device") != "disk":
                    continue
                s = d.find("source")
                if s is not None and s.get("file"):
                    sources.append(s.get("file"))
        elif delete_storage:
            sources = list(delete_storage)
        if dom.isActive():
            dom.destroy()
        flags = (
            libvirt.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE
            | libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
            | libvirt.VIR_DOMAIN_UNDEFINE_NVRAM
        )
        dom.undefineFlags(flags)
        for path in sources:
            try:
                vol = conn.storageVolLookupByPath(path)
                vol.delete(0)
            except libvirt.libvirtError:
                pass

    _with_conn(go)

def svc_save_to_file(uuid: str, path: str) -> None:
    _with_conn(lambda c: c.lookupByUUIDString(uuid).save(path))

def svc_restore_from_file(path: str) -> None:
    _with_conn(lambda c: c.restore(path))

def svc_migrate(uuid: str, dest_uri: str, live: bool) -> None:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        flags = (
            libvirt.VIR_MIGRATE_PEER2PEER
            | libvirt.VIR_MIGRATE_PERSIST_DEST
            | libvirt.VIR_MIGRATE_UNDEFINE_SOURCE
        )
        if live:
            flags |= libvirt.VIR_MIGRATE_LIVE
        dom.migrateToURI3(dest_uri, {}, flags)

    _with_conn(go)

def _read_vmm_meta(dom) -> tuple[bool, tuple[str, ...], str]:
    """(is_template, tags, os_icon) from our metadata element.

    Tolerant of the older bare ``<template/>`` form and of a missing element.
    """
    try:
        xml = dom.metadata(
            libvirt.VIR_DOMAIN_METADATA_ELEMENT, VMM_NS,
            libvirt.VIR_DOMAIN_AFFECT_CONFIG,
        )
    except libvirt.libvirtError:
        return False, (), ""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return False, (), ""
    if root.tag.endswith("template"):  # legacy bare <template/>
        return True, (), ""
    is_template = root.find("template") is not None
    tags = tuple(
        t.strip() for t in (root.findtext("tags") or "").split(",") if t.strip()
    )
    return is_template, tags, (root.findtext("osicon") or "").strip()

def _write_vmm_meta(
    dom, is_template: bool, tags: tuple[str, ...], os_icon: str = ""
) -> None:
    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if not is_template and not tags and not os_icon:
        dom.setMetadata(
            libvirt.VIR_DOMAIN_METADATA_ELEMENT, None, "vmm", VMM_NS, flags
        )
        return
    body = "<template/>" if is_template else ""
    if tags:
        body += f"<tags>{x(','.join(tags))}</tags>"
    if os_icon:
        body += f"<osicon>{x(os_icon)}</osicon>"
    dom.setMetadata(
        libvirt.VIR_DOMAIN_METADATA_ELEMENT, f"<meta>{body}</meta>",
        "vmm", VMM_NS, flags,
    )

def svc_set_template(uuid: str, on: bool) -> None:
    """Mark/unmark a domain as a template, preserving its tags."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        _is_t, tags, icon = _read_vmm_meta(dom)
        _write_vmm_meta(dom, on, tags, icon)

    _with_conn(go)

def svc_set_tags(uuid: str, tags: tuple[str, ...]) -> None:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        is_t, _old, icon = _read_vmm_meta(dom)
        _write_vmm_meta(dom, is_t, tags, icon)

    _with_conn(go)

def _pool_for_path(conn, path: str):
    import os

    parent = os.path.dirname(path)
    for pool in conn.listAllStoragePools():
        if not pool.isActive():
            continue
        root = ET.fromstring(pool.XMLDesc(0))
        if (root.findtext("target/path") or "") == parent:
            return pool
    raise RuntimeError(f"No active pool holds {parent}")

def svc_linked_clone(uuid: str, new_name: str, network: str | None = None) -> None:
    """Instant copy-on-write clone: overlay disks backed by the template's."""

    def go(conn):
        try:
            conn.lookupByName(new_name)
            raise RuntimeError(f"A machine named '{new_name}' already exists")
        except libvirt.libvirtError:
            pass
        dom = conn.lookupByUUIDString(uuid)
        if dom.isActive():
            raise RuntimeError("Shut the template down before cloning from it")
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))

        for d in root.findall("devices/disk"):
            if d.get("device") != "disk":
                continue
            source = d.find("source")
            driver = d.find("driver")
            if source is None or not source.get("file"):
                continue
            base_path = source.get("file")
            base_fmt = driver.get("type", "qcow2") if driver is not None else "qcow2"
            pool = _pool_for_path(conn, base_path)
            base_vol = conn.storageVolLookupByPath(base_path)
            _, capacity, _ = base_vol.info()
            target = d.find("target")
            dev = target.get("dev", "disk") if target is not None else "disk"
            overlay = pool.createXML(
                f"""<volume>
  <name>{x(new_name)}-{x(dev)}.qcow2</name>
  <capacity unit='bytes'>{capacity}</capacity>
  <target><format type='qcow2'/></target>
  <backingStore>
    <path>{x(base_path)}</path>
    <format type='{x(base_fmt)}'/>
  </backingStore>
</volume>""",
                0,
            )
            source.set("file", overlay.path())
            if driver is None:
                driver = ET.SubElement(d, "driver", {"name": "qemu"})
            driver.set("type", "qcow2")

        # fresh identity: name, uuid, MACs, nvram file
        name_el = root.find("name")
        name_el.text = new_name
        uuid_el = root.find("uuid")
        if uuid_el is not None:
            root.remove(uuid_el)
        for iface in root.findall("devices/interface"):
            mac = iface.find("mac")
            if mac is not None:
                iface.remove(mac)
            if network is not None and iface.get("type") == "network":
                src = iface.find("source")
                if src is not None:
                    src.set("network", network)
        nvram = root.find("os/nvram")
        if nvram is not None:
            nvram.text = None
        # the clone is a working machine, not a template
        meta = root.find("metadata")
        if meta is not None:
            for child in list(meta):
                if child.tag.endswith("template") or VMM_NS in child.tag:
                    meta.remove(child)
        conn.defineXML(ET.tostring(root, encoding="unicode"))

    _with_conn(go)

def svc_deploy_stack(
    name: str, template_uuid: str, count: int, network: str
) -> str:
    """N linked clones off a template; network 'new-isolated' creates one."""
    net_name = network
    if network == "new-isolated":
        net_name = f"{name}-net"
        octet = 100 + (abs(hash(name)) % 100)
        try:
            svc_create_network(
                net_name, "isolated", f"192.168.{octet}.0/24",
                f"192.168.{octet}.10", f"192.168.{octet}.254",
            )
        except libvirt.libvirtError as e:
            if "already exists" not in str(e):
                raise
    started = 0
    for i in range(1, count + 1):
        clone = f"{name}-{i}"
        svc_linked_clone(template_uuid, clone, network=net_name)
        try:
            _with_conn(lambda c, n=clone: c.lookupByName(n).create())
            started += 1
        except libvirt.libvirtError:
            pass
    return f"Deployed {count} machine(s) on {net_name}; {started} started."

def svc_teardown_stack(name: str) -> str:
    """Delete every {name}-N clone (with overlays) and the stack network."""

    def go(conn):
        removed = 0
        for dom in conn.listAllDomains():
            dom_name = dom.name()
            if not dom_name.startswith(f"{name}-"):
                continue
            suffix = dom_name[len(name) + 1 :]
            if not suffix.isdigit():
                continue
            svc_delete(dom.UUIDString(), True)
            removed += 1
        try:
            net = conn.networkLookupByName(f"{name}-net")
            if net.isActive():
                net.destroy()
            if net.isPersistent():
                net.undefine()
        except libvirt.libvirtError:
            pass
        return f"Removed {removed} machine(s)."

    return _with_conn(go)

def svc_get_on_crash(uuid: str) -> str:
    return _with_conn(
        lambda c: ET.fromstring(
            c.lookupByUUIDString(uuid).XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
        ).findtext("on_crash")
        or "destroy"
    )

def svc_set_on_crash(uuid: str, restart: bool) -> str:
    """Restart-on-crash is enforced by libvirt itself, works app or no app."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        el = root.find("on_crash")
        if el is None:
            el = ET.SubElement(root, "on_crash")
        el.text = "restart" if restart else "destroy"
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)

def svc_prune_snapshots(uuid: str, prefix: str, keep: int) -> int:
    """Delete oldest prefix-matching snapshots beyond `keep`; returns count."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        matching = []
        for snap in dom.listAllSnapshots():
            if snap.getName().startswith(prefix):
                created = int(
                    ET.fromstring(snap.getXMLDesc(0)).findtext("creationTime") or 0
                )
                matching.append((created, snap))
        matching.sort()
        deleted = 0
        for _created, snap in matching[: max(0, len(matching) - keep)]:
            snap.delete(0)
            deleted += 1
        return deleted

    return _with_conn(go)

def svc_list_domain_disks(uuid: str) -> list[DomainDisk]:
    """File-backed data disks (not cdroms) - candidates for deletion."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        out: list[DomainDisk] = []
        for d in root.findall("devices/disk"):
            if d.get("device") != "disk":
                continue
            src = d.find("source")
            target = d.find("target")
            path = src.get("file") if src is not None else None
            if not path:
                continue
            cap = 0.0
            try:
                vol = conn.storageVolLookupByPath(path)
                _, c, _ = vol.info()
                cap = c / 1024**3
            except libvirt.libvirtError:
                pass
            out.append(
                DomainDisk(
                    dev=target.get("dev", "?") if target is not None else "?",
                    path=path,
                    capacity_gb=cap,
                )
            )
        return out

    return _with_conn(go)


def svc_set_labels(uuid: str, title: str, description: str) -> str:
    """<title> is a one-line human name; <description> is free-form notes."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        dom.setMetadata(
            libvirt.VIR_DOMAIN_METADATA_TITLE, title.strip() or None, None, None, flags
        )
        dom.setMetadata(
            libvirt.VIR_DOMAIN_METADATA_DESCRIPTION,
            description.strip() or None, None, None, flags,
        )
        return "Saved."

    return _with_conn(go)


# -- cloning with per-disk choices


def svc_clone_advanced(
    uuid: str, new_name: str, disks: list[tuple[str, str, str]],
    preserve_macs: bool = False,
) -> None:
    """virt-clone with an explicit decision per disk.

    `disks` is [(target_dev, action, path)] where action is "clone" (copy to
    path), "share" (reuse the original) or "skip" (drop the disk).
    """
    if not shutil.which("virt-clone"):
        raise RuntimeError("virt-clone is not installed")

    def go(conn):
        original = conn.lookupByUUIDString(uuid)
        if original.isActive():
            raise RuntimeError("Shut the machine down before cloning")
        argv = [
            "virt-clone", "--connect", current_uri(),
            "--original", original.name(), "--name", new_name,
        ]
        for dev, action, path in disks:
            if action == "share":
                argv += ["--preserve-data", "--file", path]
            elif action == "skip":
                argv += ["--skip-copy", dev]
            else:
                argv += ["--file", path]
        if preserve_macs:
            argv.append("--preserve")
        result = subprocess.run(argv, capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "virt-clone failed")

    _with_conn(go)


# -- migration with transport options


def svc_migrate_advanced(
    uuid: str,
    dest_uri: str,
    live: bool = True,
    tunnelled: bool = False,
    unsafe: bool = False,
    temporary: bool = False,
    auto_converge: bool = True,
    dest_address: str = "",
    dest_port: int = 0,
    bandwidth_mib: int = 0,
    max_downtime_ms: int = 0,
) -> None:
    """Migrate with the knobs virt-manager exposes.

    Tunnelled sends the memory stream over the libvirt connection instead of a
    direct host-to-host one, slower, but it needs no extra open ports.
    Temporary leaves the definition behind on this host so the machine comes
    back here when it stops.
    """

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        flags = libvirt.VIR_MIGRATE_PEER2PEER
        if live:
            flags |= libvirt.VIR_MIGRATE_LIVE
        if tunnelled:
            flags |= libvirt.VIR_MIGRATE_TUNNELLED
        if unsafe:
            flags |= libvirt.VIR_MIGRATE_UNSAFE
        if auto_converge and live:
            flags |= libvirt.VIR_MIGRATE_AUTO_CONVERGE
        if not temporary:
            # normal move: define it there, forget it here
            flags |= libvirt.VIR_MIGRATE_PERSIST_DEST
            flags |= libvirt.VIR_MIGRATE_UNDEFINE_SOURCE
        # temporary migration adds neither flag: the destination copy is
        # transient and this host keeps the definition, so the machine comes
        # back here the next time it starts.

        params: dict = {}
        if dest_address:
            params[libvirt.VIR_MIGRATE_PARAM_LISTEN_ADDRESS] = dest_address
        if dest_port:
            params[libvirt.VIR_MIGRATE_PARAM_DISKS_PORT] = int(dest_port)
        if bandwidth_mib:
            params[libvirt.VIR_MIGRATE_PARAM_BANDWIDTH] = int(bandwidth_mib)
        if max_downtime_ms:
            try:
                dom.migrateSetMaxDowntime(int(max_downtime_ms))
            except libvirt.libvirtError:
                pass
        dom.migrateToURI3(dest_uri, params, flags)

    _with_conn(go)


def svc_set_os_icon(uuid: str, os_icon: str) -> None:
    """Pin the machine's OS icon, or pass "" to go back to auto-detection."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        is_template, tags, _old = _read_vmm_meta(dom)
        _write_vmm_meta(dom, is_template, tags, os_icon)

    _with_conn(go)
