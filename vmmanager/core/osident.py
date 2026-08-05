"""Work out which OS a machine runs, for its label and icon.

In order of trust: a user-set override, the <libosinfo:os id="..."/> metadata
in the domain XML (our wizard writes it, so does virt-manager), the guest
agent, then the machine name. Names are a guess, but a good one in practice.

Keys match simple-icons slugs where one exists so oslogos.py can fetch by name.
"""

from __future__ import annotations

import re

# key -> (display name, family). Family picks the fallback glyph.
OS_CATALOG: dict[str, tuple[str, str]] = {
    "archlinux": ("Arch Linux", "linux"),
    "cachyos": ("CachyOS", "linux"),
    "debian": ("Debian", "linux"),
    "ubuntu": ("Ubuntu", "linux"),
    "linuxmint": ("Linux Mint", "linux"),
    "popos": ("Pop!_OS", "linux"),
    "elementary": ("elementary OS", "linux"),
    "zorin": ("Zorin OS", "linux"),
    "fedora": ("Fedora", "linux"),
    "redhat": ("Red Hat Enterprise Linux", "linux"),
    "centos": ("CentOS", "linux"),
    "rockylinux": ("Rocky Linux", "linux"),
    "almalinux": ("AlmaLinux", "linux"),
    "opensuse": ("openSUSE", "linux"),
    "suse": ("SUSE Linux Enterprise", "linux"),
    "manjaro": ("Manjaro", "linux"),
    "endeavouros": ("EndeavourOS", "linux"),
    "garudalinux": ("Garuda Linux", "linux"),
    "gentoo": ("Gentoo", "linux"),
    "nixos": ("NixOS", "linux"),
    "alpinelinux": ("Alpine Linux", "linux"),
    "voidlinux": ("Void Linux", "linux"),
    "kalilinux": ("Kali Linux", "linux"),
    "raspbian": ("Raspberry Pi OS", "linux"),
    "devuan": ("Devuan", "linux"),
    "slackware": ("Slackware", "linux"),
    "mageia": ("Mageia", "linux"),
    "deepin": ("deepin", "linux"),
    "solus": ("Solus", "linux"),
    "qubes": ("Qubes OS", "linux"),
    "freebsd": ("FreeBSD", "bsd"),
    "openbsd": ("OpenBSD", "bsd"),
    "netbsd": ("NetBSD", "bsd"),
    "windows": ("Windows", "windows"),
    "linux": ("Linux", "linux"),
    "unknown": ("Unknown", "generic"),
}

# osinfo and os-release ids that differ from our key
_ALIASES = {
    "win": "windows", "winnt": "windows", "mswindows": "windows",
    "arch": "archlinux", "archlinux": "archlinux",
    "rhel": "redhat", "rhl": "redhat", "sles": "suse", "sled": "suse",
    "opensuse-leap": "opensuse", "opensuse-tumbleweed": "opensuse",
    "rocky": "rockylinux", "almalinux": "almalinux", "alma": "almalinux",
    "alpine": "alpinelinux", "void": "voidlinux", "kali": "kalilinux",
    "mint": "linuxmint", "linuxmint": "linuxmint",
    "pop": "popos", "pop_os": "popos", "pop-os": "popos",
    "elementaryos": "elementary", "elementary": "elementary",
    "zorinos": "zorin", "endeavour": "endeavouros",
    "garuda": "garudalinux", "raspbian": "raspbian", "rpi": "raspbian",
    "cachy": "cachyos", "cachyos": "cachyos",
    "sl": "centos", "centos-stream": "centos",
    "fedora": "fedora", "debian": "debian", "ubuntu": "ubuntu",
    "gentoo": "gentoo", "nixos": "nixos", "manjaro": "manjaro",
    "freebsd": "freebsd", "openbsd": "openbsd", "netbsd": "netbsd",
}


def normalise(value: str) -> str:
    """Map a distro id from any source onto one of our keys."""
    if not value:
        return ""
    token = re.sub(r"[^a-z0-9_-]", "", value.strip().lower())
    if token in OS_CATALOG:
        return token
    if token in _ALIASES:
        return _ALIASES[token]
    # strip a trailing version: ubuntu2404 -> ubuntu, win11 -> windows
    stem = re.sub(r"[\d.]+$", "", token)
    if stem in OS_CATALOG:
        return stem
    return _ALIASES.get(stem, "")


def key_from_osinfo_id(osinfo_id: str) -> str:
    """http://microsoft.com/win/11 -> windows.

    The path is /<distro>/<version>, so the distro is second to last.
    """
    if not osinfo_id:
        return ""
    parts = [p for p in osinfo_id.rstrip("/").split("/") if p]
    for candidate in reversed(parts[:-1] if len(parts) > 1 else parts):
        key = normalise(candidate)
        if key:
            return key
    return ""


def key_from_name(name: str) -> str:
    """Guess from a machine name: "win11-test", "arch-builder"."""
    lowered = name.lower()
    # longest first, so linuxmint beats linux and windows beats win
    for token in sorted(
        set(OS_CATALOG) | set(_ALIASES), key=len, reverse=True
    ):
        if token in lowered:
            key = normalise(token)
            if key:
                return key
    if re.search(r"\bwin(7|8|10|11|xp|dows)?\b", lowered):
        return "windows"
    return ""


def is_custom_icon(key: str) -> bool:
    """A pinned icon can be a file the user chose rather than a catalogue key."""
    return key.startswith("/")


def detect_key(
    override: str = "",
    osinfo_id: str = "",
    agent_distro: str = "",
    name: str = "",
) -> str:
    """Best answer available, or "unknown"."""
    if is_custom_icon(override):
        return override
    for candidate in (
        normalise(override),
        key_from_osinfo_id(osinfo_id),
        normalise(agent_distro),
        key_from_name(name),
    ):
        if candidate:
            return candidate
    return "unknown"


def display_name(key: str) -> str:
    if is_custom_icon(key):
        return key.rsplit("/", 1)[-1]
    return OS_CATALOG.get(key, OS_CATALOG["unknown"])[0]


def family(key: str) -> str:
    if is_custom_icon(key):
        return "generic"
    return OS_CATALOG.get(key, OS_CATALOG["unknown"])[1]
