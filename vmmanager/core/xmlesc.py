"""XML escaping for values we splice into libvirt XML."""

from __future__ import annotations

from xml.sax.saxutils import escape

_QUOTES = {'"': "&quot;", "'": "&apos;"}


def x(value: object) -> str:
    """Escape a leaf value. Safe in both element text and attributes.

    Never pass an XML fragment through this; it would escape the markup.
    """
    if value is None:
        return ""
    return escape(str(value), _QUOTES)


# libvirt accepts escaped input, stores the raw text, then writes some
# attributes back out *unescaped*. A network with domain name "lab.R&D" is
# returned as <domain name='lab.R&D'/>, which won't parse. Confirmed on the
# qemu driver, not just test:///. We can't fix the round trip, so refuse the
# characters instead; none of them are legal in a DNS name anyway.
_UNSAFE_IN_NAMES = set("<>&\"'")


def check_name(value: str, what: str) -> str:
    """Reject markup characters in a name libvirt echoes back to us."""
    bad = sorted(_UNSAFE_IN_NAMES & set(value))
    if bad:
        raise ValueError(
            f"{what} cannot contain {' or '.join(bad)} - libvirt doesn't quote "
            f"these when reporting the definition back, leaving it unreadable."
        )
    return value
