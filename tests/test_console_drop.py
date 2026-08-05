"""Dropping files on the console: what gets sent, and where it lands."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QUrl

from vmmanager.pages.detail.console import drop_destination, dropped_files


def test_local_files_come_out_of_the_mime_data():
    mime = QMimeData()
    mime.setUrls([
        QUrl.fromLocalFile("/home/me/notes.txt"),
        QUrl("https://example.com/not-a-file"),
        QUrl.fromLocalFile("/home/me/image.iso"),
    ])
    assert dropped_files(mime) == ["/home/me/notes.txt", "/home/me/image.iso"]


def test_plain_text_drag_is_not_a_file():
    mime = QMimeData()
    mime.setText("just some text")
    assert dropped_files(mime) == []


def test_unix_guests_get_tmp_and_windows_gets_public():
    assert drop_destination("debian", "notes.txt") == "/tmp/notes.txt"
    assert drop_destination("", "notes.txt") == "/tmp/notes.txt"
    assert (
        drop_destination("windows", "notes.txt")
        == "C:\\Users\\Public\\notes.txt"
    )
