"""qemu-img, brought into the app.

libvirt reports a volume's capacity and allocation and nothing else. Every
other question about an image - is it damaged, what is it layered on, what
cluster size was it made with - needed a terminal until now.

The parsers are tested against output from a real qemu-img rather than
hand-written JSON where that is possible, because the shape of that output
is the thing most likely to move.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from vmmanager.core.imgtools import (
    parse_check,
    parse_info,
    svc_check_image,
    svc_convert_image,
    svc_image_info,
)

qemu_img = pytest.mark.skipif(
    shutil.which("qemu-img") is None, reason="qemu-img is not installed"
)


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "disk.qcow2"
    subprocess.run(
        ["qemu-img", "create", "-f", "qcow2", "-o", "cluster_size=128k",
         str(path), "64M"],
        check=True, capture_output=True,
    )
    return path


# ---------------------------------------------------------------- parsing

def test_info_reads_what_the_image_costs_against_what_it_claims():
    info = parse_info(
        '{"format": "qcow2", "virtual-size": 21474836480,'
        ' "actual-size": 1048576, "cluster-size": 65536,'
        ' "format-specific": {"data": {"compat": "1.1", "corrupt": false}}}',
        "/pool/a.qcow2",
    )
    assert info.format == "qcow2"
    assert info.virtual_size == 21474836480
    assert info.actual_size == 1048576
    assert info.cluster_size == 65536
    assert info.thin, "20 GB to the guest, 1 MB on the host"


def test_a_full_image_is_not_reported_as_thin():
    info = parse_info(
        '{"virtual-size": 100, "actual-size": 100}', "/pool/raw.img"
    )
    assert not info.thin


def test_a_corrupt_flag_in_the_image_itself_is_carried_through():
    info = parse_info(
        '{"format": "qcow2",'
        ' "format-specific": {"data": {"corrupt": true}}}', "/pool/bad.qcow2"
    )
    assert info.corrupt


def test_unreadable_output_says_so_rather_than_guessing():
    with pytest.raises(RuntimeError, match="readable"):
        parse_info("not json at all", "/pool/x")


def test_a_clean_check_says_so():
    result = parse_check('{"leaks": 0, "check-errors": 0}', 0)
    assert result.ok
    assert result.summary == "No errors found."
    assert not result.repairable


def test_leaked_clusters_are_wasted_space_not_damage():
    """qemu-img exits 3 for this, and "3" on its own reads as alarming."""
    result = parse_check(
        '{"leaks": 12, "check-errors": 0, "total-clusters": 100,'
        ' "allocated-clusters": 40}', 3,
    )
    assert not result.ok
    assert result.leaks == 12
    assert result.errors == 0
    assert "safe to run" in result.summary
    assert result.repairable
    assert result.allocated_pct == 40


def test_real_damage_is_named_as_damage():
    result = parse_check('{"leaks": 0, "check-errors": 4}', 2)
    assert result.errors == 4
    assert "damaged" in result.summary
    assert "backup" in result.summary
    assert result.repairable


def test_a_check_that_could_not_run_reports_why():
    result = parse_check("", 1, stderr="Could not open 'x': No such file")
    assert not result.ok
    assert "No such file" in result.summary
    assert not result.repairable, "there is nothing to repair"


def test_a_zero_cluster_count_does_not_divide_by_zero():
    assert parse_check('{"total-clusters": 0}', 0).allocated_pct == 0


# ------------------------------------------------------- against qemu-img

@qemu_img
def test_it_reads_an_image_qemu_img_just_made(image):
    info = svc_image_info(str(image))
    assert info.format == "qcow2"
    assert info.cluster_size == 131072, "the 128k it was created with"
    assert info.virtual_size == 64 * 1024 ** 2
    assert info.thin


@qemu_img
def test_a_backing_file_is_reported(image, tmp_path):
    overlay = tmp_path / "overlay.qcow2"
    subprocess.run(
        ["qemu-img", "create", "-f", "qcow2", "-b", str(image),
         "-F", "qcow2", str(overlay)],
        check=True, capture_output=True,
    )
    info = svc_image_info(str(overlay))
    assert info.backing_file == str(image)
    assert info.backing_format == "qcow2"


@qemu_img
def test_a_fresh_image_checks_clean(image):
    result = svc_check_image(str(image))
    assert result.ok and result.errors == 0


@qemu_img
def test_converting_writes_a_new_file_and_leaves_the_old_one(image, tmp_path):
    dest = tmp_path / "converted.raw"
    message = svc_convert_image(str(image), "raw", dest=str(dest))

    assert dest.exists() and image.exists(), "never in place"
    assert str(dest) in message
    assert svc_image_info(str(dest)).format == "raw"


@qemu_img
def test_it_will_not_write_over_an_image_that_is_already_there(image, tmp_path):
    dest = tmp_path / "taken.raw"
    dest.write_bytes(b"something already here")
    with pytest.raises(RuntimeError, match="already exists"):
        svc_convert_image(str(image), "raw", dest=str(dest))
    assert dest.read_bytes() == b"something already here"


@qemu_img
def test_a_cluster_size_can_be_chosen_when_converting(image, tmp_path):
    dest = tmp_path / "big-clusters.qcow2"
    svc_convert_image(str(image), "qcow2", dest=str(dest), cluster_size="1M")
    assert svc_image_info(str(dest)).cluster_size == 1024 ** 2


def test_options_that_belong_to_qcow2_are_refused_for_raw(tmp_path):
    with pytest.raises(ValueError, match="cluster size"):
        svc_convert_image("/x", "raw", cluster_size="64k")
    with pytest.raises(ValueError, match="compressed"):
        svc_convert_image("/x", "raw", compress=True)
    with pytest.raises(ValueError, match="qcow2 or raw"):
        svc_convert_image("/x", "vmdk")
