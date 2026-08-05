"""Snapshots and checkpoint-based incremental backups."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import libvirt

from .connection import _with_conn
from .models import CheckpointInfo, SnapshotInfo
from .xmlesc import x
from .xmlutil import _backup_disks

def svc_list_snapshots(uuid: str) -> list[SnapshotInfo]:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        result = []
        for snap in dom.listAllSnapshots():
            root = ET.fromstring(snap.getXMLDesc(0))
            desc = root.findtext("description") or ""
            created = int(root.findtext("creationTime") or 0)
            state = root.findtext("state") or "?"
            parent = root.findtext("parent/name")
            external = any(
                d.get("snapshot") == "external"
                for d in root.findall("disks/disk")
            ) or root.findtext("memory") == "external" or (
                (m := root.find("memory")) is not None
                and m.get("snapshot") == "external"
            )
            result.append(
                SnapshotInfo(
                    name=snap.getName(),
                    description=desc,
                    created=created,
                    state=state,
                    parent=parent,
                    current=bool(snap.isCurrent()),
                    external=external,
                )
            )
        result.sort(key=lambda s: s.created)
        return result

    return _with_conn(go)

def svc_create_snapshot(
    uuid: str, name: str, description: str, external: bool = False
) -> None:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        desc_xml = f"<description>{x(description)}</description>" if description else ""
        body = ""
        flags = 0
        if external:
            root = ET.fromstring(dom.XMLDesc(0))
            disks = "".join(
                f"<disk name='{x(t.get('dev'))}' snapshot='external'/>"
                for d in root.findall("devices/disk")
                if d.get("device") == "disk" and (t := d.find("target")) is not None
            )
            if dom.isActive():
                # external memory image next to the first disk
                first = root.find("devices/disk/source")
                base = (first.get("file") if first is not None else "") or "/var/lib/libvirt/images/x"
                mem_path = f"{base.rsplit('/', 1)[0]}/{dom.name()}.{name}.memsnap"
                body = f"<memory snapshot='external' file='{x(mem_path)}'/><disks>{disks}</disks>"
            else:
                body = f"<disks>{disks}</disks>"
                flags = libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY
        dom.snapshotCreateXML(
            f"<domainsnapshot><name>{x(name)}</name>{desc_xml}{body}</domainsnapshot>",
            flags,
        )

    _with_conn(go)

def svc_revert_snapshot(uuid: str, name: str) -> None:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        dom.revertToSnapshot(dom.snapshotLookupByName(name), 0)

    _with_conn(go)

def svc_delete_snapshot(uuid: str, name: str) -> None:
    def go(conn):
        conn.lookupByUUIDString(uuid).snapshotLookupByName(name).delete(0)

    _with_conn(go)

def svc_list_checkpoints(uuid: str) -> list[CheckpointInfo]:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        out = []
        try:
            checkpoints = dom.listAllCheckpoints()
        except libvirt.libvirtError:
            return out
        for chk in checkpoints:
            root = ET.fromstring(chk.getXMLDesc())
            out.append(
                CheckpointInfo(
                    name=chk.getName(),
                    created=int(root.findtext("creationTime") or 0),
                    parent=root.findtext("parent/name"),
                    disks=tuple(
                        d.get("name")
                        for d in root.findall("disks/disk")
                        if d.get("checkpoint") != "no"
                    ),
                )
            )
        out.sort(key=lambda c: c.created)
        return out

    return _with_conn(go)

def svc_delete_checkpoint(uuid: str, name: str) -> None:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        dom.checkpointLookupByName(name).delete()

    _with_conn(go)

def svc_backup(uuid: str, dest_dir: str, incremental: bool) -> str:
    """Pull-free (push) backup of every file-backed disk into dest_dir.

    Always writes a fresh checkpoint so the *next* run can be incremental.
    With incremental=True the newest existing checkpoint becomes the parent
    and only changed blocks are written.
    """
    import json
    import os
    import time as _time

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        if not dom.isActive():
            raise RuntimeError(
                "Backups run against a running machine, use Export for a "
                "machine that is shut off"
            )
        root = ET.fromstring(dom.XMLDesc(0))
        disks = _backup_disks(root)
        if not disks:
            raise RuntimeError("No file-backed disks to back up")

        existing = svc_list_checkpoints(uuid)
        parent = existing[-1].name if existing else None
        if incremental and parent is None:
            raise RuntimeError(
                "No previous checkpoint, run a full backup first"
            )

        stamp = _time.strftime("%Y%m%d-%H%M%S")
        kind = "incr" if incremental else "full"
        folder = os.path.join(dest_dir, f"{dom.name()}-{kind}-{stamp}")
        os.makedirs(folder)
        chk_name = f"chk-{stamp}"

        disk_xml = "".join(
            f"<disk name='{x(dev)}' backup='yes' type='file'>"
            f"<target file='{x(os.path.join(folder, dev + '.qcow2'))}'/>"
            f"<driver type='qcow2'/></disk>"
            for dev in disks
        )
        incremental_xml = (
            f"<incremental>{x(parent)}</incremental>" if incremental else ""
        )
        backup_xml = (
            f"<domainbackup mode='push'>{incremental_xml}"
            f"<disks>{disk_xml}</disks></domainbackup>"
        )
        chk_disks = "".join(f"<disk name='{x(dev)}' checkpoint='bitmap'/>" for dev in disks)
        chk_xml = (
            f"<domaincheckpoint><name>{x(chk_name)}</name>"
            f"<disks>{chk_disks}</disks></domaincheckpoint>"
        )

        dom.backupBegin(backup_xml, chk_xml)
        # the job runs asynchronously; wait for it to finish
        deadline = _time.monotonic() + 3600
        while _time.monotonic() < deadline:
            stats = dom.jobStats()
            if stats.get("type", libvirt.VIR_DOMAIN_JOB_NONE) == libvirt.VIR_DOMAIN_JOB_NONE:
                break
            _time.sleep(0.5)
        else:
            dom.abortJob()
            raise RuntimeError("Backup timed out after an hour")

        written = {
            f: os.path.getsize(os.path.join(folder, f))
            for f in sorted(os.listdir(folder))
        }
        with open(os.path.join(folder, "domain.xml"), "w") as f:
            f.write(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        with open(os.path.join(folder, "manifest.json"), "w") as f:
            json.dump(
                {
                    "name": dom.name(), "kind": kind, "checkpoint": chk_name,
                    "parent": parent, "disks": list(disks),
                },
                f, indent=2,
            )
        total = sum(written.values())
        return f"{kind} backup → {folder} ({total / 1024**2:.0f} MB)"

    return _with_conn(go)
