"""The virtio-win driver disc: finding one, attaching it, remembering where.

Windows ships no virtio driver, so a machine given virtio storage boots into an
installer that reports no drives at all until this disc is mounted and the
driver is loaded off it. The disc is the same for every Windows guest and is
700 MB to fetch, so what matters here is that a copy already on the host is
found and offered, and that a path pointed at once comes back for the machine
after it.
"""

from __future__ import annotations

import pytest

from vmmanager.core.models import DomainSnapshot


def snap(name: str = "win11", **kwargs) -> DomainSnapshot:
    base = dict(uuid=f"uuid-{name}", name=name, state="shutoff", vcpus=2,
                memory_mb=2048, autostart=False)
    base.update(kwargs)
    return DomainSnapshot(**base)


@pytest.fixture
def scratch_settings(tmp_path):
    """QSettings pointed somewhere disposable.

    What it writes to otherwise is the user's own configuration, which a test
    has no business editing. Qt settles on the config directory the first time
    anything asks for it, so the environment is too late by the time the suite
    is running: setPath is what still moves it.
    """
    from pathlib import Path

    from PySide6.QtCore import QSettings

    fmt = QSettings.Format.NativeFormat
    scope = QSettings.Scope.UserScope
    real = Path(QSettings("vmmanager", "vmmanager").fileName()).parent.parent
    QSettings.setPath(fmt, scope, str(tmp_path))
    where = QSettings("vmmanager", "vmmanager").fileName()
    assert where.startswith(str(tmp_path)), f"settings still going to {where}"
    yield tmp_path
    QSettings.setPath(fmt, scope, str(real))


# -- finding a copy on the host


def test_a_packaged_disc_is_offered_before_one_in_downloads(tmp_path, monkeypatch):
    """Search order is the answer: the top hit is what the dialog starts on."""
    from vmmanager.data import catalog

    packaged = tmp_path / "usr" / "virtio-win.iso"
    downloaded = tmp_path / "downloads" / "virtio-win-0.1.271.iso"
    for path in (packaged, downloaded):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not really an iso")
    monkeypatch.setattr(catalog, "VIRTIO_WIN_PLACES", (
        str(packaged),
        str(tmp_path / "nothing-here" / "virtio-win.iso"),
        str(tmp_path / "downloads" / "virtio-win*.iso"),
    ))

    assert catalog.virtio_win_candidates() == [str(packaged), str(downloaded)]


def test_a_place_with_nothing_in_it_is_not_offered(tmp_path, monkeypatch):
    """A path that does not exist is not a disc, and neither is a directory."""
    from vmmanager.data import catalog

    (tmp_path / "virtio-win.iso").mkdir()  # a directory with the right name
    monkeypatch.setattr(catalog, "VIRTIO_WIN_PLACES", (
        str(tmp_path / "virtio-win.iso"),
        str(tmp_path / "gone" / "virtio-win*.iso"),
    ))

    assert catalog.virtio_win_candidates() == []


# -- the dialog


def test_the_dialog_starts_on_the_remembered_disc(qapp):
    from vmmanager.dialogs import VirtioIsoDialog

    dialog = VirtioIsoDialog(
        None, saved="/srv/isos/virtio-win.iso",
        found=["/usr/share/virtio-win/virtio-win.iso"],
    )
    assert dialog.chosen_path() == "/srv/isos/virtio-win.iso"
    assert dialog.remember.isChecked(), (
        "the point of asking once is not being asked again"
    )


def test_the_dialog_falls_back_to_what_it_found(qapp):
    """Nothing remembered yet, but the host has a copy: use it."""
    from vmmanager.dialogs import VirtioIsoDialog

    dialog = VirtioIsoDialog(None, found=["/usr/share/virtio-win/virtio-win.iso"])
    assert dialog.chosen_path() == "/usr/share/virtio-win/virtio-win.iso"


def test_the_dialog_refuses_to_attach_nothing(qapp):
    from vmmanager.dialogs import VirtioIsoDialog

    dialog = VirtioIsoDialog(None)
    assert dialog.chosen_path() == ""
    assert not dialog._ok_button.isEnabled()
    dialog.path.setCurrentText("/srv/isos/virtio-win.iso")
    assert dialog._ok_button.isEnabled()


def test_a_remote_host_is_not_offered_a_local_file_browser(qapp):
    """The path has to be one the remote libvirt can read, so pools only."""
    from PySide6.QtWidgets import QPushButton

    from vmmanager.dialogs import VirtioIsoDialog

    local = VirtioIsoDialog(None)
    remote = VirtioIsoDialog(None, remote=True)

    def buttons(dialog) -> list[str]:
        return [b.text() for b in dialog.findChildren(QPushButton)]

    assert "Browse…" in buttons(local)
    assert "Browse…" not in buttons(remote)
    assert "From pool…" in buttons(remote)


def test_asking_for_a_download_is_not_a_path(qapp):
    """The fallback when the host has no copy - said apart from the path so the
    caller does not attach the empty string."""
    from PySide6.QtWidgets import QDialog, QPushButton

    from vmmanager.dialogs import VirtioIsoDialog

    dialog = VirtioIsoDialog(None)
    assert not dialog.download
    for button in dialog.findChildren(QPushButton):
        if button.text().startswith("Download"):
            button.click()
    assert dialog.download
    assert dialog.result() == QDialog.DialogCode.Accepted


# -- attaching it from the hardware tab


class FakeDialog:
    """The picker, answered in advance."""

    path = "/usr/share/virtio-win/virtio-win.iso"
    remembered = True
    download = False

    def __init__(self, parent, **kwargs) -> None:
        from PySide6.QtWidgets import QCheckBox

        self.kwargs = kwargs
        FakeDialog.seen = kwargs
        self.remember = QCheckBox()
        self.remember.setChecked(FakeDialog.remembered)
        self.download = FakeDialog.download

    def exec(self):
        from PySide6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted

    def chosen_path(self) -> str:
        return FakeDialog.path


@pytest.fixture
def page(qapp, testconn, monkeypatch):
    """A detail page on a real machine, with service calls answered on the spot.

    Real because attaching reloads the hardware afterwards, and a uuid libvirt
    has never heard of turns that reload into a modal error dialog with nothing
    in a test to dismiss it.
    """
    from vmmanager.pages.detail import DetailPage
    import vmmanager.pages.detail.hardware as hardware

    def inline(fn, done=None, failed=None) -> None:
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 - run_task's own failed path
            if failed:
                failed(str(e))
            return
        if done:
            done(result)

    attached = []
    FakeDialog.path = "/usr/share/virtio-win/virtio-win.iso"
    FakeDialog.remembered = True
    FakeDialog.download = False
    monkeypatch.setattr(hardware, "run_task", inline)
    monkeypatch.setattr(hardware, "VirtioIsoDialog", FakeDialog)
    monkeypatch.setattr(hardware, "svc_list_pools", lambda: [])
    monkeypatch.setattr(
        hardware, "svc_attach_cdrom",
        lambda uuid, path: attached.append((uuid, path)) or "Applied to the config.",
    )
    uuid = testconn.lookupByName("test").UUIDString()
    page = DetailPage()
    page.uuid = uuid  # show_domain() would start the console and eight more reads
    page.update_from(snap(uuid=uuid))
    page.attached = attached
    yield page
    page.shutdown()


def test_attaching_the_disc_puts_it_on_this_machine(page, scratch_settings):
    page._add_virtio_iso()
    assert page.attached == [(page.uuid, FakeDialog.path)]


def test_the_disc_is_remembered_for_the_next_machine(page, scratch_settings):
    from vmmanager.pages.settings import virtio_win_iso

    page._add_virtio_iso()
    assert virtio_win_iso() == FakeDialog.path
    assert FakeDialog.seen["saved"] == "", "nothing was remembered before this"

    page._add_virtio_iso()
    assert FakeDialog.seen["saved"] == FakeDialog.path, (
        "the second machine should be offered the disc the first one used"
    )


def test_unticking_forgets_the_disc_it_was_offered(page, scratch_settings):
    from vmmanager.pages.settings import save_virtio_win_iso, virtio_win_iso

    save_virtio_win_iso(FakeDialog.path)
    FakeDialog.remembered = False
    page._add_virtio_iso()
    assert virtio_win_iso() == ""


def test_unticking_leaves_a_different_disc_alone(page, scratch_settings):
    """Attaching a one-off disc should not throw away the usual one."""
    from vmmanager.pages.settings import save_virtio_win_iso, virtio_win_iso

    save_virtio_win_iso("/srv/isos/virtio-win.iso")
    FakeDialog.path = "/tmp/some-other-virtio-win.iso"
    FakeDialog.remembered = False
    page._add_virtio_iso()
    assert virtio_win_iso() == "/srv/isos/virtio-win.iso"


def test_asking_for_a_download_attaches_nothing_yet(page, scratch_settings,
                                                    monkeypatch):
    """The download is a separate, long path - it must not also attach the
    empty picker answer on the way there."""
    asked = []
    monkeypatch.setattr(
        type(page), "_get_virtio_win", lambda self, uuid: asked.append(uuid)
    )
    FakeDialog.download = True

    page._add_virtio_iso()
    assert asked == [page.uuid]
    assert page.attached == []
