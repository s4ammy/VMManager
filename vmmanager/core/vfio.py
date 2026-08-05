"""Binding devices to vfio-pci, making it stick, and video BIOS ROMs.

The passthrough diagnostics say why a device will not work. This is the
other half: doing something about it.

Three things people end up doing by hand:

- **Bind now.** Take the card off its host driver and give it to vfio-pci
  without rebooting. Works when nothing is using the card.
- **Bind at boot.** A GPU the host driver claims first is often impossible
  to take back, so the usual answer is to claim it earlier - a modprobe.d
  options line, or vfio-pci.ids on the kernel command line.
- **The ROM.** Some cards (consumer NVIDIA especially) will not initialise
  in a guest unless it is handed a copy of their video BIOS, and the dump
  has to start at the legacy x86 image.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from .elevate import PCI_ID, check_address, install_root_file, run_root_script

MODPROBE_CONF = "/etc/modprobe.d/vmmanager-vfio.conf"

@dataclass(frozen=True)
class DeviceIds:
    """What a PCI address is, in the terms boot-time binding uses."""

    address: str
    vendor: str  # "10de"
    device: str  # "2705"
    driver: str  # bound now, "" for none
    class_code: str  # "0x030000"

    @property
    def ident(self) -> str:
        return f"{self.vendor}:{self.device}"

    @property
    def is_display(self) -> bool:
        return self.class_code.startswith("0x03")

    @property
    def is_audio(self) -> bool:
        return self.class_code.startswith("0x0403")

def read_device_ids(address: str, root: str = "/sys") -> DeviceIds:
    """Vendor, device, driver and class from sysfs for one address."""
    address = check_address(address)
    base = os.path.join(root, "bus/pci/devices", address)

    def field(name: str) -> str:
        try:
            with open(os.path.join(base, name)) as f:
                return f.read().strip()
        except OSError:
            return ""

    driver = ""
    link = os.path.join(base, "driver")
    if os.path.islink(link):
        driver = os.path.basename(os.readlink(link))
    return DeviceIds(
        address=address,
        vendor=field("vendor").removeprefix("0x"),
        device=field("device").removeprefix("0x"),
        driver=driver,
        class_code=field("class"),
    )

def function_siblings(address: str, root: str = "/sys") -> list[str]:
    """Every function of the same physical card, this one included.

    A GPU is a graphics function plus an audio one (and sometimes USB and
    a serial bus on newer cards); they share an IOMMU group and have to
    move together, which is the thing people miss.
    """
    address = check_address(address)
    prefix = address.rsplit(".", 1)[0]
    base = os.path.join(root, "bus/pci/devices")
    try:
        entries = os.listdir(base)
    except OSError:
        return [address]
    return sorted(e for e in entries if e.startswith(prefix + "."))

# -- binding now

BIND_SCRIPT = """
set -e
for addr in "$@"; do
    dev="/sys/bus/pci/devices/$addr"
    [ -e "$dev" ] || { echo "no such device: $addr" >&2; exit 1; }
    # tell the kernel which driver this device wants, then move it
    echo vfio-pci > "$dev/driver_override"
    if [ -e "$dev/driver" ]; then
        echo "$addr" > "$dev/driver/unbind"
    fi
    echo "$addr" > /sys/bus/pci/drivers_probe
done
"""

RESTORE_SCRIPT = """
set -e
for addr in "$@"; do
    dev="/sys/bus/pci/devices/$addr"
    [ -e "$dev" ] || { echo "no such device: $addr" >&2; exit 1; }
    # an empty override means "whichever driver claims it normally"
    echo > "$dev/driver_override"
    if [ -e "$dev/driver" ]; then
        echo "$addr" > "$dev/driver/unbind"
    fi
    echo "$addr" > /sys/bus/pci/drivers_probe
done
"""

def svc_bind_vfio(addresses: list[str]) -> str:
    """Move devices onto vfio-pci now. Needs a password."""
    checked = [check_address(a) for a in addresses]
    run_root_script("modprobe vfio-pci || true", [])
    run_root_script(BIND_SCRIPT, checked)
    return f"{', '.join(checked)} now on vfio-pci"

def svc_restore_driver(addresses: list[str]) -> str:
    """Give devices back to whatever normally drives them."""
    checked = [check_address(a) for a in addresses]
    run_root_script(RESTORE_SCRIPT, checked)
    return f"{', '.join(checked)} handed back to the host"

# -- binding at boot

def modprobe_conf(idents: list[str]) -> str:
    """The modprobe.d file that claims these devices for vfio-pci at boot.

    softdep lines make vfio-pci load before the drivers that would
    otherwise get there first; without them the options line is read too
    late to matter on a machine that has the real driver in its initramfs.
    """
    for ident in idents:
        if not PCI_ID.match(ident):
            raise ValueError(f"{ident!r} is not a PCI id like 10de:2705")
    ids = ",".join(i.lower() for i in idents)
    return (
        "# Written by VMManager: hand these devices to vfio-pci at boot,\n"
        "# before the host's own driver can claim them.\n"
        f"options vfio-pci ids={ids}\n"
        "softdep nvidia pre: vfio-pci\n"
        "softdep nouveau pre: vfio-pci\n"
        "softdep amdgpu pre: vfio-pci\n"
        "softdep radeon pre: vfio-pci\n"
        "softdep snd_hda_intel pre: vfio-pci\n"
    )

def cmdline_snippet(idents: list[str]) -> str:
    """The kernel command line equivalent, for people who prefer it there."""
    for ident in idents:
        if not PCI_ID.match(ident):
            raise ValueError(f"{ident!r} is not a PCI id like 10de:2705")
    return "vfio-pci.ids=" + ",".join(i.lower() for i in idents)

def initramfs_command(root: str = "/") -> tuple[list[str], str] | None:
    """How this distribution rebuilds its initramfs, if we recognise it.

    A modprobe.d file that is not in the initramfs is read after the
    graphics driver has already loaded, so this step is not optional -
    and it is the one everybody forgets.
    """
    candidates = [
        (["mkinitcpio", "-P"], "usr/bin/mkinitcpio", "Arch"),
        (["dracut", "--force", "--regenerate-all"], "usr/bin/dracut", "Fedora/RHEL"),
        (["update-initramfs", "-u", "-k", "all"], "usr/sbin/update-initramfs",
         "Debian/Ubuntu"),
    ]
    for argv, probe, distro in candidates:
        if os.path.exists(os.path.join(root, probe)):
            return argv, distro
    return None

def svc_persist_vfio(idents: list[str], rebuild_initramfs: bool = True) -> str:
    """Write the modprobe.d file and rebuild the initramfs."""
    install_root_file(MODPROBE_CONF, modprobe_conf(idents))
    message = f"wrote {MODPROBE_CONF}"
    if rebuild_initramfs:
        found = initramfs_command()
        if found is None:
            return (
                message + " - rebuild your initramfs yourself, then reboot "
                "(this distribution's command was not recognised)"
            )
        argv, distro = found
        run_root_script('"$@"', argv, timeout=900)
        message += f" and rebuilt the initramfs ({distro})"
    return message + " - reboot for it to take effect"

def svc_clear_persist_vfio() -> str:
    run_root_script('rm -f "$1"', [MODPROBE_CONF])
    found = initramfs_command()
    if found is not None:
        run_root_script('"$@"', found[0], timeout=900)
    return f"removed {MODPROBE_CONF} - reboot to hand the devices back"

def persisted_ids(path: str = MODPROBE_CONF) -> list[str]:
    """The ids our modprobe.d file currently claims, if it is there."""
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return []
    for line in text.splitlines():
        if line.startswith("options vfio-pci") and "ids=" in line:
            return [
                i for i in line.split("ids=", 1)[1].split()[0].split(",") if i
            ]
    return []

# -- IOMMU

def iommu_advice(root: str = "/") -> str:
    """What to add to the kernel command line, for this host specifically."""
    vendor = ""
    try:
        with open(os.path.join(root, "proc/cpuinfo")) as f:
            for line in f:
                if line.startswith("vendor_id"):
                    vendor = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    flag = "amd_iommu=on" if "AMD" in vendor else "intel_iommu=on"
    param = f"{flag} iommu=pt"
    if os.path.exists(os.path.join(root, "etc/default/grub")):
        where = (
            "add it to GRUB_CMDLINE_LINUX_DEFAULT in /etc/default/grub, then "
            "run grub-mkconfig -o /boot/grub/grub.cfg"
        )
    elif os.path.isdir(os.path.join(root, "boot/loader/entries")):
        where = (
            "add it to the options line of your entry in "
            "/boot/loader/entries/, or to /etc/kernel/cmdline if you use "
            "a unified kernel image"
        )
    else:
        where = "add it wherever your bootloader keeps the kernel command line"
    return f"{param} - {where}, and reboot."

# -- video BIOS ROMs
#
# A PCI expansion ROM is a chain of images. Each starts with 0x55 0xAA;
# two bytes at 0x18 point at a "PCIR" structure holding the image's
# length, its code type (0 is the legacy x86 BIOS, 3 is EFI) and a bit
# saying whether it is the last one. A card's ROM dumped from a running
# system often leads with the EFI image, and a guest that wants a video
# BIOS wants the x86 one - which is the trim people do in a hex editor.

@dataclass(frozen=True)
class RomImage:
    offset: int
    length: int
    code_type: int  # 0 x86, 1 Open Firmware, 2 PA-RISC, 3 EFI
    last: bool
    vendor: str
    device: str

    @property
    def kind(self) -> str:
        return {0: "x86 BIOS", 1: "Open Firmware", 2: "PA-RISC",
                3: "EFI"}.get(self.code_type, f"type {self.code_type}")

def parse_rom_images(data: bytes) -> list[RomImage]:
    """Walk a PCI expansion ROM. Stops at anything that does not parse."""
    images: list[RomImage] = []
    offset = 0
    while offset + 0x1A <= len(data):
        if data[offset:offset + 2] != b"\x55\xAA":
            break
        (pcir_off,) = struct.unpack_from("<H", data, offset + 0x18)
        pcir = offset + pcir_off
        if pcir + 0x16 > len(data) or data[pcir:pcir + 4] != b"PCIR":
            break
        vendor, device = struct.unpack_from("<HH", data, pcir + 4)
        (blocks,) = struct.unpack_from("<H", data, pcir + 0x10)
        code_type = data[pcir + 0x14]
        last = bool(data[pcir + 0x15] & 0x80)
        length = blocks * 512
        if length <= 0:
            break
        images.append(RomImage(
            offset=offset, length=length, code_type=code_type, last=last,
            vendor=f"{vendor:04x}", device=f"{device:04x}",
        ))
        if last:
            break
        offset += length
    return images

def trim_rom_to_legacy(data: bytes) -> bytes:
    """The x86 image on its own, which is what a guest's BIOS looks for.

    Already-trimmed ROMs come back unchanged. A ROM with no x86 image at
    all is refused rather than silently handed over - a card whose ROM is
    EFI-only needs an OVMF guest and no rom file, not a mangled one.
    """
    images = parse_rom_images(data)
    if not images:
        raise RuntimeError(
            "This does not look like a PCI option ROM (no 0x55 0xAA "
            "signature). A ROM dumped while the card was in use is often "
            "empty - try dumping it again from a boot where the guest "
            "never started."
        )
    legacy = next((i for i in images if i.code_type == 0), None)
    if legacy is None:
        kinds = ", ".join(i.kind for i in images)
        raise RuntimeError(
            f"This ROM holds no legacy x86 image (only {kinds}), so there "
            "is nothing to trim to. A UEFI guest does not need a rom file."
        )
    if legacy.offset == 0 and len(data) == legacy.length:
        return data
    return data[legacy.offset:legacy.offset + legacy.length]

def rom_matches_device(data: bytes, ids: DeviceIds) -> bool:
    """Whether a ROM says it belongs to this card."""
    images = parse_rom_images(data)
    return any(
        i.vendor == ids.vendor.lower() and i.device == ids.device.lower()
        for i in images
    )

# Reading a device's ROM needs the "rom" attribute enabled first, and the
# read has to happen while it is enabled - hence one script rather than
# three calls. The card must not be in use, which is why this is offered
# with the machine shut down.
DUMP_ROM_SCRIPT = """
set -e
dev="/sys/bus/pci/devices/$1"
[ -e "$dev/rom" ] || { echo "this device exposes no ROM" >&2; exit 1; }
echo 1 > "$dev/rom"
cat "$dev/rom" > "$2"
echo 0 > "$dev/rom"
chmod 0644 "$2"
"""

def svc_dump_rom(address: str, dest_path: str) -> str:
    """Copy a card's video BIOS out of sysfs into a file we can read."""
    address = check_address(address)
    if not dest_path.startswith("/") or any(c in dest_path for c in "\n\r"):
        raise ValueError(f"{dest_path!r} is not a plain absolute path")
    run_root_script(DUMP_ROM_SCRIPT, [address, dest_path])
    try:
        size = os.path.getsize(dest_path)
    except OSError:
        size = 0
    if size == 0:
        raise RuntimeError(
            "The ROM read back empty. That usually means the card is in "
            "use by the host - the dump works from a boot where the "
            "graphics driver never bound to it, or once it is on vfio-pci."
        )
    return f"dumped {size / 1024:.0f} KB from {address}"
