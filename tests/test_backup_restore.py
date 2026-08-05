"""Rebuilding a machine from a full+incremental backup chain.

The chain walk and the qemu-img plan are pure logic, tested against real
folders on disk; nothing here touches libvirt or runs qemu-img.
"""

from __future__ import annotations

import json
import os

import pytest

from vmmanager.core.storage import _restore_plan, backup_chain


def _mk_backup(root, folder, name, kind, checkpoint, parent, disks=("vda",)):
    path = root / folder
    path.mkdir()
    (path / "manifest.json").write_text(json.dumps({
        "name": name, "kind": kind, "checkpoint": checkpoint,
        "parent": parent, "disks": list(disks),
    }))
    for dev in disks:
        (path / f"{dev}.qcow2").write_bytes(b"")
    (path / "domain.xml").write_text("<domain/>")
    return str(path)


def test_chain_walks_from_incremental_back_to_full(tmp_path):
    full = _mk_backup(tmp_path, "vm-full-1", "vm", "full", "chk-1", None)
    incr1 = _mk_backup(tmp_path, "vm-incr-2", "vm", "incr", "chk-2", "chk-1")
    incr2 = _mk_backup(tmp_path, "vm-incr-3", "vm", "incr", "chk-3", "chk-2")
    assert backup_chain(incr2) == [full, incr1, incr2]
    assert backup_chain(incr1) == [full, incr1]
    assert backup_chain(full) == [full]


def test_chain_ignores_another_machines_folders(tmp_path):
    full = _mk_backup(tmp_path, "vm-full", "vm", "full", "chk-1", None)
    # same checkpoint names, different machine - must not be picked up
    _mk_backup(tmp_path, "other-full", "other", "full", "chk-1", None)
    _mk_backup(tmp_path, "other-incr", "other", "incr", "chk-2", "chk-1")
    incr = _mk_backup(tmp_path, "vm-incr", "vm", "incr", "chk-2", "chk-1")
    assert backup_chain(incr) == [full, incr]


def test_chain_with_missing_parent_says_so(tmp_path):
    incr = _mk_backup(tmp_path, "vm-incr", "vm", "incr", "chk-2", "chk-1")
    with pytest.raises(RuntimeError, match="whole chain"):
        backup_chain(incr)


def test_chain_that_never_reaches_a_full_backup_is_refused(tmp_path):
    # parent exists but is itself an incremental with a missing base
    a = _mk_backup(tmp_path, "vm-a", "vm", "incr", "chk-2", "chk-1")
    del a
    # a's parent chk-1 provided by another incremental that loops back
    incr = _mk_backup(tmp_path, "vm-b", "vm", "incr", "chk-1", "chk-2")
    with pytest.raises(RuntimeError, match="loop"):
        backup_chain(incr)


def test_a_plain_folder_is_not_a_backup(tmp_path):
    plain = tmp_path / "stuff"
    plain.mkdir()
    with pytest.raises(RuntimeError, match="manifest"):
        backup_chain(str(plain))


def test_an_export_folder_is_pointed_at_import_instead(tmp_path):
    exp = tmp_path / "vm-20260101"
    exp.mkdir()
    (exp / "manifest.json").write_text(json.dumps({
        "name": "vm", "disks": [{"dev": "vda", "file": "vda.qcow2"}],
    }))
    with pytest.raises(RuntimeError, match="Import"):
        backup_chain(str(exp))


def test_single_layer_plan_only_flattens(tmp_path):
    copies, cmds, out = _restore_plan(["/b/full/vda.qcow2"], str(tmp_path), "vda")
    assert copies == []
    assert cmds == [["qemu-img", "convert", "-O", "qcow2",
                     "/b/full/vda.qcow2", out]]
    assert out.endswith("vda.restored.qcow2")


def test_multi_layer_plan_rebases_copies_never_originals(tmp_path):
    layers = ["/b/full/vda.qcow2", "/b/i1/vda.qcow2", "/b/i2/vda.qcow2"]
    copies, cmds, out = _restore_plan(layers, str(tmp_path), "vda")
    # every incremental is copied out of the backup folder first
    assert [src for src, _ in copies] == layers[1:]
    dsts = [dst for _, dst in copies]
    assert all(dst.startswith(str(tmp_path)) for dst in dsts)
    # each copy is rebased onto the layer below it
    rebases = [c for c in cmds if c[1] == "rebase"]
    assert len(rebases) == 2
    assert rebases[0][-2:] == [layers[0], dsts[0]]
    assert rebases[1][-2:] == [dsts[0], dsts[1]]
    assert "-u" in rebases[0]
    # and only files under the workdir are ever written to
    written = {c[-1] for c in rebases} | {out}
    assert all(w.startswith(str(tmp_path)) for w in written)
    assert cmds[-1] == ["qemu-img", "convert", "-O", "qcow2", dsts[1], out]
