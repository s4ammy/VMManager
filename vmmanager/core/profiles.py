"""Reusable hardware profiles: the shape of a machine, without its disk.

A template is a disk to clone. A profile is the other half - firmware,
chipset, CPU model and topology, memory, video, TPM, the features you turn
on for a Windows guest - with no storage in it at all. That is the part
people rebuild by hand every time, and the part they get subtly wrong on
the third machine.

Profiles are captured from a machine that already works rather than typed
into a form: "make new machines like this one" is the thing anybody
actually wants, and it cannot drift from a definition that proved itself.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields

from .devices import svc_get_hardware
from .features import svc_get_features

# What a profile carries. Anything not here is either storage (a profile has
# none), identity (name, uuid, MAC - unique per machine), or host-specific
# (a passed-through card's PCI address means nothing on another host).
@dataclass(frozen=True)
class Profile:
    name: str
    firmware: str = "UEFI"
    machine: str = "q35"
    cpu_mode: str = "host-passthrough"
    vcpus: int = 4
    topology: tuple[int, int, int] | None = None
    memory_mb: int = 4096
    max_memory_mb: int = 0        # 0 means the same as memory_mb
    video: str = "virtio"
    video_accel3d: bool = False
    sounds: tuple[str, ...] = ()
    tpm: str = ""                 # "" means no TPM
    tpm_version: str = "2.0"
    rng: str = ""                 # "" means none, else the entropy source
    shared_memory: bool = False
    boot_menu: bool = False
    # Guest features, as GuestFeatures stores them
    hyperv: dict = field(default_factory=dict)
    kvm_hidden: bool = False
    vendor_id: str = ""
    secure_boot: bool = False
    note: str = ""

    def summary(self) -> str:
        """One line, for a list where the name alone says too little."""
        parts = [
            f"{self.vcpus} vcpu", f"{self.memory_mb / 1024:g} GB",
            self.machine, self.firmware, self.cpu_mode,
        ]
        if self.tpm:
            parts.append(f"TPM {self.tpm_version}")
        if self.secure_boot:
            parts.append("secure boot")
        if self.hyperv:
            parts.append(f"{sum(1 for v in self.hyperv.values() if v)} hyper-v")
        return " · ".join(parts)


def profile_from(name: str, hw, features=None, note: str = "") -> Profile:
    """Capture a machine's shape, leaving out everything unique to it."""
    return Profile(
        name=name,
        firmware=hw.firmware or "UEFI",
        machine=hw.machine or "q35",
        cpu_mode=hw.cpu_mode or "host-passthrough",
        vcpus=hw.vcpus or 1,
        topology=tuple(hw.topology) if hw.topology else None,
        memory_mb=hw.memory_mb or 1024,
        max_memory_mb=hw.max_memory_mb or 0,
        video=hw.video or "virtio",
        video_accel3d=bool(hw.video_accel3d),
        sounds=tuple(hw.sounds),
        tpm=hw.tpm,
        tpm_version=hw.tpm_version or "2.0",
        rng=hw.rng,
        shared_memory=bool(hw.shared_memory),
        boot_menu=bool(hw.boot_menu),
        hyperv=dict(features.hyperv) if features is not None else {},
        kvm_hidden=bool(features.kvm_hidden) if features is not None else False,
        vendor_id=features.vendor_id if features is not None else "",
        secure_boot=bool(features.secure_boot) if features is not None else False,
        note=note,
    )


def to_json(profile: Profile) -> str:
    return json.dumps(asdict(profile), sort_keys=True)


def from_json(payload: str) -> Profile:
    """Tolerant of fields this version does not know: a profile written by
    a later build should load with what it understands rather than fail."""
    raw = json.loads(payload)
    known = {f.name for f in fields(Profile)}
    kept = {k: v for k, v in raw.items() if k in known}
    if isinstance(kept.get("topology"), list):
        kept["topology"] = tuple(kept["topology"])
    if isinstance(kept.get("sounds"), list):
        kept["sounds"] = tuple(kept["sounds"])
    return Profile(**kept)


def svc_capture_profile(uuid: str, name: str, note: str = "") -> Profile:
    """Read a machine and make a profile of it."""
    if not name.strip():
        raise ValueError("A profile needs a name")
    features = None
    try:
        features = svc_get_features(uuid)
    except Exception:  # noqa: BLE001 - a profile without them is still useful
        features = None
    return profile_from(name.strip(), svc_get_hardware(uuid), features, note)


def apply_to_spec(profile: Profile, spec):
    """Overlay a profile onto a machine spec from the wizard.

    Returns a new spec: the name, the disk and the install source are the
    wizard's and are never touched, because those are the things a profile
    deliberately does not carry.
    """
    import dataclasses

    changes = {}
    for attr, value in (
        ("firmware", profile.firmware), ("machine", profile.machine),
        ("cpu_mode", profile.cpu_mode), ("vcpus", profile.vcpus),
        ("memory_mb", profile.memory_mb), ("video", profile.video),
        ("tpm", bool(profile.tpm)), ("secure_boot", profile.secure_boot),
    ):
        if hasattr(spec, attr):
            changes[attr] = value
    return dataclasses.replace(spec, **changes) if changes else spec
