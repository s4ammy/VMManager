"""OS detection from osinfo ids and machine names."""

from __future__ import annotations

import pytest

from vmmanager.core.osident import (
    OS_CATALOG,
    detect_key,
    display_name,
    family,
    key_from_name,
    key_from_osinfo_id,
    normalise,
)


@pytest.mark.parametrize(
    "osinfo_id,expected",
    [
        # ids libvirt and virt-manager actually write
        ("http://microsoft.com/win/11", "windows"),
        ("http://microsoft.com/win/10", "windows"),
        ("http://microsoft.com/win/7", "windows"),
        ("http://archlinux.org/archlinux/rolling", "archlinux"),
        ("http://ubuntu.com/ubuntu/24.04", "ubuntu"),
        ("http://debian.org/debian/12", "debian"),
        ("http://fedoraproject.org/fedora/42", "fedora"),
        ("http://redhat.com/rhel/9.4", "redhat"),
        ("http://opensuse.org/opensuse/15.6", "opensuse"),
        ("http://freebsd.org/freebsd/14.0", "freebsd"),
        # trailing slash, then one we know nothing about
        ("http://ubuntu.com/ubuntu/22.04/", "ubuntu"),
        ("http://example.com/madeup/1", ""),
        ("", ""),
    ],
)
def test_osinfo_ids(osinfo_id, expected):
    assert key_from_osinfo_id(osinfo_id) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("win11", "windows"),
        ("win7-test", "windows"),
        ("windows-server", "windows"),
        ("arch-builder", "archlinux"),
        ("cachy-desktop", "cachyos"),
        ("my-ubuntu-vm", "ubuntu"),
        ("debian12", "debian"),
        # longest token wins, so these mustn't collapse to "linux"
        ("linuxmint-vm", "linuxmint"),
        ("kali-box", "kalilinux"),
        # nothing to go on
        ("buildbox", ""),
        ("vm1", ""),
    ],
)
def test_name_heuristics(name, expected):
    assert key_from_name(name) == expected


def test_detection_order_prefers_the_override():
    """Override beats osinfo beats agent beats name."""
    assert detect_key(override="gentoo", osinfo_id="http://microsoft.com/win/11",
                      agent_distro="ubuntu", name="arch-vm") == "gentoo"
    assert detect_key(osinfo_id="http://microsoft.com/win/11",
                      agent_distro="ubuntu", name="arch-vm") == "windows"
    assert detect_key(agent_distro="ubuntu", name="arch-vm") == "ubuntu"
    assert detect_key(name="arch-vm") == "archlinux"


def test_unknown_is_the_floor_not_a_guess():
    assert detect_key() == "unknown"
    assert detect_key(name="buildbox") == "unknown"
    assert detect_key(override="not-an-os", name="buildbox") == "unknown"


@pytest.mark.parametrize("alias,expected", [
    ("arch", "archlinux"), ("rhel", "redhat"), ("sles", "suse"),
    ("mint", "linuxmint"), ("pop_os", "popos"), ("cachy", "cachyos"),
    ("rocky", "rockylinux"), ("alpine", "alpinelinux"), ("void", "voidlinux"),
    ("win", "windows"), ("ubuntu2404", "ubuntu"),
])
def test_aliases_and_version_stripping(alias, expected):
    assert normalise(alias) == expected


def test_every_catalogue_key_has_a_name_and_family():
    for key in OS_CATALOG:
        assert display_name(key) and display_name(key) != "Unknown" or key == "unknown"
        assert family(key) in {"linux", "windows", "bsd", "generic"}


def test_unknown_key_falls_back_rather_than_raising():
    assert display_name("no-such-os") == "Unknown"
    assert family("no-such-os") == "generic"


# ---------------------------------------------------------------- custom icons


def test_a_path_is_carried_through_as_the_icon():
    """A picked file is stored where a catalogue key would be; no key looks
    like a path, so the two cannot be confused."""
    from vmmanager.core.osident import detect_key, is_custom_icon

    path = "/home/me/pictures/tux.png"
    assert is_custom_icon(path)
    assert detect_key(override=path, osinfo_id="http://microsoft.com/win/11") == path


def test_a_catalogue_key_is_not_mistaken_for_a_file():
    from vmmanager.core.osident import detect_key, is_custom_icon

    assert not is_custom_icon("debian")
    assert detect_key(override="debian") == "debian"


def test_a_custom_icon_is_named_after_its_file():
    from vmmanager.core.osident import display_name, family

    assert display_name("/home/me/pictures/tux.png") == "tux.png"
    assert family("/home/me/pictures/tux.png") == "generic"


# -- bundled logos
#
# Two logos ship with vmmanager because the alternatives looked wrong: CachyOS
# is not in simple-icons, and the penguin we painted for the Linux family was a
# poor likeness. These check they are found, and that shipping one does not
# elbow past a distro's own icon.


def bundled(tmp_path, monkeypatch, *names):
    """A bundled-logo directory holding a 1x1 PNG for each name given."""
    from PySide6.QtGui import QImage

    from vmmanager.data import oslogos

    folder = tmp_path / "logos"
    folder.mkdir()
    for name in names:
        image = QImage(4, 4, QImage.Format.Format_ARGB32)
        image.fill(0xFF00FF00)
        assert image.save(str(folder / f"{name}.png"))
    monkeypatch.setattr(oslogos, "BUNDLED_DIR", folder)
    oslogos.forget_cached_pixmaps()
    return folder


def test_the_two_logos_we_ship_are_there(qapp):
    """Named for their keys, or nothing finds them."""
    from vmmanager.data.oslogos import bundled_logo, logo_pixmap

    for key in ("cachyos", "linux"):
        path = bundled_logo(key)
        assert path is not None, f"no bundled logo for {key}"
        assert path.is_file()
        icon = logo_pixmap(key, 32)
        assert not icon.isNull()
        assert max(icon.width(), icon.height()) == 32, "should fill the icon box"


def test_a_bundled_logo_is_reported_as_the_source(qapp, tmp_path, monkeypatch):
    from vmmanager.data.oslogos import source_of

    bundled(tmp_path, monkeypatch, "cachyos")
    assert source_of("cachyos") == "bundled"


def test_a_bundled_logo_beats_a_downloaded_one(qapp, tmp_path, monkeypatch):
    """Curated artwork for one OS is there because the silhouette was wrong."""
    from vmmanager.data import oslogos

    bundled(tmp_path, monkeypatch, "debian")
    monkeypatch.setattr(oslogos, "CACHE_DIR", tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    oslogos.cached_svg("debian").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M0 0h24v24H0z"/></svg>'
    )
    assert oslogos.source_of("debian") == "bundled"


def test_a_family_logo_does_not_beat_a_downloaded_distro_icon(qapp, tmp_path,
                                                              monkeypatch):
    """Tux stands in for a Linux with nothing better, not for one with an icon."""
    from vmmanager.data import oslogos

    bundled(tmp_path, monkeypatch, "linux")
    monkeypatch.setattr(oslogos, "CACHE_DIR", tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    oslogos.cached_svg("debian").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path d="M0 0h24v24H0z"/></svg>'
    )
    assert oslogos.source_of("debian") == "simple-icons"


def test_a_family_logo_does_not_beat_the_icon_theme(qapp, tmp_path, monkeypatch):
    """The host's own distributor logo is more specific than a generic Tux."""
    from vmmanager.data import oslogos

    folder = bundled(tmp_path, monkeypatch, "linux")
    monkeypatch.setattr(oslogos, "CACHE_DIR", tmp_path / "empty")
    from_theme = folder / "pretend-distributor-logo.png"
    from_theme.write_bytes((folder / "linux.png").read_bytes())
    monkeypatch.setattr(oslogos, "_theme_logo_path", lambda key: from_theme)
    assert oslogos.source_of("solus") == "icon theme"
    assert oslogos.resolve("solus")[1] == from_theme


def test_a_family_logo_stands_in_where_there_is_nothing_else(qapp, tmp_path,
                                                             monkeypatch):
    from vmmanager.data import oslogos

    bundled(tmp_path, monkeypatch, "linux")
    monkeypatch.setattr(oslogos, "CACHE_DIR", tmp_path / "empty")
    monkeypatch.setattr(oslogos, "_theme_logo_path", lambda key: None)
    assert oslogos.source_of("solus") == "bundled, for any linux"
    assert not oslogos.logo_pixmap("solus", 24).isNull()


def test_windows_is_still_painted(qapp, tmp_path, monkeypatch):
    """No permissive icon set carries Microsoft's marks, so it stays drawn."""
    from vmmanager.data import oslogos

    bundled(tmp_path, monkeypatch, "linux")
    monkeypatch.setattr(oslogos, "CACHE_DIR", tmp_path / "empty")
    monkeypatch.setattr(oslogos, "_theme_logo_path", lambda key: None)
    assert oslogos.source_of("windows") == "drawn"
    assert not oslogos.logo_pixmap("windows", 24).isNull()


def test_a_users_own_file_still_wins(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QImage

    from vmmanager.data import oslogos

    bundled(tmp_path, monkeypatch, "linux", "cachyos")
    mine = tmp_path / "mine.png"
    image = QImage(8, 8, QImage.Format.Format_ARGB32)
    image.fill(0xFFFF0000)
    image.save(str(mine))
    assert oslogos.source_of(str(mine)) == "your file"
    assert not oslogos.logo_pixmap(str(mine), 24).isNull()
