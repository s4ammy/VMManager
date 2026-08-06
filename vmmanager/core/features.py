"""Guest features: Hyper-V enlightenments, hiding, CPU flags, Looking Glass.

The settings that decide whether a Windows guest with a passed-through GPU is
usable, and the ones people copy out of forum posts into raw XML because no
manager exposes them.

What the host supports is read from libvirt's domain capabilities rather than
hardcoded, since the list of enlightenments grows with every QEMU release and a
feature this host has never heard of would just fail to define.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import libvirt

from .connection import _with_conn
from .devices import _APPLIED_CONFIG
from .xmlutil import _editable_xml

EVDEV_DIR = Path("/dev/input/by-id")

# Hyper-V enlightenments in the order they are worth explaining, with what each
# one buys. Anything the host reports but we have no note for still appears.
HYPERV_NOTES = {
    "relaxed": "stops Windows panicking when the host deschedules it",
    "vapic": "faster interrupt handling",
    "spinlocks": "tells Windows to back off rather than spin",
    "vpindex": "virtual processor index, used by the synthetic timers",
    "runtime": "processor run-time accounting",
    "synic": "synthetic interrupt controller, needed by stimer",
    "stimer": "synthetic timers - the big one for Windows idle load",
    "reset": "lets the guest reset itself through Hyper-V",
    "vendor_id": "hides the KVM signature from the guest",
    "frequencies": "exposes TSC and APIC frequencies",
    "reenlightenment": "keeps time sane across a migration",
    "tlbflush": "faster TLB shootdowns on many vCPUs",
    "ipi": "faster inter-processor interrupts",
    "avic": "hardware-accelerated interrupt delivery (AMD)",
    "emsr_bitmap": "avoids some MSR exits",
    "xmm_input": "passes hypercall arguments in registers",
}

# Dependencies libvirt enforces, checked against 12.6 by trying each pairing:
# stimer needs synic and the hypervclock timer. synic alone is fine. Both are
# reported only when defining, so we satisfy the timer rather than fail late.
STIMER_REQUIRES_TIMER = "hypervclock"
STIMER_REQUIRES_FEATURE = "synic"

CPU_FLAG_NOTES = {
    "topoext": "AMD: lets the guest see its core/thread layout properly",
    "invtsc": "invariant TSC - Windows uses it as a clock source",
    "hypervisor": "the flag that says 'you are a VM'; disable to hide",
    "svm": "nested virtualisation on AMD",
    "vmx": "nested virtualisation on Intel",
    "x2apic": "faster APIC access with many vCPUs",
}
CPU_POLICIES = ("require", "disable", "optional", "force", "forbid")


@dataclass(frozen=True)
class FeatureSupport:
    """What this host can actually do, read from domain capabilities."""

    hyperv: tuple[str, ...] = ()
    secure_boot: bool = False
    secure_loader: str = ""
    machine: str = "q35"


@dataclass(frozen=True)
class GuestFeatures:
    hyperv: dict[str, bool] = field(default_factory=dict)
    vendor_id: str = ""
    spinlocks: int = 0  # retries; libvirt requires at least 4095
    kvm_hidden: bool = False
    vmport: bool = True  # libvirt's default is on
    cpu_features: dict[str, str] = field(default_factory=dict)  # name -> policy
    shmem_mb: int = 0  # Looking Glass frame relay; 0 for none
    evdev: tuple[str, ...] = ()
    secure_boot: bool = False

    @property
    def hyperv_on(self) -> tuple[str, ...]:
        return tuple(name for name, on in sorted(self.hyperv.items()) if on)


def svc_feature_support() -> FeatureSupport:
    """Ask libvirt what this host offers, for a q35 KVM guest."""

    def go(conn):
        try:
            caps = ET.fromstring(
                conn.getDomainCapabilities(None, "x86_64", "q35", "kvm")
            )
        except libvirt.libvirtError:
            return FeatureSupport()
        hyperv = tuple(
            value.text for value in caps.findall(
                "features/hyperv/enum[@name='features']/value"
            ) if value.text
        )
        loader = caps.find("os/loader")
        secure = False
        secure_loader = ""
        if loader is not None:
            secure = "yes" in [
                v.text for v in loader.findall("enum[@name='secure']/value")
            ]
            for value in loader.findall("value"):
                if value.text and "secboot" in value.text:
                    secure_loader = value.text
                    break
        return FeatureSupport(
            hyperv=hyperv, secure_boot=secure and bool(secure_loader),
            secure_loader=secure_loader,
        )

    return _with_conn(go)


def svc_list_evdev() -> list[tuple[str, str]]:
    """(path, label) for host input devices worth passing to a guest.

    Only the event nodes: the mouse/js aliases point at the same hardware and
    handing both to a guest gets you doubled input.
    """
    if not EVDEV_DIR.is_dir():
        return []
    found = []
    for entry in sorted(EVDEV_DIR.iterdir()):
        if "-event-" not in entry.name and not entry.name.endswith("-event"):
            continue
        label = entry.name.replace("usb-", "").replace("-event", " ")
        label = label.replace("-", " ").replace("_", " ").strip()
        found.append((str(entry), label))
    return found


def svc_get_features(uuid: str) -> GuestFeatures:
    def go(conn):
        root = ET.fromstring(
            conn.lookupByUUIDString(uuid).XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
        )
        hyperv: dict[str, bool] = {}
        vendor_id = ""
        spinlocks = 0
        node = root.find("features/hyperv")
        if node is not None:
            for child in node:
                hyperv[child.tag] = child.get("state", "on") == "on"
                if child.tag == "vendor_id":
                    vendor_id = child.get("value", "")
                if child.tag == "spinlocks":
                    try:
                        spinlocks = int(child.get("retries", "0"))
                    except ValueError:
                        spinlocks = 0
        vmport = root.find("features/vmport")
        loader = root.find("os/loader")
        shmem = root.find("devices/shmem")
        shmem_mb = 0
        if shmem is not None:
            size = shmem.find("size")
            if size is not None and (size.text or "").strip().isdigit():
                shmem_mb = int(size.text.strip())
        return GuestFeatures(
            hyperv=hyperv,
            vendor_id=vendor_id,
            spinlocks=spinlocks,
            kvm_hidden=root.find("features/kvm/hidden") is not None,
            vmport=vmport is None or vmport.get("state", "on") == "on",
            cpu_features={
                f.get("name", "?"): f.get("policy", "require")
                for f in root.findall("cpu/feature")
            },
            shmem_mb=shmem_mb,
            evdev=tuple(
                source.get("dev", "")
                for source in root.findall("devices/input[@type='evdev']/source")
                if source.get("dev")
            ),
            secure_boot=loader is not None and loader.get("secure") == "yes",
        )

    return _with_conn(go)


def _set_child(parent: ET.Element, tag: str, attrs: dict) -> None:
    for existing in parent.findall(tag):
        parent.remove(existing)
    ET.SubElement(parent, tag, attrs)


def svc_set_features(uuid: str, wanted: GuestFeatures,
                     support: FeatureSupport | None = None) -> str:
    """Apply the whole set in one definition, so it cannot half-succeed."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = _editable_xml(dom)
        features = root.find("features")
        if features is None:
            features = ET.SubElement(root, "features")

        # -- hyper-V
        for existing in features.findall("hyperv"):
            features.remove(existing)
        enabled = {name for name, on in wanted.hyperv.items() if on}
        if enabled:
            hyperv = ET.SubElement(features, "hyperv", {"mode": "custom"})
            for name in sorted(enabled):
                attrs = {"state": "on"}
                if name == "vendor_id":
                    if not wanted.vendor_id:
                        continue  # no value, no point declaring it
                    attrs["value"] = wanted.vendor_id[:12]
                if name == "spinlocks":
                    attrs["retries"] = str(max(wanted.spinlocks, 4095))
                ET.SubElement(hyperv, name, attrs)
            # libvirt refuses stimer without this timer, and reports it only
            # when defining, so add it rather than let the save fail
            if "stimer" in enabled:
                clock = root.find("clock")
                if clock is None:
                    clock = ET.SubElement(root, "clock", {"offset": "utc"})
                if not clock.findall(f"timer[@name='{STIMER_REQUIRES_TIMER}']"):
                    ET.SubElement(clock, "timer", {
                        "name": STIMER_REQUIRES_TIMER, "present": "yes",
                    })

        # -- hiding
        for existing in features.findall("kvm"):
            features.remove(existing)
        if wanted.kvm_hidden:
            _set_child(ET.SubElement(features, "kvm"), "hidden", {"state": "on"})
        for existing in features.findall("vmport"):
            features.remove(existing)
        if not wanted.vmport:
            ET.SubElement(features, "vmport", {"state": "off"})

        # -- cpu flags
        cpu = root.find("cpu")
        if cpu is None and wanted.cpu_features:
            cpu = ET.SubElement(root, "cpu", {"mode": "host-passthrough"})
        if cpu is not None:
            for existing in cpu.findall("feature"):
                cpu.remove(existing)
            for name, policy in sorted(wanted.cpu_features.items()):
                ET.SubElement(cpu, "feature", {
                    "policy": policy if policy in CPU_POLICIES else "require",
                    "name": name,
                })

        devices = root.find("devices")
        if devices is None:
            devices = ET.SubElement(root, "devices")

        # -- Looking Glass frame relay
        for existing in devices.findall("shmem"):
            if existing.get("name") == "looking-glass":
                devices.remove(existing)
        if wanted.shmem_mb > 0:
            shmem = ET.SubElement(devices, "shmem", {"name": "looking-glass"})
            ET.SubElement(shmem, "model", {"type": "ivshmem-plain"})
            size = ET.SubElement(shmem, "size", {"unit": "M"})
            size.text = str(wanted.shmem_mb)

        # -- evdev input passthrough
        for existing in devices.findall("input[@type='evdev']"):
            devices.remove(existing)
        for index, path in enumerate(wanted.evdev):
            node = ET.SubElement(devices, "input", {"type": "evdev"})
            attrs = {"dev": path}
            if index == 0:
                # the first device carries the hotkey that releases the grab
                attrs["grab"] = "all"
                attrs["repeat"] = "on"
            ET.SubElement(node, "source", attrs)

        # -- secure boot
        if support is not None and wanted.secure_boot and support.secure_loader:
            os_node = root.find("os")
            if os_node is not None:
                for existing in os_node.findall("loader"):
                    os_node.remove(existing)
                loader = ET.Element("loader", {
                    "readonly": "yes", "secure": "yes", "type": "pflash",
                })
                loader.text = support.secure_loader
                os_node.insert(0, loader)
                # secure boot needs SMM, and libvirt will not infer it
                _set_child(features, "smm", {"state": "on"})
        elif not wanted.secure_boot:
            loader = root.find("os/loader")
            if loader is not None and loader.get("secure") == "yes":
                loader.set("secure", "no")

        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)


def looking_glass_hint(shmem_mb: int, guest: str = "win11") -> str:
    """What still has to happen outside libvirt for Looking Glass to work."""
    if shmem_mb <= 0:
        return ""
    return (
        "/dev/shm/looking-glass has to exist and be writable by the user QEMU "
        "runs as, or the machine will not start. The guest also needs the "
        "Looking Glass host application installed."
    )


def shmem_for_resolution(width: int, height: int) -> int:
    """Smallest power-of-two MiB that fits Looking Glass's frame buffers.

    Its formula is width x height x 4 bytes x 2 frames, plus 10 MiB of headroom,
    rounded up to a power of two because ivshmem wants one.
    """
    needed = (width * height * 4 * 2) + 10 * 1024 * 1024
    size = 32
    while size * 1024 * 1024 < needed:
        size *= 2
    return size
