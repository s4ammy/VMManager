"""Named configurations for a machine, and switching between them.

A machine with a GPU passed through usually needs two different setups, and
switching between them by hand is a dozen careful edits each way.

One setup hands the graphics card, its audio function and a USB controller to
the guest and gives it no console: the monitors switch over to it, and the
desktop goes away until it shuts down. The other leaves the card alone and
gives the guest a plain VGA device and a SPICE console, so you can watch it
boot in a window. The first is how you use the machine; the second is how you
work out why it will not start.

A mode is the whole persistent definition saved under a name. Switching means
defining it again, which is atomic in libvirt: it either takes or it does not.
The safety comes from the checks around it - the machine must be off, the XML
must validate, the identity must match, and what was there before is kept.

Because a mode is the whole definition rather than a set of edits, switching
reverts anything changed since that mode was saved. Save the mode again after
changing something you want to keep.

A mode may also name a *marker*: a file to write its own name into, for
something outside libvirt that has to know which mode is in use. The usual
reader is a libvirt hook - which is exactly the case where getting it wrong
matters, because the hook is what decides whether your desktop keeps its
graphics card. Markers usually belong to root, and this process is not root, so
the state of one is checked before a switch and again after: a definition that
says one thing while the marker says another is worse than either alone.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import libvirt

from ..logs import log
from .connection import _with_conn

AUTOSAVE_NAME = "before last switch"

# A mode may name a file to write its own name into: a marker, for something
# outside libvirt that has to know which mode is in use. The usual reader is a
# libvirt hook, which is where this default points, but nothing here assumes
# that - the path is per-mode and the reader is whatever you set below.
DEFAULT_HOOK_SCRIPT = "/etc/libvirt/hooks/qemu"
_hook_script = DEFAULT_HOOK_SCRIPT

# What a marker may be called and hold. Both end up on a pkexec command line.
SAFE_VALUE = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")


def hook_script() -> str:
    """The script we check for marker awareness. Empty means do not check."""
    return _hook_script


def set_hook_script(path: str) -> None:
    global _hook_script
    _hook_script = path.strip()

# Prefixes libvirt uses. Without registering them ElementTree invents ns0, ns1
# and so on, which is valid XML and unreadable in a diff.
NAMESPACES = {
    "libosinfo": "http://libosinfo.org/xmlns/libvirt/domain/1.0",
    "qemu": "http://libvirt.org/schemas/domain/qemu/1.0",
    "vmm": "http://vmmanager/xmlns/1.0",
}
for _prefix, _uri in NAMESPACES.items():
    ET.register_namespace(_prefix, _uri)


def canonical(xml: str) -> str:
    """Re-indent consistently so a diff shows changes, not formatting.

    A definition that has been through ElementTree is spaced differently from
    one straight out of libvirt, and comparing the two produces a diff where
    every line has moved. Both sides go through this first.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return xml
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode")


@dataclass(frozen=True)
class Mode:
    name: str
    note: str
    marker: str  # optional file a libvirt hook reads to know the mode
    created: int
    active: bool = False
    matches: bool = True  # the definition still looks like this mode


def _store():
    """The stats database, resolved now rather than at import.

    StatsStore takes its path as a default argument, so binding it here would
    make the location impossible to redirect.
    """
    from ..data import history

    return history.StatsStore(history.DB_PATH)


def _domain_xml(conn, uuid: str) -> str:
    return conn.lookupByUUIDString(uuid).XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)


def _identity(xml: str) -> tuple[str, str]:
    root = ET.fromstring(xml)
    return (root.findtext("uuid") or "", root.findtext("name") or "")


def _normalise(xml: str) -> str:
    """Compare definitions without tripping over libvirt's own additions.

    libvirt fills in aliases, addresses and a seclabel when it defines a
    domain, so the XML you hand it is never quite the XML you get back.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return xml
    for tag in ("seclabel", "alias"):
        for parent in root.iter():
            for child in list(parent):
                if child.tag == tag:
                    parent.remove(child)
    return ET.tostring(root, encoding="unicode")


def svc_list_modes(uuid: str) -> list[Mode]:
    """Saved modes, with which one the definition currently matches."""

    def go(conn):
        store = _store()
        try:
            current = _normalise(_domain_xml(conn, uuid))
            active = store.active_mode(uuid)
            out = []
            for name, note, marker, created in store.modes(uuid):
                saved = store.mode_xml(uuid, name) or ""
                out.append(Mode(
                    name=name, note=note, marker=marker, created=created,
                    active=name == active,
                    matches=_normalise(saved) == current,
                ))
            return out
        finally:
            store.close()

    return _with_conn(go)


def svc_save_mode(uuid: str, name: str, note: str = "", marker: str = "") -> str:
    """Capture the machine as it is now, under this name."""
    name = name.strip()
    if not name:
        raise ValueError("A mode needs a name.")

    def go(conn):
        xml = _domain_xml(conn, uuid)
        store = _store()
        try:
            store.save_mode(uuid, name, xml, note.strip(), marker.strip())
            store.set_active_mode(uuid, name)
        finally:
            store.close()
        return f"Saved this configuration as '{name}'."

    return _with_conn(go)


def svc_delete_mode(uuid: str, name: str) -> str:
    store = _store()
    try:
        store.delete_mode(uuid, name)
    finally:
        store.close()
    return f"Deleted mode '{name}'."


def svc_definition_diff(uuid: str, proposed_xml: str) -> str:
    """Unified diff from the current persistent definition to a proposed one.

    Empty when nothing would change. Both sides are re-indented first so the
    diff shows edits, not formatting.
    """

    def go(conn):
        current = _domain_xml(conn, uuid)
        lines = difflib.unified_diff(
            canonical(current).splitlines(),
            canonical(proposed_xml).splitlines(),
            fromfile="current", tofile="proposed", lineterm="",
        )
        return "\n".join(lines)

    return _with_conn(go)

def svc_mode_diff(uuid: str, name: str) -> str:
    """Unified diff from the current definition to the saved mode."""

    def go(conn):
        store = _store()
        try:
            saved = store.mode_xml(uuid, name)
        finally:
            store.close()
        if saved is None:
            raise RuntimeError(f"No mode named '{name}'.")
        current = _domain_xml(conn, uuid)
        lines = difflib.unified_diff(
            canonical(current).splitlines(), canonical(saved).splitlines(),
            fromfile="current", tofile=name, lineterm="",
        )
        return "\n".join(lines) or "The definition already matches this mode."

    return _with_conn(go)


def validate_xml(xml: str) -> str | None:
    """Run libvirt's own schema check, if it is installed. None means fine."""
    tool = shutil.which("virt-xml-validate")
    if tool is None:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as handle:
        handle.write(xml)
        path = handle.name
    try:
        result = subprocess.run(
            [tool, path], capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return (result.stderr or result.stdout).strip()[:400]
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("virt-xml-validate unavailable: %s", exc)
        return None
    finally:
        Path(path).unlink(missing_ok=True)


def svc_switch_mode(uuid: str, name: str) -> str:
    """Define the saved mode, keeping what was there as a way back."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        if dom.isActive():
            raise RuntimeError(
                f"{dom.name()} is running. A mode changes the definition, "
                "which only takes effect on the next start, so shut it down "
                "first rather than being surprised later."
            )
        store = _store()
        try:
            saved = store.mode_xml(uuid, name)
            if saved is None:
                raise RuntimeError(f"No mode named '{name}'.")

            # A mode saved from a different machine would redefine this one out
            # of existence, so refuse rather than trust the name.
            saved_uuid, saved_name = _identity(saved)
            if saved_uuid and saved_uuid != uuid:
                raise RuntimeError(
                    "That mode was saved from a different machine "
                    f"({saved_name or saved_uuid}), so applying it here would "
                    "replace this one. Refusing."
                )

            problem = validate_xml(saved)
            if problem:
                raise RuntimeError(f"The saved definition no longer validates:\n{problem}")

            before = _domain_xml(conn, uuid)
            store.save_mode(uuid, AUTOSAVE_NAME, before,
                            note=f"automatic, taken before switching to {name}")
            conn.defineXML(saved)
            store.set_active_mode(uuid, name)
            marker = next(
                (m for n, _note, m, _c in store.modes(uuid) if n == name), ""
            )
        finally:
            store.close()

        message = f"Now in '{name}'."
        if marker:
            message += " " + _write_marker(marker, name)
        return message

    return _with_conn(go)


@dataclass(frozen=True)
class MarkerState:
    """What we know about a mode's marker, before switching to it.

    A mode with no marker is the common case and has nothing to report. When
    there is one, the questions are the same whatever reads it: can we write it,
    does it already say the right thing, and does the reader look at it at all.
    """

    path: str = ""
    holds: str = ""          # what it says now; empty if absent or unreadable
    writable: bool = False   # without asking for a password
    already_right: bool = False
    reader: str = ""         # the script we checked, empty if none configured
    reader_uses_it: bool | None = None  # None: could not read the script
    reader_missing: bool = False  # the configured script is not there at all

    @property
    def matters(self) -> bool:
        return bool(self.path) and not self.already_right

    def concerns(self) -> list[str]:
        """What to tell someone before they commit to the switch."""
        if not self.matters:
            return []
        out = []
        if not self.writable:
            out.append(
                f"{self.path} needs root, so this cannot update it. Whatever "
                f"reads that file will go on seeing "
                f"{self.holds or 'whatever is in it'} until it is changed."
            )
        if self.reader_uses_it is False:
            out.append(
                f"{self.reader} does not mention {self.path}, so it looks like "
                "nothing is reading the marker. Setting it may have no effect."
            )
        if self.reader_missing:
            out.append(
                f"There is no {self.reader} to read the marker. Point Settings "
                "at the right script, or clear it to stop checking."
            )
        return out


def svc_marker_state(uuid: str, name: str) -> MarkerState:
    """Check a mode's marker without changing anything."""
    store = _store()
    try:
        marker = next(
            (m for n, _note, m, _c in store.modes(uuid) if n == name), ""
        )
    finally:
        store.close()
    if not marker:
        return MarkerState()

    target = Path(marker)
    holds = ""
    try:
        holds = target.read_text().strip()
    except OSError:
        pass

    if target.exists():
        writable = os.access(target, os.W_OK)
    else:
        parent = target.parent
        writable = parent.is_dir() and os.access(parent, os.W_OK)

    reader = hook_script()
    uses_it: bool | None = None
    reader_missing = False
    if reader:
        script = Path(reader)
        if not script.exists():
            reader_missing = True
        else:
            try:
                body = script.read_text()
            except OSError:
                uses_it = None  # root-only; saying nothing beats guessing
            else:
                uses_it = target.name in body or marker in body

    return MarkerState(
        path=marker, holds=holds, writable=writable,
        already_right=holds == name, reader=reader, reader_uses_it=uses_it,
        reader_missing=reader_missing,
    )


def svc_write_marker_elevated(path: str, value: str) -> str:
    """Write a marker as root, asking for a password through polkit.

    Both arguments reach a command line, so both are checked against a pattern
    first rather than trusted for having come from our own store.
    """
    if not SAFE_VALUE.match(value):
        raise ValueError(f"{value!r} is not a plausible mode name.")
    target = Path(path)
    if not target.is_absolute() or any(c in path for c in "\n\r"):
        raise ValueError(f"{path!r} is not a plain absolute path.")

    pkexec = shutil.which("pkexec")
    tee = shutil.which("tee")
    if pkexec is None or tee is None:
        raise RuntimeError(
            "pkexec is not installed, so this cannot ask for a password. Run "
            f"this instead:\n\necho -n {value} | sudo tee {path}"
        )
    result = subprocess.run(
        [pkexec, tee, str(target)], input=value.encode(),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(detail or "The password prompt was dismissed.")
    return f"{target} now says '{value}'."


def _write_marker(path: str, name: str) -> str:
    """Update the file a libvirt hook reads, if we are allowed to.

    Hooks run as root and usually read their marker from somewhere under
    /etc, which this process cannot write. Say so plainly rather than
    reporting success and leaving the hook on the old mode.
    """
    target = Path(path)
    try:
        target.write_text(name)
        return f"Marker {target} updated."
    except PermissionError:
        return (
            f"Could not write {target} - it needs root. The hook will still "
            f"see the old mode until you run: echo -n {name} | sudo tee {target}"
        )
    except OSError as exc:
        return f"Could not write {target}: {exc}"
