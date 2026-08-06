"""Moving a disk between pools: the guard rails.

The fake driver cannot clone a volume across pools (its createXMLFrom looks
the source up in the destination pool), so the copy itself is exercised
against real libvirt; what's tested here is every way the move refuses to
start, which is the part with user-facing sentences in it.
"""

from __future__ import annotations

import pytest

from vmmanager.libvirt_service import svc_move_disk

DOMAIN = """
<domain type='test'>
  <name>mover</name>
  <memory unit='MiB'>64</memory>
  <os><type>hvm</type></os>
  <devices>
    <disk type='file' device='disk'>
      <source file='/default-pool/mover.qcow2'/>
      <target dev='vda'/>
      <driver name='qemu' type='qcow2'/>
    </disk>
    <disk type='block' device='disk'>
      <source dev='/dev/null'/>
      <target dev='vdb'/>
    </disk>
  </devices>
</domain>
"""


@pytest.fixture
def mover(testconn):
    pool = testconn.storagePoolLookupByName("default-pool")
    vol = pool.createXML(
        "<volume><name>mover.qcow2</name><capacity>1048576</capacity>"
        "<target><format type='qcow2'/></target></volume>", 0
    )
    dom = testconn.defineXML(DOMAIN)
    yield dom.UUIDString()
    dom.undefine()
    vol.delete(0)


def test_unknown_disk_is_refused(mover):
    with pytest.raises(RuntimeError, match="No disk 'vdz'"):
        svc_move_disk(mover, "vdz", "default-pool")


def test_non_file_disk_is_refused(mover):
    with pytest.raises(RuntimeError, match="not a file-backed disk"):
        svc_move_disk(mover, "vdb", "default-pool")


def test_moving_into_the_same_pool_is_refused(mover):
    with pytest.raises(RuntimeError, match="already in pool"):
        svc_move_disk(mover, "vda", "default-pool")


def test_stopped_destination_pool_is_refused(mover, testconn):
    testconn.storagePoolDefineXML(
        "<pool type='dir'><name>parked</name>"
        "<target><path>/parked</path></target></pool>", 0
    )
    try:
        with pytest.raises(RuntimeError, match="not started"):
            svc_move_disk(mover, "vda", "parked")
    finally:
        testconn.storagePoolLookupByName("parked").undefine()


# -- growing a disk


def test_growing_a_disk_asks_for_the_right_number_of_bytes(monkeypatch):
    """The fake driver has no virStorageVolResize, and the unit is the part
    worth pinning: blockResize counts KiB unless told otherwise, so passing
    bytes without the flag makes a disk 1024 times the wrong size."""
    import libvirt

    from vmmanager.core import devices

    calls = {}

    class _Vol:
        def info(self): return (0, 1 * 1024**3, 0)
        def resize(self, size, flags): calls["vol"] = (size, flags)

    class _Dom:
        def isActive(self): return True                     # noqa: N802
        def XMLDesc(self, _f=0):                            # noqa: N802
            return """<domain><devices>
              <disk device='disk'><source file='/p/a.qcow2'/>
                <target dev='vda'/></disk>
            </devices></domain>"""
        def blockResize(self, dev, size, flags):            # noqa: N802
            calls["block"] = (dev, size, flags)

    class _C:
        def lookupByUUIDString(self, _u): return _Dom()      # noqa: N802
        def storageVolLookupByPath(self, _p): return _Vol()  # noqa: N802

    monkeypatch.setattr(devices, "_with_conn", lambda go: go(_C()))
    message = devices.svc_grow_disk("uuid", "vda", 4.0)

    assert calls["vol"][0] == 4 * 1024**3
    dev, size, flags = calls["block"]
    assert (dev, size) == ("vda", 4 * 1024**3)
    assert flags & libvirt.VIR_DOMAIN_BLOCK_RESIZE_BYTES, "size would be read as KiB"
    assert "partition" in message, "the guest-side step has to be said"


def test_shrinking_is_refused_rather_than_silently_destructive(mover):
    """qcow2 will not shrink and a raw image loses whatever was past the new
    end, with the filesystem inside finding out later."""
    from vmmanager.libvirt_service import svc_grow_disk

    with pytest.raises(RuntimeError, match="only\n?\\s*grows|only grows"):
        svc_grow_disk(mover, "vda", 0.0005)


def test_growing_an_unknown_or_non_file_disk_is_refused(mover):
    from vmmanager.libvirt_service import svc_grow_disk

    with pytest.raises(RuntimeError, match="No disk 'vdz'"):
        svc_grow_disk(mover, "vdz", 10)
    with pytest.raises(RuntimeError, match="not a file-backed disk"):
        svc_grow_disk(mover, "vdb", 10)
