"""The Windows answer file: the counterpart to a cloud-init seed.

Windows Setup reads autounattend.xml from any attached volume. It is long,
namespaced and order-sensitive, and the part that decides whether an install
works at all is the driver path - without it Setup reaches "where do you want
to install Windows?" with an empty disk list.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from vmmanager.core.unattend import (
    CANDIDATE_DRIVES,
    Unattend,
    build_autounattend,
    driver_paths,
)

NS = {"u": "urn:schemas-microsoft-com:unattend"}


def _parse(spec: Unattend) -> ET.Element:
    return ET.fromstring(build_autounattend(spec))


def test_it_is_the_three_passes_setup_expects():
    root = _parse(Unattend(user="admin"))
    assert [s.get("pass") for s in root.findall("u:settings", NS)] == [
        "windowsPE", "specialize", "oobeSystem",
    ]


def test_the_storage_driver_is_offered_on_every_letter_the_disc_might_get():
    """Which letter WinPE gives the driver disc depends on how many volumes
    are attached; Setup ignores a path that is not there, so listing them
    all beats guessing one."""
    root = _parse(Unattend(user="admin", windows_version="w11"))
    paths = [
        p.text for p in
        root.findall(".//u:DriverPaths/u:PathAndCredentials/u:Path", NS)
    ]
    assert len(paths) == len(CANDIDATE_DRIVES) * len(driver_paths("w11"))
    assert all("\\" in p and "/" not in p for p in paths), "Windows wants backslashes"
    assert any(p.startswith("E:") for p in paths)
    # the disk driver is the one that decides whether the install works
    assert any("viostor" in p for p in paths)
    assert any("vioscsi" in p for p in paths)


def test_the_driver_folder_follows_the_windows_version():
    assert all("w10" in p for p in driver_paths("w10"))
    assert all("w11" in p for p in driver_paths("w11"))
    # something unknown still produces a usable file rather than failing
    assert driver_paths("some-future-windows") == driver_paths("w11")


def test_the_account_is_created_as_an_administrator():
    root = _parse(Unattend(user="sam", password="hunter2"))
    account = root.find(".//u:LocalAccounts/u:LocalAccount", NS)
    assert account.findtext("u:Name", namespaces=NS) == "sam"
    assert account.findtext("u:Group", namespaces=NS) == "Administrators"
    assert account.findtext("u:Password/u:Value", namespaces=NS) == "hunter2"


def test_no_password_leaves_the_element_out_rather_than_empty():
    """Setup rejects an empty <Password>, which is not the same as none."""
    root = _parse(Unattend(user="sam", password=""))
    assert root.find(".//u:LocalAccount/u:Password", NS) is None
    # and with no password there is nothing to log in with automatically
    assert root.find(".//u:AutoLogon", NS) is None


def test_windows_11_is_told_not_to_demand_an_account():
    root = _parse(Unattend(user="sam", skip_oobe=True))
    assert root.find(".//u:OOBE/u:HideOnlineAccountScreens", NS) is not None
    commands = [c.text for c in root.findall(".//u:RunSynchronousCommand/u:Path", NS)]
    assert any("BypassNRO" in c for c in commands)

    plain = _parse(Unattend(user="sam", skip_oobe=False))
    assert plain.find(".//u:OOBE", NS) is None


def test_a_hostname_is_cut_to_what_windows_takes():
    root = _parse(Unattend(user="admin", hostname="a-very-long-machine-name"))
    name = root.findtext(".//u:ComputerName", namespaces=NS)
    assert len(name) <= 15, "NetBIOS names are 15 characters"


def test_text_that_would_break_the_xml_is_escaped():
    root = _parse(Unattend(user="R&D", password="a<b>c", hostname="x&y"))
    account = root.find(".//u:LocalAccounts/u:LocalAccount", NS)
    assert account.findtext("u:Name", namespaces=NS) == "R&D"
    assert account.findtext("u:Password/u:Value", namespaces=NS) == "a<b>c"


def test_it_refuses_to_build_without_a_user():
    with pytest.raises(ValueError, match="user"):
        build_autounattend(Unattend(user="  "))


# -- the wizard side


def test_the_wizard_only_offers_it_for_an_iso_install(qapp):
    """An answer file is read by Windows Setup, so it means nothing for an
    imported image, a template clone, or a network install that runs the
    distribution's own installer."""
    from vmmanager.wizard import NewVmDialog

    dialog = NewVmDialog(None, ["default"], [], host_cpus=16,
                         host_mem_mb=65536)
    dialog.src_iso.setChecked(True)
    dialog._source_changed(True)
    assert dialog.unattend.isEnabled()

    dialog.src_import.setChecked(True)
    dialog._source_changed(True)
    assert not dialog.unattend.isEnabled()
    assert not dialog.unattend.isChecked()

    dialog.src_url.setChecked(True)
    dialog._source_changed(True)
    assert not dialog.unattend.isEnabled()


def test_the_wizard_puts_the_answers_into_the_spec(qapp):
    from vmmanager.wizard import NewVmDialog

    dialog = NewVmDialog(None, ["default"], [], host_cpus=16,
                         host_mem_mb=65536)
    dialog.name.setText("builder")
    dialog.src_iso.setChecked(True)
    dialog._source_changed(True)
    dialog.unattend.setChecked(True)
    dialog.ua_user.setText("sam")
    dialog.ua_password.setText("hunter2")
    dialog.ua_edition.setCurrentText("Windows 11 Pro")

    spec = dialog.spec()
    assert spec.unattend is not None
    assert spec.unattend.user == "sam"
    assert spec.unattend.edition == "Windows 11 Pro"
    assert spec.unattend.hostname == "builder"
    # and it builds into a file Setup can read
    root = ET.fromstring(build_autounattend(spec.unattend))
    assert root.findtext(".//u:LocalAccount/u:Name", namespaces=NS) == "sam"


def test_it_is_off_unless_asked_for(qapp):
    from vmmanager.wizard import NewVmDialog

    dialog = NewVmDialog(None, ["default"], [], host_cpus=16,
                         host_mem_mb=65536)
    dialog.name.setText("plain")
    assert dialog.spec().unattend is None
