"""Images from other hypervisors: VMware, VirtualBox, Hyper-V.

Three shapes arrive at the wizard: a bare foreign disk (vmdk, vhdx, vhd,
vdi), an OVF descriptor next to its disks, or an OVA, which is the two of
those in a tar. qemu-img turns any of them into qcow2; the OVF also names
the machine and says how much CPU and memory it expected, which the wizard
uses as defaults rather than guessing.

Everything here is pure parsing and command building - no libvirt - so it
tests against files on disk.
"""

from __future__ import annotations

import os
import tarfile
from dataclasses import dataclass

import xml.etree.ElementTree as ET

# extension → qemu-img format name. qemu-img can probe most of these, but
# naming the source format means a mangled file fails with a sentence
# instead of being misread.
FOREIGN_FORMATS = {
    ".vmdk": "vmdk",   # VMware
    ".vhdx": "vhdx",   # Hyper-V
    ".vhd": "vpc",     # older Hyper-V / Azure
    ".vpc": "vpc",
    ".vdi": "vdi",     # VirtualBox
}

@dataclass(frozen=True)
class OvfInfo:
    """What an OVF descriptor says about the machine it describes."""

    name: str
    vcpus: int  # 0 when the descriptor does not say
    memory_mb: int  # 0 when the descriptor does not say
    disk_files: tuple[str, ...]  # hrefs in boot order, first is the system disk

def foreign_format(path: str) -> str | None:
    """The qemu-img format name for a foreign disk, or None if native."""
    return FOREIGN_FORMATS.get(os.path.splitext(path)[1].lower())

def is_foreign_source(path: str) -> bool:
    """True for anything the import path has to convert or unpack first."""
    ext = os.path.splitext(path)[1].lower()
    return ext in FOREIGN_FORMATS or ext in (".ova", ".ovf")

def convert_cmd(src: str, dst: str, src_format: str | None = None) -> list[str]:
    """The qemu-img call that rewrites a foreign disk as qcow2."""
    cmd = ["qemu-img", "convert"]
    if src_format:
        cmd += ["-f", src_format]
    return cmd + ["-O", "qcow2", src, dst]

def _local(tag: str) -> str:
    """Element name without its namespace - OVF producers disagree on
    prefixes, and some omit namespaces entirely."""
    return tag.rsplit("}", 1)[-1]

def _attr(el: ET.Element, name: str) -> str:
    """An attribute regardless of which namespace it was written in."""
    for key, value in el.attrib.items():
        if _local(key) == name:
            return value
    return ""

def parse_ovf(text: str) -> OvfInfo:
    """Name, CPU, memory and disk files out of an OVF descriptor.

    Resource types 3 and 4 are CPU and memory in every OVF; memory's
    AllocationUnits says what the quantity counts ("byte * 2^20" is MB).
    Missing pieces come back as 0 - the wizard keeps its own defaults then.
    """
    root = ET.fromstring(text)

    # ovf:File id → href, in document order
    files: dict[str, str] = {}
    for el in root.iter():
        if _local(el.tag) == "File":
            file_id = _attr(el, "id")
            href = _attr(el, "href")
            if file_id and href:
                files[file_id] = href

    # DiskSection references files; only those are disks (the rest are
    # manifests, ISO images and so on)
    disk_refs: list[str] = []
    for el in root.iter():
        if _local(el.tag) == "Disk":
            ref = _attr(el, "fileRef")
            if ref in files:
                disk_refs.append(files[ref])

    name = ""
    vcpus = 0
    memory_mb = 0
    for system in root.iter():
        if _local(system.tag) != "VirtualSystem":
            continue
        name = _attr(system, "id")
        for el in system.iter():
            if _local(el.tag) == "Name" and (el.text or "").strip():
                name = el.text.strip()
                break
        for item in system.iter():
            if _local(item.tag) != "Item":
                continue
            rtype = quantity = units = ""
            for child in item:
                if _local(child.tag) == "ResourceType":
                    rtype = (child.text or "").strip()
                elif _local(child.tag) == "VirtualQuantity":
                    quantity = (child.text or "").strip()
                elif _local(child.tag) == "AllocationUnits":
                    units = (child.text or "").strip()
            if not quantity.isdigit():
                continue
            if rtype == "3":
                vcpus = int(quantity)
            elif rtype == "4":
                memory_mb = _to_mb(int(quantity), units)
        break  # the first VirtualSystem is the machine

    return OvfInfo(
        name=name, vcpus=vcpus, memory_mb=memory_mb,
        disk_files=tuple(disk_refs),
    )

def _to_mb(quantity: int, units: str) -> int:
    """OVF memory units: 'byte * 2^20' style, or unit names."""
    u = units.lower().replace(" ", "")
    if u in ("", "byte*2^20", "megabytes", "mb"):
        return quantity
    if u in ("byte*2^30", "gigabytes", "gb"):
        return quantity * 1024
    if u in ("byte*2^10", "kilobytes", "kb"):
        return max(1, quantity // 1024)
    if u in ("byte", "bytes"):
        return max(1, quantity // 1024**2)
    return quantity

def ovf_from_ova(path: str) -> OvfInfo:
    """Read just the descriptor out of an OVA without unpacking the disks."""
    with tarfile.open(path) as tar:
        for member in tar.getmembers():
            if member.name.lower().endswith(".ovf"):
                f = tar.extractfile(member)
                if f is not None:
                    return parse_ovf(f.read().decode("utf-8", "replace"))
    raise RuntimeError("No .ovf descriptor inside this OVA")

def describe_source(path: str) -> OvfInfo | None:
    """OVF details for an .ova/.ovf pick; None for a bare disk."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".ova":
        return ovf_from_ova(path)
    if ext == ".ovf":
        with open(path, encoding="utf-8", errors="replace") as f:
            return parse_ovf(f.read())
    return None

def extract_ova(path: str, dest_dir: str) -> list[str]:
    """Unpack an OVA's disks; the descriptor's disk order, absolute paths."""
    info = ovf_from_ova(path)
    wanted = set(info.disk_files)
    with tarfile.open(path) as tar:
        members = [
            m for m in tar.getmembers()
            if os.path.basename(m.name) in wanted and m.isfile()
        ]
        tar.extractall(dest_dir, members=members, filter="data")
    by_name = {
        os.path.basename(m.name): os.path.join(dest_dir, m.name)
        for m in members
    }
    missing = [f for f in info.disk_files if f not in by_name]
    if missing:
        raise RuntimeError(
            f"The OVA's descriptor names {missing[0]} but the archive does "
            "not contain it"
        )
    return [by_name[f] for f in info.disk_files]

def foreign_disk_files(path: str, workdir: str) -> list[str]:
    """The disk files an import source resolves to, extracting if needed.

    Order matters: the first one is the system disk and becomes the
    machine's boot volume.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".ova":
        return extract_ova(path, workdir)
    if ext == ".ovf":
        info = parse_ovf(open(path, encoding="utf-8", errors="replace").read())
        base = os.path.dirname(os.path.abspath(path))
        out = []
        for href in info.disk_files:
            full = os.path.join(base, href)
            if not os.path.exists(full):
                raise RuntimeError(
                    f"The descriptor names {href} and it is not next to the "
                    ".ovf file"
                )
            out.append(full)
        if not out:
            raise RuntimeError("The descriptor names no disks")
        return out
    return [path]
