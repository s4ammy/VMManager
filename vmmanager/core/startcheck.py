"""Why a machine will not start.

libvirt's answer to a failed start is usually accurate and rarely useful:
"unable to set XATTR", "Device or resource busy", "unsupported configuration".
The reason is nearly always something about the host that the definition
assumed and the host no longer provides - a card the host driver took back,
hugepages nobody reserved, an ISO that moved.

Everything here reads; nothing changes anything. The checks are the same
readers the rest of the app uses, pointed at one question.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import libvirt

from .connection import _with_conn
from .tuning import _parse_cpuset

@dataclass(frozen=True)
class StartProblem:
    """One reason, in the order a person would want to read them."""

    severity: str  # "blocked" | "caution"
    what: str
    why: str

def _hugepage_free(size_kb: int) -> int | None:
    """Free pages of that size, or None if the host does not have the pool."""
    name = "hugepages-%dkB" % size_kb
    path = f"/sys/kernel/mm/hugepages/{name}/free_hugepages"
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None

def _memory_mb(root: ET.Element) -> int:
    """What <memory> asks for, in MiB, whatever unit it is written in."""
    el = root.find("memory")
    if el is None or not (el.text or "").strip().isdigit():
        return 0
    value = int(el.text.strip())
    unit = el.get("unit", "KiB")
    return {
        "KiB": value // 1024, "MiB": value, "GiB": value * 1024,
        "bytes": value // 1024**2,
    }.get(unit, value // 1024)


def check_disks(root: ET.Element) -> list[StartProblem]:
    """Every file the definition points at, and whether it is there."""
    out = []
    for disk in root.findall("devices/disk"):
        src = disk.find("source")
        if src is None or not src.get("file"):
            continue
        path = src.get("file")
        target = disk.find("target")
        dev = target.get("dev", "?") if target is not None else "?"
        kind = disk.get("device", "disk")
        if os.path.exists(path):
            continue
        if kind == "cdrom":
            out.append(StartProblem(
                "caution",
                f"The disc in {dev} is missing",
                f"{path} is not there any more. A machine usually still "
                "starts with an empty drive, but it will not boot from that "
                "disc - eject it or point it somewhere else.",
            ))
        else:
            out.append(StartProblem(
                "blocked",
                f"The disk {dev} is missing",
                f"{path} does not exist. If its storage pool is on a "
                "filesystem that is not mounted, mounting it is the fix; "
                "otherwise the disk is gone and the machine cannot start.",
            ))
    return out

def check_firmware(root: ET.Element) -> list[StartProblem]:
    """A UEFI machine whose NVRAM file has been removed."""
    nvram = root.findtext("os/nvram")
    if nvram and not os.path.exists(nvram):
        return [StartProblem(
            "caution",
            "The UEFI variables file is missing",
            f"{nvram} is gone. libvirt makes a fresh one from the firmware "
            "template, which starts the machine but loses its boot entries - "
            "a UEFI guest that boots to the firmware menu has usually just "
            "had this happen.",
        )]
    return []

def check_memory(root: ET.Element, host_free_mb: int) -> list[StartProblem]:
    """Whether the host can still find the memory the machine asks for."""
    wanted_mb = _memory_mb(root)
    if not wanted_mb:
        return []
    if wanted_mb > host_free_mb:
        return [StartProblem(
            "caution",
            f"It asks for {wanted_mb / 1024:.1f} GB and the host has "
            f"{host_free_mb / 1024:.1f} GB free",
            "Linux will usually still start it by reclaiming cache, and it "
            "may swap or be killed under pressure. Hugepage-backed machines "
            "are the exception: those need the memory up front.",
        )]
    return []

def check_hugepages(root: ET.Element, free_pages=_hugepage_free) -> list[StartProblem]:
    """Hugepage backing asks for memory that has to exist before it starts."""
    backing = root.find("memoryBacking/hugepages")
    if backing is None:
        return []
    page = backing.find("page")
    size_kb = 2048
    if page is not None and page.get("size", "").isdigit():
        size_kb = int(page.get("size"))
        if page.get("unit", "KiB") == "MiB":
            size_kb *= 1024
    need = -(-(_memory_mb(root) * 1024) // size_kb)  # round up
    have = free_pages(size_kb)
    if have is None:
        return [StartProblem(
            "blocked",
            f"It wants {size_kb // 1024} MiB hugepages and this host has no "
            "pool of that size",
            "Nothing has reserved hugepages of that size, so there are none "
            "to hand out and the machine cannot start. Reserve them - "
            "vm.nr_hugepages for 2 MiB pages, or hugepagesz and hugepages on "
            "the kernel command line for 1 GiB - or turn hugepage backing "
            "off in Tuning.",
        )]
    if have < need:
        return [StartProblem(
            "blocked",
            f"It needs {need} hugepages and {have} are free",
            "Hugepages are reserved up front and are not reclaimed from "
            "anything else, so a machine that asks for more than are spare "
            "simply fails to start. Another running machine may be holding "
            "them.",
        )]
    return []

def check_hostdevs(root: ET.Element, driver_of) -> list[StartProblem]:
    """PCI devices the host has taken back since the machine last ran."""
    out = []
    for h in root.findall("devices/hostdev"):
        if h.get("type") != "pci":
            continue
        a = h.find("source/address")
        if a is None:
            continue
        try:
            address = (
                f"{int(a.get('domain', '0'), 16):04x}:"
                f"{int(a.get('bus', '0'), 16):02x}:"
                f"{int(a.get('slot', '0'), 16):02x}."
                f"{int(a.get('function', '0'), 16):x}"
            )
        except ValueError:
            continue
        driver = driver_of(address)
        if driver is None:
            out.append(StartProblem(
                "blocked",
                f"The device {address} is not on this host",
                "It is in the definition but the host has no PCI device at "
                "that address. A card moved to another slot changes its "
                "address.",
            ))
        elif driver not in ("vfio-pci", ""):
            out.append(StartProblem(
                "caution",
                f"{address} is bound to {driver}, not vfio-pci",
                "libvirt detaches a managed device as it starts, which works "
                "when nothing is using it. If the host driver will not let "
                "go - a GPU driving your display, most often - the start "
                "fails here. Bind it at boot from the passthrough "
                "diagnostics to take that out of the equation.",
            ))
    return out

def _driver_of(address: str) -> str | None:
    link = f"/sys/bus/pci/devices/{address}/driver"
    if not os.path.exists(f"/sys/bus/pci/devices/{address}"):
        return None
    if os.path.islink(link):
        return os.path.basename(os.readlink(link))
    return ""

def check_pinning(root: ET.Element, host_cpus: int) -> list[StartProblem]:
    """Pinning that names host CPUs this host does not have."""
    out = []
    for pin in root.findall("cputune/vcpupin"):
        for cpu in _parse_cpuset(pin.get("cpuset", "")):
            if cpu >= host_cpus:
                out.append(StartProblem(
                    "blocked",
                    f"vCPU {pin.get('vcpu')} is pinned to host CPU {cpu}",
                    f"This host has CPUs 0-{host_cpus - 1}. A definition "
                    "moved from a bigger machine keeps its pinning, and "
                    "libvirt refuses to start it rather than ignoring it.",
                ))
                break
    return out


def svc_start_problems(uuid: str) -> list[StartProblem]:
    """Everything about this host that would stop the machine starting."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        free_mb = 0
        try:
            free_mb = conn.getFreeMemory() // 1024**2
        except libvirt.libvirtError:
            pass
        problems: list[StartProblem] = []
        problems += check_disks(root)
        problems += check_firmware(root)
        problems += check_hugepages(root)
        problems += check_hostdevs(root, _driver_of)
        try:
            problems += check_pinning(root, conn.getInfo()[2])
        except libvirt.libvirtError:
            pass
        if free_mb:
            problems += check_memory(root, free_mb)
        # blocked first: those are the ones that will actually stop it
        problems.sort(key=lambda p: 0 if p.severity == "blocked" else 1)
        return problems

    return _with_conn(go)
