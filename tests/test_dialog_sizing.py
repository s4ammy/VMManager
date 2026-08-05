"""No dialog may be sized smaller than the content it draws.

Qt asks every child how short it is willing to be, and a word-wrapped QLabel
answers with about one line - it would rather shrink and let its text spill than
refuse. In a column of labels, fields and a button row, nothing objects, so the
minimum that reaches the window manager comes out under what the content
occupies and the body text is drawn over the buttons. Seven dialogs shipped like
that before this file existed.

The check deliberately does not reuse the sizing code's own arithmetic. It
squeezes each dialog to the smallest size it allows, then measures two things
that are true of a broken layout regardless of how it got there: text that needs
more height than its label has, and siblings whose rectangles overlap.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QWidget

import vmmanager.dialogs as dialogs

from test_ui_smoke import ARGS, POOL, dialog_names

# Real prose, because the bug only bites once a label has to wrap. The smoke
# table uses short strings that fit on one line and hide it.
LONG_BODY = (
    "This replaces the machine's definition. What is there now is kept as "
    "'before last switch', so you can come back to it whenever you want to."
)


def squeeze(qapp, dialog) -> None:
    """Show the dialog, then shrink it as far as it will go."""
    dialog.show()
    qapp.processEvents()
    dialog.resize(dialog.minimumSize())
    qapp.processEvents()


def clipped_text(dialog) -> list[str]:
    """Wrapped labels given less height than their text needs."""
    out = []
    for label in dialog.findChildren(QLabel):
        if not label.wordWrap() or not label.isVisible() or not label.text():
            continue
        needed = label.heightForWidth(label.width())
        if needed > label.height():
            out.append(
                f"{label.text()[:40]!r} has {label.height()}px, needs "
                f"{needed}px at {label.width()}px wide"
            )
    return out


def overlapping(dialog) -> list[str]:
    """Widgets sharing a parent whose rectangles intersect."""
    out = []
    for parent in [dialog, *dialog.findChildren(QWidget)]:
        siblings = [
            child for child in parent.children()
            if isinstance(child, QWidget) and child.isVisible()
            and not child.isWindow()
        ]
        for index, first in enumerate(siblings):
            for second in siblings[index + 1:]:
                if first.geometry().intersects(second.geometry()):
                    out.append(
                        f"{type(first).__name__}{first.geometry().getRect()} over "
                        f"{type(second).__name__}{second.geometry().getRect()}"
                    )
    return out


def assert_fits(qapp, dialog, name: str) -> None:
    squeeze(qapp, dialog)
    problems = clipped_text(dialog) + overlapping(dialog)
    assert problems == [], (
        f"{name} at its minimum {dialog.width()}x{dialog.height()}:\n  "
        + "\n  ".join(problems)
    )
    dialog.close()


@pytest.mark.parametrize("name", dialog_names())
def test_dialog_fits_at_its_minimum_size(qapp, name):
    args = ARGS.get(name)
    if isinstance(args, str):
        pytest.skip(args)
    assert args is not None, f"{name} has no smoke-test arguments"
    assert_fits(qapp, getattr(dialogs, name)(None, *args), name)


@pytest.mark.parametrize("name,make", [
    ("ConfirmDialog", lambda: dialogs.ConfirmDialog(
        None, "Switch win11 to 'prod'", LONG_BODY, "Switch")),
    ("ErrorDialog", lambda: dialogs.ErrorDialog(
        None, "Could not start web-01", LONG_BODY)),
    ("LabelsDialog", lambda: dialogs.LabelsDialog(
        None, "a machine with a rather long descriptive title", LONG_BODY)),
])
def test_dialog_fits_with_prose_in_it(qapp, name, make):
    """The same dialogs again, with text long enough to wrap."""
    assert_fits(qapp, make(), f"{name} (long text)")


def test_wizard_fits(qapp):
    from vmmanager.wizard import NewVmDialog

    assert_fits(qapp, NewVmDialog(None, ["default"], [POOL], host_cpus=16,
                                  host_mem_mb=65536), "NewVmDialog")


def test_deploy_dialog_fits(qapp):
    from vmmanager.pages.templates import DeployDialog

    assert_fits(qapp, DeployDialog(None, "golden", ["default"], "default"),
                "DeployDialog")


def test_new_stack_dialog_fits(qapp):
    from vmmanager.pages.stacks import NewStackDialog

    assert_fits(qapp, NewStackDialog(None, ["web-01", "db-01"]), "NewStackDialog")


def test_tuning_dialog_fits(qapp, testconn):
    from vmmanager.core.models import DiskInfo
    from vmmanager.core.tuning import Tuning
    from vmmanager.libvirt_service import svc_host_topology

    disk = DiskInfo(dev="vda", bus="virtio", source="/pool/a.qcow2",
                    format="qcow2", device="disk")
    assert_fits(qapp, dialogs.TuningDialog(
        None, "web-01", 4, svc_host_topology(), Tuning(), (disk,)
    ), "TuningDialog")


def test_guest_features_dialog_fits(qapp, testconn):
    from vmmanager.libvirt_service import (svc_feature_support, svc_get_features,
                                           svc_list_evdev)

    uuid = testconn.lookupByName("test").UUIDString()
    assert_fits(qapp, dialogs.GuestFeaturesDialog(
        None, "web-01", svc_get_features(uuid), svc_feature_support(),
        svc_list_evdev(),
    ), "GuestFeaturesDialog")


def test_a_note_filled_in_later_still_fits(qapp):
    """ConnectionDialog writes its probe result into a wrapped label on reply.

    A message longer than whatever was there when the dialog was measured has to
    raise the floor, or the dialog goes straight back to drawing over itself.
    """
    dialog = dialogs.ConnectionDialog(None)
    squeeze(qapp, dialog)
    was = dialog.minimumHeight()
    dialog.probe_result.setText(
        "could not connect to qemu+ssh://buildhost.internal/system: "
        "ssh: connect to host buildhost.internal port 22: no route to host, "
        "and the saved key no longer matches the one the host offers"
    )
    qapp.processEvents()
    assert dialog.minimumHeight() > was, (
        "a longer message did not raise the dialog's minimum height"
    )
    assert_fits(qapp, dialog, "ConnectionDialog with a long probe result")


def test_a_shorter_note_does_not_leave_the_floor_raised(qapp):
    """Otherwise one long error inflates the dialog for the rest of its life."""
    dialog = dialogs.ConnectionDialog(None)
    squeeze(qapp, dialog)
    settled = dialog.minimumHeight()
    dialog.probe_result.setText("a much longer message than " + "x " * 60)
    qapp.processEvents()
    inflated = dialog.minimumHeight()
    assert inflated > settled, "the long message should have raised the floor"

    dialog.probe_result.setText("connected")
    qapp.processEvents()
    # Within a pixel: an empty label measures a hair shorter than one that has
    # held text, and this label started out empty.
    assert dialog.minimumHeight() <= settled + 1
    dialog.close()


def test_every_dialog_inherits_the_sizing(qapp):
    """A dialog built on plain QDialog would miss the fix silently."""
    from PySide6.QtWidgets import QDialog

    from vmmanager.dialogs import SizedDialog

    plain = []
    for name in dialog_names():
        cls = getattr(dialogs, name)
        if issubclass(cls, QDialog) and not issubclass(cls, SizedDialog):
            plain.append(name)
    assert plain == [], (
        f"these subclass QDialog directly, so nothing measures them: {plain}"
    )
