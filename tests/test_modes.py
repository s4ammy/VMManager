"""Named configurations: saving, switching, and the rails around switching.

Modelled on a hand-written script that flips a passthrough guest between a
GPU shape and a console shape. The interesting part is not the saving, it is
refusing to do the wrong thing: switching a running machine, or applying a
definition that belongs to a different one.
"""

from __future__ import annotations

import pytest


def stop(domain) -> None:
    """Shut the fake domain down, whatever state the driver handed it over in."""
    if domain.isActive():
        domain.destroy()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the mode store at a scratch database."""
    from vmmanager.data import history

    monkeypatch.setattr(history, "DB_PATH", tmp_path / "modes.db")
    return history


def test_saving_captures_the_definition(testconn, domain, store):
    from vmmanager.libvirt_service import svc_list_modes, svc_save_mode

    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug", "console only")
    modes = svc_list_modes(uuid)
    assert [m.name for m in modes] == ["debug"]
    assert modes[0].note == "console only"
    assert modes[0].active and modes[0].matches


def test_a_mode_stops_matching_once_the_machine_changes(testconn, domain, store):
    from vmmanager.libvirt_service import svc_list_modes, svc_save_mode, svc_set_labels

    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug")
    svc_set_labels(uuid, title="changed", description="")
    assert svc_list_modes(uuid)[0].matches is False


def test_switching_restores_the_saved_definition(testconn, domain, store):
    from vmmanager.libvirt_service import (svc_get_hardware, svc_save_mode,
                                           svc_set_labels, svc_switch_mode)

    stop(domain)              # switching a running machine is refused
    uuid = domain.UUIDString()
    svc_set_labels(uuid, title="before", description="")
    svc_save_mode(uuid, "debug")
    svc_set_labels(uuid, title="after", description="")
    assert svc_get_hardware(uuid).title == "after"

    svc_switch_mode(uuid, "debug")
    assert svc_get_hardware(uuid).title == "before"


def test_switching_keeps_what_was_there(testconn, domain, store):
    """The way back, without having to have thought of it in advance."""
    from vmmanager.core.modes import AUTOSAVE_NAME
    from vmmanager.libvirt_service import svc_list_modes, svc_save_mode, svc_switch_mode

    stop(domain)
    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug")
    svc_switch_mode(uuid, "debug")
    assert AUTOSAVE_NAME in [m.name for m in svc_list_modes(uuid)]


def test_a_running_machine_is_refused(testconn, domain, store):
    """A mode only applies on the next start, so switching now would mislead."""
    from vmmanager.libvirt_service import svc_save_mode, svc_switch_mode

    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug")
    assert domain.isActive()
    with pytest.raises(RuntimeError, match="running"):
        svc_switch_mode(uuid, "debug")


def test_a_mode_from_another_machine_is_refused(testconn, domain, store):
    """Applying it would redefine this machine out of existence."""
    from vmmanager.data import history
    from vmmanager.libvirt_service import svc_switch_mode

    uuid = domain.UUIDString()
    stop(domain)
    foreign = (
        "<domain type='test'><name>somebody-else</name>"
        "<uuid>11111111-2222-3333-4444-555555555555</uuid>"
        "<memory>65536</memory><os><type>hvm</type></os></domain>"
    )
    s = history.StatsStore(history.DB_PATH)
    try:
        s.save_mode(uuid, "foreign", foreign)
    finally:
        s.close()
    with pytest.raises(RuntimeError, match="different machine"):
        svc_switch_mode(uuid, "foreign")


def test_switching_to_something_that_is_not_there(testconn, domain, store):
    from vmmanager.libvirt_service import svc_switch_mode

    stop(domain)
    with pytest.raises(RuntimeError, match="No mode named"):
        svc_switch_mode(domain.UUIDString(), "nope")


def test_a_mode_needs_a_name(testconn, domain, store):
    from vmmanager.libvirt_service import svc_save_mode

    with pytest.raises(ValueError):
        svc_save_mode(domain.UUIDString(), "   ")


def test_deleting_a_mode_clears_it_as_active(testconn, domain, store):
    from vmmanager.libvirt_service import svc_delete_mode, svc_list_modes, svc_save_mode

    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug")
    svc_delete_mode(uuid, "debug")
    assert svc_list_modes(uuid) == []


def test_diff_reports_no_difference_when_it_matches(testconn, domain, store):
    from vmmanager.libvirt_service import svc_mode_diff, svc_save_mode

    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug")
    assert "already matches" in svc_mode_diff(uuid, "debug")


def test_diff_shows_what_changed(testconn, domain, store):
    from vmmanager.libvirt_service import svc_mode_diff, svc_save_mode, svc_set_labels

    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug")
    svc_set_labels(uuid, title="a new title", description="")
    diff = svc_mode_diff(uuid, "debug")
    assert "a new title" in diff
    assert diff.startswith("--- current")


def test_a_marker_that_needs_root_says_so_rather_than_claiming_success():
    from vmmanager.core.modes import _write_marker

    message = _write_marker("/etc/definitely-not-writable-by-us", "prod")
    assert "root" in message or "Could not write" in message


def test_diff_ignores_formatting(testconn, domain, store):
    """A definition that has been through ElementTree is spaced differently
    from one straight out of libvirt; without canonicalising, every line of a
    real diff moves and the useful change is lost in it."""
    import xml.etree.ElementTree as ET

    import libvirt

    from vmmanager.core.modes import canonical
    from vmmanager.data import history
    from vmmanager.libvirt_service import svc_mode_diff

    uuid = domain.UUIDString()
    original = domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
    # same document, reserialised: no semantic change at all
    reserialised = ET.tostring(ET.fromstring(original), encoding="unicode")
    assert reserialised != original, "the fixture needs the two to differ textually"

    s = history.StatsStore(history.DB_PATH)
    try:
        s.save_mode(uuid, "reformatted", reserialised)
    finally:
        s.close()
    assert canonical(reserialised) == canonical(original)
    assert "already matches" in svc_mode_diff(uuid, "reformatted")


def test_libvirt_namespace_prefixes_survive():
    """ElementTree invents ns0 unless the prefixes are registered, which makes
    a diff of a machine with libosinfo metadata unreadable."""
    import xml.etree.ElementTree as ET

    from vmmanager.core.modes import canonical

    xml = (
        '<domain type="kvm"><name>x</name><metadata>'
        '<libosinfo:libosinfo xmlns:libosinfo='
        '"http://libosinfo.org/xmlns/libvirt/domain/1.0">'
        '<libosinfo:os id="http://microsoft.com/win/11"/>'
        "</libosinfo:libosinfo></metadata></domain>"
    )
    out = canonical(xml)
    assert "libosinfo:os" in out
    assert "ns0:" not in out


# -- markers
#
# A mode may name a file to write its own name into, for something outside
# libvirt that has to know which mode is in use - typically a hook deciding
# whether to hand a graphics card over. That file usually belongs to root, which
# this process is not, so a switch can succeed and leave the marker stale: the
# definition says one thing and whatever reads the marker acts on another.


@pytest.fixture
def hook_default():
    """Put the configured reader back, whatever a test set it to."""
    from vmmanager.core import modes

    was = modes.hook_script()
    yield
    modes.set_hook_script(was)


def test_a_mode_without_a_marker_has_nothing_to_report(testconn, domain, store):
    from vmmanager.libvirt_service import svc_marker_state, svc_save_mode

    uuid = domain.UUIDString()
    svc_save_mode(uuid, "plain")
    state = svc_marker_state(uuid, "plain")
    assert state.path == ""
    assert not state.matters
    assert state.concerns() == []


def test_a_marker_we_can_write_is_not_a_concern(tmp_path, testconn, domain, store,
                                                hook_default):
    from vmmanager.core import modes
    from vmmanager.libvirt_service import svc_marker_state, svc_save_mode

    marker = tmp_path / "mode"
    marker.write_text("prod")
    modes.set_hook_script("")  # no reader configured, so no reader check

    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug", marker=str(marker))
    state = svc_marker_state(uuid, "debug")
    assert state.path == str(marker)
    assert state.holds == "prod"
    assert state.writable
    assert state.matters, "it says prod and we are switching to debug"
    assert state.concerns() == []


def test_a_marker_that_already_says_the_right_thing_is_left_alone(
    tmp_path, testconn, domain, store, hook_default
):
    from vmmanager.core import modes
    from vmmanager.libvirt_service import svc_marker_state, svc_save_mode

    marker = tmp_path / "mode"
    marker.write_text("debug")
    modes.set_hook_script("")

    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug", marker=str(marker))
    state = svc_marker_state(uuid, "debug")
    assert state.already_right
    assert not state.matters


def test_a_marker_that_exists_but_is_read_only_is_reported(
    tmp_path, testconn, domain, store, hook_default
):
    """The switch still works; what it cannot do is tell the reader."""
    import os

    from vmmanager.core import modes
    from vmmanager.libvirt_service import svc_marker_state, svc_save_mode

    if os.geteuid() == 0:
        pytest.skip("root can write it, so there is nothing to report")

    marker = tmp_path / "mode"
    marker.write_text("prod")
    marker.chmod(0o444)
    modes.set_hook_script("")

    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug", marker=str(marker))
    state = svc_marker_state(uuid, "debug")
    assert state.holds == "prod", "it is readable, just not writable"
    assert not state.writable
    assert any("needs root" in c for c in state.concerns())


def test_a_marker_in_a_directory_we_cannot_write_is_reported(
    testconn, domain, store, hook_default
):
    """A marker that does not exist yet still needs somewhere to be created."""
    import os

    from vmmanager.core import modes
    from vmmanager.libvirt_service import svc_marker_state, svc_save_mode

    if os.geteuid() == 0:
        pytest.skip("root can create it")

    modes.set_hook_script("")
    uuid = domain.UUIDString()
    svc_save_mode(uuid, "prod", marker="/proc/1/not-a-real-marker")
    state = svc_marker_state(uuid, "prod")
    assert state.holds == "", "it does not exist, so it holds nothing"
    assert not state.writable
    assert any("needs root" in c for c in state.concerns())


def test_a_reader_that_ignores_the_marker_is_reported(tmp_path, testconn, domain,
                                                     store, hook_default):
    from vmmanager.core import modes
    from vmmanager.libvirt_service import svc_marker_state, svc_save_mode

    marker = tmp_path / "which-mode"
    marker.write_text("prod")
    hook = tmp_path / "hook"
    hook.write_text("#!/bin/sh\necho this hook looks at nothing\n")
    modes.set_hook_script(str(hook))

    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug", marker=str(marker))
    state = svc_marker_state(uuid, "debug")
    assert state.reader_uses_it is False
    assert any("does not mention" in c for c in state.concerns())


def test_a_reader_that_uses_the_marker_is_not_reported(tmp_path, testconn, domain,
                                                      store, hook_default):
    from vmmanager.core import modes
    from vmmanager.libvirt_service import svc_marker_state, svc_save_mode

    marker = tmp_path / "which-mode"
    marker.write_text("prod")
    hook = tmp_path / "hook"
    hook.write_text('#!/bin/sh\nmode=$(cat /somewhere/which-mode)\n')
    modes.set_hook_script(str(hook))

    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug", marker=str(marker))
    state = svc_marker_state(uuid, "debug")
    assert state.reader_uses_it is True
    assert state.concerns() == []


def test_a_reader_that_is_not_there_is_reported(tmp_path, testconn, domain, store,
                                               hook_default):
    """Different from one we cannot read: a wrong path is worth pointing out."""
    from vmmanager.core import modes
    from vmmanager.libvirt_service import svc_marker_state, svc_save_mode

    marker = tmp_path / "which-mode"
    marker.write_text("prod")
    modes.set_hook_script(str(tmp_path / "no-such-hook"))

    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug", marker=str(marker))
    state = svc_marker_state(uuid, "debug")
    assert state.reader_missing
    assert any("no " in c and "to read the marker" in c for c in state.concerns())


def test_an_unreadable_reader_keeps_quiet(testconn, domain, store, hook_default):
    """Most hooks are root-only. Guessing would cry wolf on every switch."""
    from vmmanager.core import modes
    from vmmanager.libvirt_service import svc_marker_state, svc_save_mode

    modes.set_hook_script("/proc/1/mem")  # exists, cannot be read by us
    uuid = domain.UUIDString()
    svc_save_mode(uuid, "debug", marker="/tmp/vmmanager-test-marker")
    state = svc_marker_state(uuid, "debug")
    assert state.reader_uses_it is None
    assert not state.reader_missing
    assert not any("does not mention" in c for c in state.concerns())


@pytest.mark.parametrize("value", ["prod; rm -rf /", "a\nb", "", "x" * 65])
def test_an_implausible_mode_name_is_not_passed_to_pkexec(value):
    from vmmanager.libvirt_service import svc_write_marker_elevated

    with pytest.raises(ValueError, match="plausible"):
        svc_write_marker_elevated("/etc/libvirt/hooks/win11-mode", value)


@pytest.mark.parametrize("path", ["relative/path", "/etc/hooks\nmode", "x"])
def test_an_implausible_marker_path_is_not_passed_to_pkexec(path):
    from vmmanager.libvirt_service import svc_write_marker_elevated

    with pytest.raises(ValueError, match="absolute path"):
        svc_write_marker_elevated(path, "prod")
