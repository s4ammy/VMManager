"""What the machine's own definition does for its graphical console.

The complaint this answers came in as "I installed the virtio drivers in the
guest and the console is no faster and still will not resize". It was a VGA
display device: there is no accelerated driver for one, so the guest repaints
the whole screen for every change, and there is no mode to retarget, so nothing
can resize it. The drivers were never the problem, and nothing in the app said
so.
"""

from __future__ import annotations

import pytest

from vmmanager.core.models import DisplayHealth


def health(**kwargs) -> DisplayHealth:
    base = dict(graphics=("spice",), video_model="qxl", spice_agent_channel=True,
                tablet=True)
    base.update(kwargs)
    return DisplayHealth(**base)


def keys(h: DisplayHealth) -> list[str]:
    return [key for key, _what, _why in h.problems()]


def test_a_well_set_up_machine_has_nothing_to_say():
    assert health().problems() == []


def test_a_vga_device_is_the_first_thing_wrong():
    """It is also usually the whole answer, so it comes first."""
    assert keys(health(video_model="vga"))[0] == "video"


def test_virtio_and_qxl_are_both_accepted():
    """Either can be driven properly; there is no reason to nag about the other."""
    for model in ("virtio", "qxl"):
        assert "video" not in keys(health(video_model=model))


def test_spice_is_pointed_at_qxl_and_everything_else_at_virtio():
    """QXL is the one with a signed Windows driver on the virtio-win disc, and
    it is what carries the SPICE agent's resize."""
    assert health(graphics=("spice",)).best_video == "qxl"
    assert health(graphics=("vnc",)).best_video == "virtio"


def test_a_missing_spice_agent_channel_is_reported():
    assert "agent" in keys(health(spice_agent_channel=False))


def test_a_vnc_machine_is_not_asked_for_a_spice_agent():
    """There is nothing at the other end of that channel without SPICE."""
    assert "agent" not in keys(
        health(graphics=("vnc",), video_model="virtio", spice_agent_channel=False)
    )


def test_a_missing_tablet_is_reported():
    assert "tablet" in keys(health(tablet=False))


def test_a_machine_with_no_display_at_all_is_a_different_conversation():
    """Nothing to improve until there is something to connect to, which the
    console tab offers separately."""
    assert health(graphics=(), video_model="").problems() == []


def test_every_problem_says_what_and_why():
    """The dialog prints both, and a blank line in it would be worse than none."""
    for key, what, why in health(video_model="vga", spice_agent_channel=False,
                                 tablet=False).problems():
        assert key and what and why
        assert why.endswith(".")


# -- reading it off a real domain


def test_health_is_read_from_the_definition(testconn, domain):
    from vmmanager.libvirt_service import svc_display_health

    h = svc_display_health(domain.UUIDString())
    assert h.running is True
    assert isinstance(h.graphics, tuple)
    assert h.problems() == h.problems(), "pure function of what was read"


# -- the dialog over it


def test_the_dialog_offers_a_button_per_problem(qapp):
    from PySide6.QtWidgets import QPushButton

    from vmmanager.dialogs import DisplayFixDialog

    broken = health(video_model="vga", spice_agent_channel=False, tablet=False)
    dialog = DisplayFixDialog(None, "win11", broken)
    labels = [b.text() for b in dialog.findChildren(QPushButton)]
    for key in ("video", "agent", "tablet"):
        assert DisplayFixDialog.LABELS[key] in labels
    assert "Fix all of it" in labels
    dialog.close()


def test_fix_all_asks_for_every_problem_in_order(qapp):
    from PySide6.QtWidgets import QPushButton

    from vmmanager.dialogs import DisplayFixDialog

    broken = health(video_model="vga", spice_agent_channel=False, tablet=False)
    dialog = DisplayFixDialog(None, "win11", broken)
    for button in dialog.findChildren(QPushButton):
        if button.text() == "Fix all of it":
            button.click()
    assert dialog.actions == ["video", "agent", "tablet"]
    dialog.close()


def test_one_button_asks_for_one_fix(qapp):
    from PySide6.QtWidgets import QPushButton

    from vmmanager.dialogs import DisplayFixDialog

    dialog = DisplayFixDialog(None, "win11", health(tablet=False))
    for button in dialog.findChildren(QPushButton):
        if button.text() == DisplayFixDialog.LABELS["tablet"]:
            button.click()
    assert dialog.actions == ["tablet"]
    dialog.close()


def test_a_healthy_machine_still_opens_and_says_so(qapp):
    from PySide6.QtWidgets import QPushButton

    from vmmanager.dialogs import DisplayFixDialog

    dialog = DisplayFixDialog(None, "win11", health())
    labels = [b.text() for b in dialog.findChildren(QPushButton)]
    assert labels == ["Close"], "nothing to fix, so nothing to press"
    assert dialog.windowTitle()
    dialog.close()


def test_a_vnc_only_machine_is_told_why_its_clipboard_does_nothing():
    """The dead end this closes: with no SPICE display the check reported
    nothing at all, so the fix button stayed hidden and there was no way to
    find out that a VNC display cannot carry a clipboard."""
    assert "spice" in keys(health(graphics=("vnc",), video_model="virtio"))
    what = dict((k, w) for k, w, _y in
                health(graphics=("vnc",), video_model="virtio").problems())
    assert "clipboard" in what["spice"].lower()


def test_a_spice_machine_is_not_told_to_add_spice():
    assert "spice" not in keys(health(graphics=("spice",), video_model="qxl"))


# -- which display the console connects to


def _gfx(gtype, port=5900, tls_port=-1, socket=""):
    from vmmanager.core.models import GraphicsInfo

    return GraphicsInfo(type=gtype, host="127.0.0.1", port=port,
                        socket=socket, has_password=False, tls_port=tls_port)


def test_spice_is_preferred_when_this_build_can_speak_it():
    """Adding a SPICE display used to change nothing, because VNC was always
    picked - so the clipboard stayed broken with no way to tell why."""
    from vmmanager.pages.detail.console import pick_display

    both = [_gfx("vnc", 5900), _gfx("spice", 5901)]
    assert pick_display(both, spice_available=True).type == "spice"


def test_vnc_is_used_when_there_is_no_spice_glib():
    from vmmanager.pages.detail.console import pick_display

    both = [_gfx("vnc", 5900), _gfx("spice", 5901)]
    assert pick_display(both, spice_available=False).type == "vnc"


def test_a_spice_only_machine_is_still_returned_without_spice_glib():
    """Better a client that says what is missing than no console at all."""
    from vmmanager.pages.detail.console import pick_display

    assert pick_display([_gfx("spice", 5901)], spice_available=False).type == "spice"


def test_a_display_with_no_port_is_not_connectable():
    from vmmanager.pages.detail.console import pick_display

    assert pick_display([_gfx("vnc", port=-1)], spice_available=True) is None
    assert pick_display([_gfx("vnc", port=-1, socket="/run/x")],
                        spice_available=True).type == "vnc"
