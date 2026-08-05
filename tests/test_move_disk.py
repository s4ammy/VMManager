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
