"""Paths that destroy or rewrite disks, and what stops them.

A linked clone is a qcow2 overlay whose backing file is the template's
image. Deleting or flattening the image underneath breaks every clone at
once - quietly, whenever they next read a block they do not hold - so the
functions that could do it ask what is layered on a file first.
"""

from __future__ import annotations

import pytest

from vmmanager.core.storage import free_volume_name, volumes_backed_by


class _Vol:
    def __init__(self, name, path, backing=""):
        self._name, self._path, self._backing = name, path, backing

    def name(self): return self._name
    def path(self): return self._path

    def XMLDesc(self, _flags=0):  # noqa: N802 - libvirt's name
        backing = (f"<backingStore><path>{self._backing}</path></backingStore>"
                   if self._backing else "")
        return f"<volume><name>{self._name}</name>{backing}</volume>"


class _Pool:
    def __init__(self, vols, active=True):
        self._vols, self._active = vols, active

    def isActive(self): return self._active            # noqa: N802
    def listAllVolumes(self): return self._vols        # noqa: N802

    def storageVolLookupByName(self, name):            # noqa: N802
        import libvirt
        for v in self._vols:
            if v.name() == name:
                return v
        raise libvirt.libvirtError("not found")


class _Conn:
    def __init__(self, pools): self._pools = pools
    def listAllStoragePools(self): return self._pools  # noqa: N802


def test_a_template_reports_the_clones_layered_on_it():
    base = _Vol("base.qcow2", "/p/base.qcow2")
    clones = [_Vol(f"c{i}.qcow2", f"/p/c{i}.qcow2", "/p/base.qcow2")
              for i in range(3)]
    conn = _Conn([_Pool([base, *clones])])
    found = volumes_backed_by(conn, ["/p/base.qcow2"])
    assert len(found["/p/base.qcow2"]) == 3


def test_a_disk_nothing_is_built_on_reports_none():
    lone = _Vol("lone.qcow2", "/p/lone.qcow2")
    conn = _Conn([_Pool([lone])])
    assert volumes_backed_by(conn, ["/p/lone.qcow2"]) == {"/p/lone.qcow2": []}


def test_an_inactive_pool_is_skipped_not_crashed_on():
    conn = _Conn([_Pool([], active=False)])
    assert volumes_backed_by(conn, ["/p/x.qcow2"]) == {"/p/x.qcow2": []}


# -- never overwrite a volume that is already there


def test_a_free_name_is_used_rather_than_overwriting():
    """Uploading over an existing volume destroys whatever was in it, which
    for an import or a restore is somebody else's disk."""
    taken = _Pool([_Vol("web-vda.qcow2", "/p/web-vda.qcow2"),
                   _Vol("web-vda-2.qcow2", "/p/web-vda-2.qcow2")])
    assert free_volume_name(taken, "web-vda.qcow2") == "web-vda-3.qcow2"
    assert free_volume_name(taken, "other.qcow2") == "other.qcow2"


def test_a_name_without_an_extension_still_gets_a_suffix():
    taken = _Pool([_Vol("disk", "/p/disk")])
    assert free_volume_name(taken, "disk") == "disk-2"


def test_uploading_onto_an_existing_volume_is_refused(testconn):
    """The guard itself, against the real driver."""
    from vmmanager.core.storage import svc_upload_volume_from_file_conn
    import tempfile, os

    pool = testconn.storagePoolLookupByName("default-pool")
    vol = pool.createXML(
        "<volume><name>taken.qcow2</name><capacity>1048576</capacity>"
        "<target><format type='qcow2'/></target></volume>", 0
    )
    fd, path = tempfile.mkstemp()
    os.write(fd, b"x" * 16); os.close(fd)
    try:
        with pytest.raises(RuntimeError, match="already exists"):
            svc_upload_volume_from_file_conn(
                testconn, "default-pool", "taken.qcow2", path, "qcow2"
            )
    finally:
        os.unlink(path)
        vol.delete(0)


# -- compaction must not flatten an overlay


def test_an_overlay_is_not_offered_for_compaction(testconn, monkeypatch):
    """qemu-img convert merges an overlay with its backing file, so
    "compacting" a linked clone inflates it to the template's full size and
    cuts the link - the opposite of reclaiming space."""
    from vmmanager.core import storage

    pool = testconn.storagePoolLookupByName("default-pool")
    base = pool.createXML(
        "<volume><name>tpl.qcow2</name><capacity>1073741824</capacity>"
        "<target><format type='qcow2'/></target></volume>", 0
    )
    overlay_xml = (
        "<volume><name>ovl.qcow2</name><capacity>1073741824</capacity>"
        "<target><format type='qcow2'/></target>"
        "<backingStore><path>/default-pool/tpl.qcow2</path></backingStore>"
        "</volume>"
    )
    overlay = pool.createXML(overlay_xml, 0)
    try:
        monkeypatch.setattr(storage, "MIN_COMPACT_SIZE", 0)
        names = [c.name for c in storage.svc_compact_candidates()]
        assert "ovl.qcow2" not in names, "a linked clone was offered for compaction"
    finally:
        overlay.delete(0)
        base.delete(0)


# -- deleting a machine leaves disks other machines are built on


def test_delete_keeps_a_disk_that_clones_are_layered_on(testconn, monkeypatch):
    from vmmanager.core import domains

    deleted: list[str] = []

    class _Vol2:
        def __init__(self, path): self._path = path
        def delete(self, _f): deleted.append(self._path)

    class _Dom:
        def isActive(self): return False              # noqa: N802
        def undefineFlags(self, _f): pass             # noqa: N802
        def XMLDesc(self, _f=0):                      # noqa: N802
            return """<domain><devices>
              <disk device='disk'><source file='/p/base.qcow2'/></disk>
              <disk device='disk'><source file='/p/own.qcow2'/></disk>
            </devices></domain>"""

    class _C:
        def lookupByUUIDString(self, _u): return _Dom()          # noqa: N802
        def storageVolLookupByPath(self, p): return _Vol2(p)     # noqa: N802

    monkeypatch.setattr(domains, "_with_conn", lambda go: go(_C()))
    monkeypatch.setattr(
        "vmmanager.core.storage.volumes_backed_by",
        lambda _c, paths: {p: (["/p/clone.qcow2"] if "base" in p else [])
                           for p in paths},
    )
    message = domains.svc_delete("uuid", True)
    assert deleted == ["/p/own.qcow2"], "a disk with clones on it was deleted"
    assert "base.qcow2" in message and "layered" in message


# -- scheduled pruning leaves hand-made snapshots alone


def test_pruning_only_touches_what_the_scheduler_wrote(testconn, domain):
    from vmmanager.libvirt_service import (
        svc_create_snapshot, svc_list_snapshots, svc_prune_snapshots,
    )

    uuid = domain.UUIDString()
    made = ["auto-20260101-010101", "auto-20260102-010101",
            "auto-20260103-010101", "auto-before-upgrade"]
    for name in made:
        svc_create_snapshot(uuid, name, "")
    try:
        pruned = svc_prune_snapshots(uuid, "auto-", keep=1)
        left = {s.name for s in svc_list_snapshots(uuid)}
        assert "auto-before-upgrade" in left, "a hand-made snapshot was pruned"
        assert pruned == 2
        assert "auto-20260103-010101" in left  # the newest scheduled one stays
    finally:
        for s in domain.listAllSnapshots():
            s.delete(0)


def test_a_disk_that_will_not_delete_is_named_rather_than_ignored(monkeypatch):
    """"Machine deleted" over the top of a failed delete leaves an image on
    the host that the person asking believed they had just erased."""
    import libvirt

    from vmmanager.core import domains

    class _Vol3:
        def __init__(self, path): self._path = path
        def delete(self, _flags=0):
            raise libvirt.libvirtError("cannot unlink file: Permission denied")

    class _Dom:
        def isActive(self): return False              # noqa: N802
        def undefineFlags(self, _f): pass             # noqa: N802
        def XMLDesc(self, _f=0):                      # noqa: N802
            return """<domain><devices>
              <disk device='disk'><source file='/p/locked.qcow2'/></disk>
            </devices></domain>"""

    class _C:
        def lookupByUUIDString(self, _u): return _Dom()        # noqa: N802
        def storageVolLookupByPath(self, p): return _Vol3(p)   # noqa: N802

    monkeypatch.setattr(domains, "_with_conn", lambda go: go(_C()))
    monkeypatch.setattr(
        "vmmanager.core.storage.volumes_backed_by", lambda _c, paths: {}
    )
    message = domains.svc_delete("uuid", True)

    assert "Machine deleted" in message
    assert "could not be deleted" in message
    assert "/p/locked.qcow2" in message, "say which one"
