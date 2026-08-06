"""Copying the guest's framebuffer into the widget's image.

This is the console's hot path: it runs for every damage rectangle the
guest reports. It used to build a Python int per byte - 146 ms for a
1080p frame, which held the console at about seven frames a second no
matter how fast the guest was drawing - so the shape of the copy matters
as much as its correctness.
"""

from __future__ import annotations

import ctypes

import pytest
from PySide6.QtGui import QImage

from vmmanager.console.spice import SpiceClient


def _client(width: int, height: int, fill: int = 0):
    """A client with a fake guest framebuffer behind it."""
    client = SpiceClient()
    stride = width * 4
    buf = (ctypes.c_ubyte * (stride * height))()
    for i in range(len(buf)):
        buf[i] = (i + fill) % 251  # a pattern any offset error shows up in
    client._fb_buffer = buf  # keep it alive; ctypes does not
    client._fb = (ctypes.addressof(buf), stride, width, height)
    client._image = QImage(width, height, QImage.Format.Format_RGB32)
    client._image.fill(0)
    return client, buf, stride


def _pixel(img: QImage, x: int, y: int) -> int:
    return img.pixel(x, y) & 0xFFFFFF


def test_a_full_frame_copy_matches_the_source(qapp):
    client, buf, stride = _client(64, 32)
    client._copy_rect(0, 0, 64, 32)
    dst = memoryview(client._image.bits()).cast("B")
    assert bytes(dst[: stride * 32]) == bytes(buf[: stride * 32])


def test_a_partial_rect_only_touches_its_own_area(qapp):
    client, buf, stride = _client(64, 32)
    before = _pixel(client._image, 0, 0)
    client._copy_rect(16, 8, 8, 4)
    assert _pixel(client._image, 0, 0) == before, "outside the rect changed"
    # inside it matches the source
    dst = memoryview(client._image.bits()).cast("B")
    for row in range(4):
        s0 = (8 + row) * stride + 16 * 4
        d0 = (8 + row) * client._image.bytesPerLine() + 16 * 4
        assert bytes(dst[d0 : d0 + 32]) == bytes(buf[s0 : s0 + 32])


def test_the_fast_path_and_the_row_path_agree(qapp):
    """A full-width block takes one contiguous copy, a narrower one goes
    row by row - they must produce the same pixels."""
    wide, _buf, _stride = _client(64, 32)
    wide._copy_rect(0, 4, 64, 8)          # full width: the contiguous path
    narrow, _b2, _s2 = _client(64, 32)
    for row in range(8):                   # same area, one row at a time
        narrow._copy_rect(0, 4 + row, 63, 1)
        narrow._copy_rect(63, 4 + row, 1, 1)
    assert bytes(memoryview(wide._image.bits()).cast("B")) == bytes(
        memoryview(narrow._image.bits()).cast("B")
    )


@pytest.mark.parametrize("x,y,w,h", [
    (-5, -5, 10, 10),      # off the top-left
    (60, 28, 20, 20),      # past the bottom-right
    (0, 0, 0, 0),          # empty
    (100, 100, 4, 4),      # entirely outside
])
def test_a_rect_outside_the_framebuffer_does_not_corrupt_memory(qapp, x, y, w, h):
    client, _buf, _stride = _client(64, 32)
    client._copy_rect(x, y, w, h)  # must not raise or write out of bounds


def test_nothing_happens_without_a_framebuffer(qapp):
    client = SpiceClient()
    client._copy_rect(0, 0, 16, 16)  # no _fb, no _image
