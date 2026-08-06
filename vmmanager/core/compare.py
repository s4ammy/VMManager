"""Two machines, side by side.

The question this answers is "why does that one work and this one not".
Both definitions are in front of you already, one tab at a time, and
holding a hundred lines of XML in your head while you click between them
is the part that does not work.

Two views of the same comparison: a diff of the whole definitions, for
when you want everything; and a short list of the properties people
actually differ on, for when you want the answer.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

import libvirt

from .connection import _with_conn
from .devices import svc_get_hardware
from .modes import canonical


@dataclass(frozen=True)
class Difference:
    """One property, and what each machine has."""

    label: str
    left: str
    right: str

    @property
    def same(self) -> bool:
        return self.left == self.right


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) or "none"
    return str(value) if value not in (None, "") else "-"


def compare_hardware(left, right) -> list[Difference]:
    """The properties worth lining up, in the order they are worth reading.

    Deliberately not every field: a comparison that lists forty rows so
    that three of them can differ is a diff with extra steps. These are the
    ones that explain a machine behaving differently from its neighbour.
    """
    rows: list[tuple[str, object, object]] = [
        ("firmware", left.firmware, right.firmware),
        ("machine", left.machine, right.machine),
        ("cpu model", left.cpu_mode, right.cpu_mode),
        ("vcpus", left.vcpus, right.vcpus),
        ("topology", left.topology or "-", right.topology or "-"),
        ("memory", f"{left.memory_mb} MiB", f"{right.memory_mb} MiB"),
        ("maximum memory", f"{left.max_memory_mb} MiB",
         f"{right.max_memory_mb} MiB"),
        ("shared memory", left.shared_memory, right.shared_memory),
        ("boot order", left.boot, right.boot),
        ("boot menu", left.boot_menu, right.boot_menu),
        ("video", left.video, right.video),
        ("3d acceleration", left.video_accel3d, right.video_accel3d),
        ("displays", [f"{g.type}:{g.ident}" for g in left.graphics],
         [f"{g.type}:{g.ident}" for g in right.graphics]),
        ("disks", [f"{d.dev} {d.bus} {d.format}" for d in left.disks],
         [f"{d.dev} {d.bus} {d.format}" for d in right.disks]),
        ("disk cache", [f"{d.dev}={d.cache}" for d in left.disks],
         [f"{d.dev}={d.cache}" for d in right.disks]),
        ("network", [f"{n.source or 'direct'} {n.model}" for n in left.nics],
         [f"{n.source or 'direct'} {n.model}" for n in right.nics]),
        ("passed-through", [h.ident for h in left.hostdevs],
         [h.ident for h in right.hostdevs]),
        ("shared folders", [f.tag for f in left.filesystems],
         [f.tag for f in right.filesystems]),
        ("tpm", f"{left.tpm} {left.tpm_version}".strip(),
         f"{right.tpm} {right.tpm_version}".strip()),
        ("random source", left.rng, right.rng),
        ("watchdog", left.watchdog or "-", right.watchdog or "-"),
        ("sound", left.sounds, right.sounds),
        ("audio backend", left.audio, right.audio),
        ("controllers", [f"{t}{i}:{m}" for t, i, m in left.controllers],
         [f"{t}{i}:{m}" for t, i, m in right.controllers]),
    ]
    return [Difference(label, _fmt(a), _fmt(b)) for label, a, b in rows]


def svc_compare_machines(left_uuid: str, right_uuid: str):
    """Both machines' hardware, lined up. Returns (names, differences)."""
    if left_uuid == right_uuid:
        raise ValueError("Pick two different machines")
    left = svc_get_hardware(left_uuid)
    right = svc_get_hardware(right_uuid)

    def names(conn):
        return (
            conn.lookupByUUIDString(left_uuid).name(),
            conn.lookupByUUIDString(right_uuid).name(),
        )

    return _with_conn(names), compare_hardware(left, right)


def svc_compare_definitions(left_uuid: str, right_uuid: str) -> str:
    """A unified diff of the two definitions, for when the summary is not
    enough. Both sides are re-indented first so it shows edits rather than
    formatting, and identity-only elements are left in - a differing uuid
    at the top is a useful reminder of which side is which."""
    def go(conn):
        left = conn.lookupByUUIDString(left_uuid)
        right = conn.lookupByUUIDString(right_uuid)
        flags = (
            libvirt.VIR_DOMAIN_XML_INACTIVE | libvirt.VIR_DOMAIN_XML_SECURE
        )
        return "\n".join(difflib.unified_diff(
            canonical(left.XMLDesc(flags)).splitlines(),
            canonical(right.XMLDesc(flags)).splitlines(),
            fromfile=left.name(), tofile=right.name(), lineterm="",
        ))

    return _with_conn(go)
