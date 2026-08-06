"""Pools, volumes, reclaiming, compaction, export and import."""

from __future__ import annotations

import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

import libvirt

from .connection import COMPACT_TMP, MIN_COMPACT_SIZE, _with_conn
from .create import svc_upload_volume_conn
from .models import (BackingIndex, CompactCandidate, OrphanVolume, PoolInfo,
                     VolumeInfo)
from .xmlesc import x

def svc_list_pools() -> list[PoolInfo]:
    def go(conn):
        pools = []
        for pool in conn.listAllStoragePools():
            active = bool(pool.isActive())
            cap = alloc = avail = 0
            path = ""
            vols: list[VolumeInfo] = []
            try:
                root = ET.fromstring(pool.XMLDesc(0))
                path = root.findtext("target/path") or ""
                if active:
                    _, cap, alloc, avail = pool.info()
                    pool.refresh(0)
                    for vol in pool.listAllVolumes():
                        vroot = ET.fromstring(vol.XMLDesc(0))
                        fmt = vroot.find("target/format")
                        _, vcap, valloc = vol.info()
                        vols.append(
                            VolumeInfo(
                                name=vol.name(),
                                path=vol.path(),
                                capacity=vcap,
                                allocation=valloc,
                                format=fmt.get("type", "?") if fmt is not None else "?",
                            )
                        )
            except libvirt.libvirtError:
                pass
            vols.sort(key=lambda v: v.name.lower())
            pools.append(
                PoolInfo(
                    name=pool.name(),
                    active=active,
                    autostart=bool(pool.autostart()),
                    capacity=cap,
                    allocation=alloc,
                    available=avail,
                    path=path,
                    volumes=tuple(vols),
                )
            )
        pools.sort(key=lambda p: p.name.lower())
        return pools

    return _with_conn(go)

def svc_create_volume(pool_name: str, name: str, size_gb: float, fmt: str) -> str:
    def go(conn):
        pool = conn.storagePoolLookupByName(pool_name)
        size = int(size_gb * 1024**3)
        vol = pool.createXML(
            f"""<volume>
  <name>{x(name)}</name>
  <capacity unit='bytes'>{size}</capacity>
  <target><format type='{x(fmt)}'/></target>
</volume>""",
            0,
        )
        return vol.path()

    return _with_conn(go)

def svc_delete_volume(pool_name: str, vol_name: str) -> None:
    def go(conn):
        conn.storagePoolLookupByName(pool_name).storageVolLookupByName(vol_name).delete(0)

    _with_conn(go)

def svc_create_pool(
    name: str,
    ptype: str,
    target: str,
    host: str = "",
    export: str = "",
    source_device: str = "",
    source_name: str = "",
) -> None:
    def go(conn):
        source = ""
        if ptype == "netfs":
            source = (
                f"<source><host name='{x(host)}'/><dir path='{x(export)}'/>"
                "<format type='auto'/></source>"
            )
        elif ptype == "fs":
            source = f"<source><device path='{x(source_device)}'/></source>"
        elif ptype == "logical":
            dev = f"<device path='{x(source_device)}'/>" if source_device else ""
            source = f"<source>{dev}<name>{x(source_name)}</name></source>"
        elif ptype == "iscsi":
            source = (
                f"<source><host name='{x(host)}'/>"
                f"<device path='{x(source_device)}'/></source>"
            )
        elif ptype == "zfs":
            source = f"<source><name>{x(source_name)}</name></source>"
        target_path = target
        if ptype == "logical" and not target_path:
            target_path = f"/dev/{source_name}"
        elif ptype == "iscsi" and not target_path:
            target_path = "/dev/disk/by-path"
        target_xml = f"<target><path>{x(target_path)}</path></target>" if target_path else ""
        xml = f"""<pool type='{x(ptype)}'>
  <name>{x(name)}</name>
  {source}
  {target_xml}
</pool>"""
        pool = conn.storagePoolDefineXML(xml, 0)
        try:
            pool.build(0)
        except libvirt.libvirtError:
            pass  # already exists / not needed for this type
        try:
            pool.create(0)
            pool.setAutostart(1)
        except libvirt.libvirtError:
            pool.undefine()
            raise

    _with_conn(go)

def svc_pool_action(name: str, op: str) -> None:
    def go(conn):
        pool = conn.storagePoolLookupByName(name)
        ops = {
            "start": lambda: pool.create(0),
            "stop": lambda: pool.destroy(),
            "autostart-on": lambda: pool.setAutostart(1),
            "autostart-off": lambda: pool.setAutostart(0),
            "refresh": lambda: pool.refresh(0),
        }
        ops[op]()

    _with_conn(go)

def svc_delete_pool(name: str) -> None:
    """Forget the pool definition; volumes on disk are left alone."""

    def go(conn):
        pool = conn.storagePoolLookupByName(name)
        if pool.isActive():
            pool.destroy()
        pool.undefine()

    _with_conn(go)

def svc_resize_volume(pool_name: str, vol_name: str, new_gb: float) -> None:
    def go(conn):
        vol = conn.storagePoolLookupByName(pool_name).storageVolLookupByName(vol_name)
        _, cap, _ = vol.info()
        new_bytes = int(new_gb * 1024**3)
        flags = 0
        if new_bytes < cap:
            flags = libvirt.VIR_STORAGE_VOL_RESIZE_SHRINK
        vol.resize(new_bytes, flags)

    _with_conn(go)

def svc_upload_volume(pool_name: str, name: str, data: bytes, fmt: str = "raw") -> str:
    """Create a volume holding `data` (e.g. a seed ISO or imported image)."""
    return _with_conn(lambda c: svc_upload_volume_conn(c, pool_name, name, data, fmt))

def svc_upload_volume_from_file(
    pool_name: str, name: str, file_path: str, fmt: str = "qcow2"
) -> str:
    """Stream a local file into a new volume without loading it into memory."""
    return _with_conn(
        lambda c: svc_upload_volume_from_file_conn(c, pool_name, name, file_path, fmt)
    )

def svc_orphan_volumes() -> list[OrphanVolume]:
    """Volumes in active pools that no domain references, directly or as a
    backing file / nvram store."""

    def go(conn):
        used: set[str] = set()

        def mark_chain(path: str) -> None:
            while path and path not in used:
                used.add(path)
                try:
                    vol = conn.storageVolLookupByPath(path)
                    path = ET.fromstring(vol.XMLDesc(0)).findtext("backingStore/path") or ""
                except libvirt.libvirtError:
                    break

        for dom in conn.listAllDomains():
            root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
            for src in root.findall("devices/disk/source"):
                if src.get("file"):
                    mark_chain(src.get("file"))
            nvram = root.findtext("os/nvram")
            if nvram:
                used.add(nvram)

        orphans: list[OrphanVolume] = []
        for pool in conn.listAllStoragePools():
            if not pool.isActive():
                continue
            pool.refresh(0)
            for vol in pool.listAllVolumes():
                if vol.path() not in used:
                    _, cap, _ = vol.info()
                    orphans.append(
                        OrphanVolume(pool.name(), vol.name(), vol.path(), cap)
                    )
        orphans.sort(key=lambda o: -o.capacity)
        return orphans

    return _with_conn(go)

def svc_compact_candidates() -> list[CompactCandidate]:
    """qcow2 volumes worth rewriting, with a conservative slack estimate."""
    import json
    import shutil
    import subprocess

    def go(conn):
        in_use: dict[str, tuple[str, bool]] = {}
        for dom in conn.listAllDomains():
            try:
                root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
            except libvirt.libvirtError:
                continue
            active = bool(dom.isActive())
            for src in root.findall("devices/disk/source"):
                if src.get("file"):
                    in_use[src.get("file")] = (dom.name(), active)

        out: list[CompactCandidate] = []
        qemu_img = shutil.which("qemu-img")
        for pool in conn.listAllStoragePools():
            if not pool.isActive():
                continue
            pool.refresh(0)
            for vol in pool.listAllVolumes():
                try:
                    vroot = ET.fromstring(vol.XMLDesc(0))
                except libvirt.libvirtError:
                    continue
                fmt = vroot.find("target/format")
                if fmt is None or fmt.get("type") != "qcow2":
                    continue
                # An overlay is small because its data lives in the image
                # below it. qemu-img convert merges the two, so "compacting"
                # a linked clone inflates it to the full size of its
                # template and cuts the link - the opposite of the job.
                if vroot.findtext("backingStore/path"):
                    continue
                path = vol.path()
                _t, capacity, allocation = vol.info()
                if allocation < MIN_COMPACT_SIZE:
                    continue
                owner, running = in_use.get(path, (None, False))
                needed = allocation
                if qemu_img and not running:
                    try:
                        result = subprocess.run(
                            [qemu_img, "map", "--output=json", path],
                            capture_output=True, text=True, timeout=120,
                        )
                        if result.returncode == 0:
                            extents = json.loads(result.stdout)
                            needed = sum(
                                e.get("length", 0) for e in extents
                                if e.get("data") and not e.get("zero")
                            )
                    except (subprocess.SubprocessError, ValueError):
                        pass
                out.append(
                    CompactCandidate(
                        pool=pool.name(), name=vol.name(), path=path,
                        capacity=capacity, allocation=allocation, needed=needed,
                        in_use_by=owner, running=running,
                    )
                )
        out.sort(key=lambda c: -c.slack)
        return out

    return _with_conn(go)

def _convert_qcow2(qemu_img: str, src: str, dst: str) -> None:
    import subprocess

    result = subprocess.run(
        [qemu_img, "convert", "-O", "qcow2", src, dst],
        capture_output=True, text=True, timeout=7200,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "qemu-img convert failed")

def _download_volume(conn, vol, dest: str) -> None:
    stream = conn.newStream()
    vol.download(stream, 0, 0)
    try:
        with open(dest, "wb") as f:
            stream.recvAll(lambda _s, data, fh: fh.write(data), f)
        stream.finish()
    except (libvirt.libvirtError, OSError):
        stream.abort()
        raise

def svc_compact_volume(pool_name: str, vol_name: str) -> str:
    """Rewrite a qcow2 without the clusters its data no longer needs.

    Two routes. When the image file is ours to write (a pool under $HOME, say)
    we convert beside it and swap it in: one pass, nothing leaves the disk.
    System pools live in root-owned directories, so there we stream the volume
    out through libvirt, convert locally, and stream it back; the downloaded
    copy is kept until the replacement is verified so a failure can be undone.
    """
    import os
    import shutil

    qemu_img = shutil.which("qemu-img")
    if qemu_img is None:
        raise RuntimeError("qemu-img is not installed")

    def go(conn):
        pool = conn.storagePoolLookupByName(pool_name)
        vol = pool.storageVolLookupByName(vol_name)
        path = vol.path()
        for dom in conn.listAllDomains():
            if not dom.isActive():
                continue
            root = ET.fromstring(dom.XMLDesc(0))
            for src in root.findall("devices/disk/source"):
                if src.get("file") == path:
                    raise RuntimeError(
                        f"'{dom.name()}' is running and using this disk - "
                        "shut it down first"
                    )
        _t, capacity, before = vol.info()
        direct = os.access(path, os.R_OK | os.W_OK) and os.access(
            os.path.dirname(path), os.W_OK
        )

        if direct:
            tmp = f"{path}.compacting"
            try:
                _convert_qcow2(qemu_img, path, tmp)
                os.chmod(tmp, os.stat(path).st_mode)
                os.replace(tmp, path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
        else:
            COMPACT_TMP.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(COMPACT_TMP).free
            if free < before * 2 + 256 * 1024**2:
                raise RuntimeError(
                    f"{COMPACT_TMP} needs about {before * 2 / 1024**3:.1f} GB "
                    f"free to rewrite this image; only "
                    f"{free / 1024**3:.1f} GB available"
                )
            src_copy = str(COMPACT_TMP / f"{vol_name}.orig")
            dst_copy = str(COMPACT_TMP / f"{vol_name}.new")
            try:
                _download_volume(conn, vol, src_copy)
                _convert_qcow2(qemu_img, src_copy, dst_copy)
                new_size = os.path.getsize(dst_copy)
                if new_size >= before:
                    return f"{vol_name} is already as small as it can get"
                # replace the volume, keeping the original copy to fall back on
                vol.delete(0)
                try:
                    svc_upload_volume_from_file_conn(
                        conn, pool_name, vol_name, dst_copy, "qcow2",
                        replace=True,
                    )
                except Exception:
                    svc_upload_volume_from_file_conn(
                        conn, pool_name, vol_name, src_copy, "qcow2",
                        replace=True,
                    )
                    raise RuntimeError(
                        "couldn't write the compacted image. The original has "
                        "been restored"
                    )
            finally:
                for leftover in (src_copy, dst_copy):
                    if os.path.exists(leftover):
                        os.unlink(leftover)

        pool.refresh(0)
        fresh = pool.storageVolLookupByName(vol_name)
        after = fresh.info()[2]
        if capacity and fresh.info()[1] < capacity:
            fresh.resize(capacity)  # keep the guest's view of the disk size
        saved = max(0, before - after)
        return f"Reclaimed {saved / 1024**2:.0f} MB from {vol_name}"

    return _with_conn(go)

def svc_export_vm(uuid: str, dest_dir: str) -> str:
    """XML + every file-backed disk, streamed into a timestamped folder."""
    import json
    import os
    import time as _time

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        if dom.isActive():
            raise RuntimeError("Shut the machine down before exporting")
        name = dom.name()
        folder = os.path.join(
            dest_dir, f"{name}-{_time.strftime('%Y%m%d-%H%M%S')}"
        )
        os.makedirs(folder)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        manifest = {"name": name, "disks": []}
        for d in root.findall("devices/disk"):
            if d.get("device") != "disk":
                continue
            src = d.find("source")
            target = d.find("target")
            driver = d.find("driver")
            if src is None or not src.get("file"):
                continue
            vol = conn.storageVolLookupByPath(src.get("file"))
            dev = target.get("dev", "disk") if target is not None else "disk"
            fname = f"{dev}.{driver.get('type', 'qcow2') if driver is not None else 'qcow2'}"
            stream = conn.newStream()
            vol.download(stream, 0, 0)
            try:
                with open(os.path.join(folder, fname), "wb") as f:
                    stream.recvAll(lambda s, data, fh: fh.write(data), f)
                stream.finish()
            except (libvirt.libvirtError, OSError):
                stream.abort()  # or it is left open on the connection
                raise
            manifest["disks"].append(
                {"dev": dev, "file": fname,
                 "format": driver.get("type", "qcow2") if driver is not None else "qcow2"}
            )
        with open(os.path.join(folder, "domain.xml"), "w") as f:
            f.write(ET.tostring(root, encoding="unicode"))
        with open(os.path.join(folder, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)
        return folder

    return _with_conn(go)

def svc_import_backup(folder: str, pool_name: str) -> str:
    """Recreate an exported machine; disks land in the given pool."""
    import json
    import os

    def go(conn):
        with open(os.path.join(folder, "manifest.json")) as f:
            manifest = json.load(f)
        root = ET.parse(os.path.join(folder, "domain.xml")).getroot()
        name = manifest["name"]
        try:
            conn.lookupByName(name)
            raise RuntimeError(
                f"A machine named '{name}' already exists - delete or rename it first"
            )
        except libvirt.libvirtError:
            pass
        dev_to_path = {}
        pool = conn.storagePoolLookupByName(pool_name)
        for disk in manifest["disks"]:
            vol_name = free_volume_name(
                pool, f"{name}-{disk['dev']}.{disk['format']}"
            )
            path = svc_upload_volume_from_file_conn(
                conn, pool_name, vol_name,
                os.path.join(folder, disk["file"]), disk["format"],
            )
            dev_to_path[disk["dev"]] = path
        for d in root.findall("devices/disk"):
            target = d.find("target")
            src = d.find("source")
            if target is None or src is None:
                continue
            dev = target.get("dev")
            if dev in dev_to_path:
                src.set("file", dev_to_path[dev])
        uuid_el = root.find("uuid")
        if uuid_el is not None:
            root.remove(uuid_el)
        nvram = root.find("os/nvram")
        if nvram is not None:
            nvram.text = None
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return name

    return _with_conn(go)

def svc_upload_volume_from_file_conn(conn, pool_name, name, file_path, fmt,
                                     replace: bool = False):
    """Stream a local file into a pool volume.

    `replace` has to be asked for: this used to delete any volume of the
    same name without a word, so importing a backup twice - or one whose
    machine name matched an existing volume - destroyed a disk something
    else was using.
    """
    import os

    size = os.path.getsize(file_path)
    pool = conn.storagePoolLookupByName(pool_name)
    try:
        existing = pool.storageVolLookupByName(name)
    except libvirt.libvirtError:
        existing = None
    if existing is not None:
        if not replace:
            raise RuntimeError(
                f"'{name}' already exists in pool '{pool_name}'. Delete it "
                "first if it is not wanted - it is not overwritten in case "
                "another machine is using it."
            )
        existing.delete(0)
    vol = pool.createXML(
        f"""<volume>
  <name>{x(name)}</name>
  <capacity unit='bytes'>{size}</capacity>
  <target><format type='{x(fmt)}'/></target>
</volume>""",
        0,
    )
    stream = conn.newStream()
    vol.upload(stream, 0, size)
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                sent = 0
                while sent < len(chunk):
                    sent += stream.send(chunk[sent:])
        stream.finish()
    except (libvirt.libvirtError, OSError):
        stream.abort()
        vol.delete(0)
        raise
    return vol.path()


def backup_chain(folder: str) -> list[str]:
    """The backup folders from the full run to this one, oldest first.

    Each folder's manifest names the checkpoint it wrote and the one it was
    based on; sibling folders in the same directory are joined on those
    names. Pure filesystem work - no libvirt.
    """
    import json
    import os

    def manifest(path):
        try:
            with open(os.path.join(path, "manifest.json")) as f:
                m = json.load(f)
        except (OSError, ValueError):
            return None
        # exports also carry a manifest, but no checkpoint chain
        return m if isinstance(m, dict) and m.get("checkpoint") else None

    folder = os.path.abspath(folder)
    here = manifest(folder)
    if here is None:
        raise RuntimeError(
            "Not an incremental-backup folder - no manifest.json naming a "
            "checkpoint. Exported machines go through Import instead."
        )
    parent_dir = os.path.dirname(folder)
    by_checkpoint: dict[str, tuple[str, dict]] = {}
    for entry in sorted(os.listdir(parent_dir)):
        path = os.path.join(parent_dir, entry)
        m = manifest(path)
        if m and m.get("name") == here["name"]:
            by_checkpoint[m["checkpoint"]] = (path, m)

    chain = [folder]
    m = here
    while m.get("parent"):
        found = by_checkpoint.get(m["parent"])
        if found is None:
            raise RuntimeError(
                f"This backup is based on checkpoint '{m['parent']}' and no "
                f"folder next to it provides that - restoring needs the "
                f"whole chain back to the full backup"
            )
        path, m = found
        if path in chain:
            raise RuntimeError("The backup manifests refer to each other in a loop")
        chain.insert(0, path)
    if m.get("kind") != "full":
        raise RuntimeError(
            "The start of this chain is not a full backup, so the disks "
            "cannot be rebuilt completely"
        )
    return chain

def _restore_plan(layers: list[str], workdir: str, dev: str):
    """What reassembling one disk runs: (copies, commands, result file).

    An incremental layer holds only the blocks that changed, so each one is
    copied into workdir and rebased onto the layer below it - the backup
    folders themselves are never written to - then the top of the chain is
    flattened into a standalone image.
    """
    import os

    copies: list[tuple[str, str]] = []
    cmds: list[list[str]] = []
    prev = layers[0]
    for i, layer in enumerate(layers[1:], 1):
        copy = os.path.join(workdir, f"{dev}.layer{i}.qcow2")
        copies.append((layer, copy))
        cmds.append([
            "qemu-img", "rebase", "-u", "-f", "qcow2", "-F", "qcow2",
            "-b", prev, copy,
        ])
        prev = copy
    out_file = os.path.join(workdir, f"{dev}.restored.qcow2")
    cmds.append(["qemu-img", "convert", "-O", "qcow2", prev, out_file])
    return copies, cmds, out_file

def svc_restore_backup(folder: str, pool_name: str) -> str:
    """Rebuild disks from a full+incremental chain and define the machine.

    The machine gets its original name, or '<name>-restored' when that is
    taken - the original may well still exist, which is the point of a
    backup. MAC addresses are dropped in that case so the two can run
    side by side.
    """
    import json
    import os
    import tempfile

    chain = backup_chain(folder)
    with open(os.path.join(folder, "manifest.json")) as f:
        manifest = json.load(f)
    devs = manifest["disks"]

    workdir = tempfile.mkdtemp(prefix="vmmanager-restore-")
    try:
        restored: dict[str, str] = {}
        for dev in devs:
            layers = [
                p for c in chain
                if os.path.exists(p := os.path.join(c, f"{dev}.qcow2"))
            ]
            if os.path.join(chain[-1], f"{dev}.qcow2") not in layers:
                raise RuntimeError(
                    f"{dev}.qcow2 is missing from the backup folder"
                )
            copies, cmds, out_file = _restore_plan(layers, workdir, dev)
            for src, dst in copies:
                shutil.copyfile(src, dst)
            for cmd in cmds:
                try:
                    proc = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=3600
                    )
                except FileNotFoundError:
                    raise RuntimeError(
                        "qemu-img is needed to rebuild a backup chain - "
                        "install qemu-img and try again"
                    ) from None
                if proc.returncode != 0:
                    tail = (proc.stderr or "").strip().splitlines()
                    raise RuntimeError(
                        f"{' '.join(cmd[:2])} failed: "
                        f"{tail[-1] if tail else 'no error output'}"
                    )
            restored[dev] = out_file

        def go(conn):
            root = ET.parse(os.path.join(folder, "domain.xml")).getroot()
            name = manifest["name"]
            final = name
            for candidate in [name, f"{name}-restored"] + [
                f"{name}-restored{i}" for i in range(2, 100)
            ]:
                try:
                    conn.lookupByName(candidate)
                except libvirt.libvirtError:
                    final = candidate
                    break
            else:
                raise RuntimeError(f"Every name from '{name}' on is taken")
            name_el = root.find("name")
            if name_el is not None:
                name_el.text = final
            for d in root.findall("devices/disk"):
                target = d.find("target")
                src = d.find("source")
                if target is None or src is None:
                    continue
                dev = target.get("dev")
                if dev in restored:
                    pool = conn.storagePoolLookupByName(pool_name)
                    path = svc_upload_volume_from_file_conn(
                        conn, pool_name,
                        free_volume_name(pool, f"{final}-{dev}.qcow2"),
                        restored[dev], "qcow2",
                    )
                    src.set("file", path)
                    driver = d.find("driver")
                    if driver is not None:
                        driver.set("type", "qcow2")
            uuid_el = root.find("uuid")
            if uuid_el is not None:
                root.remove(uuid_el)
            nvram = root.find("os/nvram")
            if nvram is not None:
                nvram.text = None
            if final != name:
                # the original still exists - two NICs with one MAC collide
                for iface in root.findall("devices/interface"):
                    mac = iface.find("mac")
                    if mac is not None:
                        iface.remove(mac)
            conn.defineXML(ET.tostring(root, encoding="unicode"))
            return final

        return _with_conn(go)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def svc_move_disk(
    uuid: str, dev: str, pool_name: str, delete_source: bool = False
) -> str:
    """Move one disk's storage into another pool.

    On a running machine libvirt's blockCopy mirrors the disk onto the new
    volume while the guest keeps writing, then pivots to it - no downtime.
    On a stopped one the volume is cloned through the storage API. Either
    way the persistent definition ends up pointing at the new volume; the
    old one is deleted only when asked, and never while another machine
    still refers to it.
    """
    import os
    import time as _time

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        active = bool(dom.isActive())
        root = ET.fromstring(
            dom.XMLDesc(0 if active else libvirt.VIR_DOMAIN_XML_INACTIVE)
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
                f"'{dev}' is not a file-backed disk, so it has no volume to move"
            )
        src_path = src.get("file")
        driver = disk.find("driver")
        fmt = driver.get("type", "qcow2") if driver is not None else "qcow2"

        dest_pool = conn.storagePoolLookupByName(pool_name)
        if not dest_pool.isActive():
            raise RuntimeError(f"Pool '{pool_name}' is not started")
        try:
            src_vol = conn.storageVolLookupByPath(src_path)
        except libvirt.libvirtError:
            src_vol = None
        if src_vol is not None and src_vol.storagePoolLookupByVolume().name() == pool_name:
            raise RuntimeError(f"'{dev}' is already in pool '{pool_name}'")
        capacity = (
            src_vol.info()[1] if src_vol is not None else os.path.getsize(src_path)
        )

        vol_name = os.path.basename(src_path)
        try:
            dest_pool.storageVolLookupByName(vol_name)
            vol_name = f"{dom.name()}-{vol_name}"
            dest_pool.storageVolLookupByName(vol_name)
            raise RuntimeError(
                f"Both '{os.path.basename(src_path)}' and '{vol_name}' "
                f"already exist in '{pool_name}'"
            )
        except libvirt.libvirtError:
            pass
        vol_xml = (
            f"<volume><name>{x(vol_name)}</name>"
            f"<capacity unit='bytes'>{capacity}</capacity>"
            f"<target><format type='{x(fmt)}'/></target></volume>"
        )

        if active:
            new_vol = dest_pool.createXML(vol_xml, 0)
            dest_path = new_vol.path()
            try:
                dom.blockCopy(
                    dev,
                    f"<disk type='file'><source file='{x(dest_path)}'/>"
                    f"<driver type='{x(fmt)}'/></disk>",
                    None,
                    libvirt.VIR_DOMAIN_BLOCK_COPY_REUSE_EXT
                    | libvirt.VIR_DOMAIN_BLOCK_COPY_TRANSIENT_JOB,
                )
                deadline = _time.monotonic() + 3600 * 4
                while _time.monotonic() < deadline:
                    info = dom.blockJobInfo(dev, 0)
                    if not info:
                        raise RuntimeError(
                            "The copy job disappeared before finishing - "
                            "check the libvirt log"
                        )
                    if info.get("end") and info["cur"] >= info["end"]:
                        break
                    _time.sleep(0.5)
                else:
                    raise RuntimeError("Copy did not finish within four hours")
                dom.blockJobAbort(dev, libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT)
            except Exception:
                try:
                    dom.blockJobAbort(dev, 0)
                except libvirt.libvirtError:
                    pass
                new_vol.delete(0)
                raise
        else:
            new_vol = dest_pool.createXMLFrom(vol_xml, src_vol, 0)
            dest_path = new_vol.path()

        # point the persistent definition at the new volume
        proot = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        for d in proot.findall("devices/disk"):
            t = d.find("target")
            s = d.find("source")
            if t is not None and t.get("dev") == dev and s is not None:
                s.set("file", dest_path)
        conn.defineXML(ET.tostring(proot, encoding="unicode"))

        note = "old volume kept"
        if delete_source and src_vol is not None:
            used_by = []
            for other in conn.listAllDomains():
                if other.UUIDString() == uuid:
                    continue
                if src_path in other.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE):
                    used_by.append(other.name())
            if used_by:
                note = (
                    f"old volume kept - {', '.join(used_by)} still refers to it"
                )
            else:
                src_vol.delete(0)
                note = "old volume deleted"
        return f"{dev} moved to '{pool_name}' ({note})"

    return _with_conn(go)


# -- the rest of libvirt's pool types
#
# Each needs a different <source>; a few need no target at all. Kept in one
# table so the dialog and the XML builder cannot drift apart.

POOL_TYPES = {
    "dir": {"label": "dir - local directory", "target": True, "fields": ()},
    "netfs": {
        "label": "netfs - NFS export", "target": True,
        "fields": (("host", "nfs host", "nas.local"),
                   ("export", "exported path", "/export/images")),
    },
    "fs": {
        "label": "fs - mount a block device", "target": True,
        "fields": (("source_device", "block device", "/dev/sdb1"),),
    },
    "logical": {
        "label": "logical - LVM volume group", "target": False,
        "fields": (("source_name", "volume group", "vg-vms"),
                   ("source_device", "pv device (only to create a new vg)", "/dev/sdb")),
    },
    "disk": {
        "label": "disk - whole disk, partitions as volumes", "target": True,
        "fields": (("source_device", "disk device", "/dev/sdb"),),
    },
    "iscsi": {
        "label": "iscsi - iSCSI target", "target": False,
        "fields": (("host", "portal host", "san.local"),
                   ("source_device", "target IQN", "iqn.2026-01.local.san:vms")),
    },
    "iscsi-direct": {
        "label": "iscsi-direct, iSCSI without a host mount", "target": False,
        "fields": (("host", "portal host", "san.local"),
                   ("source_device", "target IQN", "iqn.2026-01.local.san:vms"),
                   ("initiator", "initiator IQN", "iqn.2026-01.local.host:init")),
    },
    "scsi": {
        "label": "scsi - an SCSI host adapter", "target": True,
        "fields": (("source_name", "adapter name", "host0"),),
    },
    "mpath": {
        "label": "mpath - multipath devices", "target": True, "fields": (),
    },
    "rbd": {
        "label": "rbd - Ceph RADOS block device", "target": False,
        "fields": (("source_name", "ceph pool", "libvirt-pool"),
                   ("host", "monitor host", "ceph-mon.local"),
                   ("auth_user", "cephx user (optional)", "libvirt"),
                   ("secret_uuid", "libvirt secret uuid (optional)", "")),
    },
    "gluster": {
        "label": "gluster - GlusterFS volume", "target": False,
        "fields": (("source_name", "gluster volume", "gv0"),
                   ("host", "gluster host", "gluster.local"),
                   ("export", "path in volume", "/")),
    },
    "zfs": {
        "label": "zfs - ZFS pool", "target": False,
        "fields": (("source_name", "zpool name", "tank/vms"),),
    },
}


def _pool_source_xml(ptype: str, opts: dict) -> str:
    host = opts.get("host", "")
    export = opts.get("export", "")
    device = opts.get("source_device", "")
    name = opts.get("source_name", "")
    if ptype == "netfs":
        return (
            f"<source><host name='{x(host)}'/><dir path='{x(export)}'/>"
            "<format type='auto'/></source>"
        )
    if ptype in ("fs", "disk"):
        fmt = "<format type='gpt'/>" if ptype == "disk" else ""
        return f"<source><device path='{x(device)}'/>{fmt}</source>"
    if ptype == "logical":
        dev = f"<device path='{x(device)}'/>" if device else ""
        return f"<source>{dev}<name>{x(name)}</name></source>"
    if ptype == "iscsi":
        return f"<source><host name='{x(host)}'/><device path='{x(device)}'/></source>"
    if ptype == "iscsi-direct":
        initiator = opts.get("initiator", "")
        iqn = (
            f"<initiator><iqn name='{x(initiator)}'/></initiator>" if initiator else ""
        )
        return (
            f"<source><host name='{x(host)}'/><device path='{x(device)}'/>{iqn}</source>"
        )
    if ptype == "scsi":
        return f"<source><adapter name='{x(name)}'/></source>"
    if ptype == "rbd":
        auth_user = opts.get("auth_user", "")
        secret = opts.get("secret_uuid", "")
        auth = ""
        if auth_user and secret:
            auth = (
                f"<auth username='{x(auth_user)}' type='ceph'>"
                f"<secret uuid='{x(secret)}'/></auth>"
            )
        return f"<source><name>{x(name)}</name><host name='{x(host)}'/>{auth}</source>"
    if ptype == "gluster":
        return (
            f"<source><name>{x(name)}</name><host name='{x(host)}'/>"
            f"<dir path='{x(export or '/')}'/></source>"
        )
    if ptype == "zfs":
        return f"<source><name>{x(name)}</name></source>"
    return ""


def svc_create_pool_ex(name: str, ptype: str, target: str, opts: dict) -> None:
    """Define, build and start a pool of any supported type."""

    def go(conn):
        spec = POOL_TYPES.get(ptype)
        if spec is None:
            raise RuntimeError(f"Unsupported pool type '{ptype}'")
        source = _pool_source_xml(ptype, opts)
        target_path = target
        if not target_path and ptype in ("logical", "zfs"):
            target_path = f"/dev/{opts.get('source_name', name)}"
        elif not target_path and ptype in ("iscsi", "iscsi-direct", "scsi", "mpath"):
            target_path = "/dev/disk/by-path"
        elif not target_path and ptype == "disk":
            target_path = "/dev"  # partitions appear as /dev nodes
        if spec["target"] and not target_path:
            raise RuntimeError(
                f"A {ptype} pool needs a target path: the directory its "
                "volumes live in."
            )
        missing = [
            label for field, label, _placeholder in spec["fields"]
            if not opts.get(field) and "optional" not in label and "only to" not in label
        ]
        if missing:
            raise RuntimeError(
                f"A {ptype} pool also needs: {', '.join(missing)}."
            )
        target_xml = (
            f"<target><path>{x(target_path)}</path></target>" if target_path else ""
        )
        pool = conn.storagePoolDefineXML(
            f"<pool type='{x(ptype)}'><name>{x(name)}</name>{source}{target_xml}</pool>", 0
        )
        try:
            pool.build(0)
        except libvirt.libvirtError:
            pass  # nothing to build for most network-backed types
        try:
            pool.create(0)
            pool.setAutostart(1)
        except libvirt.libvirtError:
            pool.undefine()
            raise

    _with_conn(go)


def volumes_backed_by(conn, paths) -> dict[str, list[str]]:
    """For each path, the volumes layered on top of it.

    A linked clone is a qcow2 overlay whose backing file is the template's
    image, and that relationship lives in the *volume* XML - a domain's own
    description says nothing about it. Deleting or rewriting a file that
    something else is layered on breaks every one of them, so the paths
    that destroy data ask this first.
    """
    wanted = {p for p in paths if p}
    found: dict[str, list[str]] = {p: [] for p in wanted}
    if not wanted:
        return found
    for pool in conn.listAllStoragePools():
        if not pool.isActive():
            continue
        try:
            volumes = pool.listAllVolumes()
        except libvirt.libvirtError:
            continue
        for vol in volumes:
            try:
                root = ET.fromstring(vol.XMLDesc(0))
            except (libvirt.libvirtError, ET.ParseError):
                continue
            parent = root.findtext("backingStore/path")
            if parent in found and vol.path() != parent:
                found[parent].append(vol.path())
    return found

def free_volume_name(pool, name: str) -> str:
    """`name`, or the first `name-2`, `name-3`… that nothing owns.

    Uploading over a volume that already exists destroys whatever was in
    it, which for an import or a restore is somebody else's disk.
    """
    stem, dot, ext = name.rpartition(".")
    stem = stem or name
    ext = f"{dot}{ext}" if dot else ""
    candidate = name
    for n in range(2, 100):
        try:
            pool.storageVolLookupByName(candidate)
        except libvirt.libvirtError:
            return candidate
        candidate = f"{stem}-{n}{ext}"
    raise RuntimeError(f"every name from '{name}' on is taken in this pool")

def svc_backing_index() -> BackingIndex:
    """Read every volume once, for the template/clone relationship.

    The backing chain lives in the volume XML rather than the domain XML, so
    a domain's own description cannot tell you what it is layered on.
    """

    def go(conn):
        backing_of: dict[str, str] = {}
        capacity_of: dict[str, int] = {}
        allocation_of: dict[str, int] = {}
        for pool in conn.listAllStoragePools():
            if not pool.isActive():
                continue
            try:
                volumes = pool.listAllVolumes()
            except libvirt.libvirtError:
                continue
            for vol in volumes:
                try:
                    root = ET.fromstring(vol.XMLDesc(0))
                except (libvirt.libvirtError, ET.ParseError):
                    continue
                path = root.findtext("target/path") or ""
                if not path:
                    continue
                parent = root.findtext("backingStore/path")
                if parent:
                    backing_of[path] = parent
                capacity = root.findtext("capacity")
                if capacity and capacity.isdigit():
                    capacity_of[path] = int(capacity)
                allocation = root.findtext("allocation")
                if allocation and allocation.isdigit():
                    allocation_of[path] = int(allocation)
        return BackingIndex(backing_of, capacity_of, allocation_of)

    return _with_conn(go)
