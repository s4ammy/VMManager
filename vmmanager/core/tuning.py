"""Performance tuning: CPU pinning, hugepages, iothreads, disk throttling.

The knobs that matter once a machine is doing real work, and the ones a
passthrough guest needs to behave. All of them live outside the device list, in
<cputune>, <memoryBacking>, <iothreads> and per-disk <iotune>, which is why they
sit here rather than in devices.py.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import libvirt

from .connection import _with_conn
from .devices import _APPLIED_CONFIG, _APPLIED_LIVE

HUGEPAGE_SYSFS = Path("/sys/kernel/mm/hugepages")


@dataclass(frozen=True)
class HostCpu:
    id: int
    socket: int
    core: int
    siblings: tuple[int, ...]  # every logical cpu on the same physical core
    cell: int  # NUMA node


@dataclass(frozen=True)
class HugePagePool:
    size_kb: int
    total: int  # pages the kernel has reserved
    free: int  # of those, still unused (local hosts only)

    @property
    def bytes_total(self) -> int:
        return self.size_kb * 1024 * self.total

    @property
    def label(self) -> str:
        return f"{self.size_kb // 1024} MiB" if self.size_kb < 1024**2 else "1 GiB"


@dataclass(frozen=True)
class HostTopology:
    sockets: int
    cores: int
    threads: int
    cpus: tuple[HostCpu, ...]
    cells: int
    hugepages: tuple[HugePagePool, ...]

    @property
    def total_cpus(self) -> int:
        return len(self.cpus)

    def physical_cores(self) -> list[tuple[int, ...]]:
        """One entry per physical core, holding its logical cpus in order."""
        seen: dict[tuple[int, int], list[int]] = {}
        for cpu in self.cpus:
            seen.setdefault((cpu.socket, cpu.core), []).append(cpu.id)
        return [tuple(sorted(v)) for _k, v in sorted(seen.items())]


@dataclass(frozen=True)
class DiskThrottle:
    read_bps: int = 0  # 0 means unlimited throughout
    write_bps: int = 0
    read_iops: int = 0
    write_iops: int = 0

    @property
    def limited(self) -> bool:
        return any((self.read_bps, self.write_bps, self.read_iops, self.write_iops))


@dataclass(frozen=True)
class Tuning:
    vcpu_pins: dict[int, tuple[int, ...]] = field(default_factory=dict)
    emulator_pin: tuple[int, ...] = ()
    hugepage_size_kb: int = 0  # 0 means not backed by hugepages
    iothreads: int = 0
    throttles: dict[str, DiskThrottle] = field(default_factory=dict)
    # <cputune>: how much host CPU this machine may take when there is
    # competition for it. shares is a relative weight (1024 is the default
    # every process starts with); quota/period are a hard ceiling per vCPU,
    # in microseconds.
    cpu_shares: int = 0   # 0 means not set
    cpu_quota: int = 0    # 0 means not set, -1 means explicitly unlimited
    cpu_period: int = 0

    @property
    def pinned(self) -> bool:
        return bool(self.vcpu_pins)

    @property
    def cpu_cap_pct(self) -> int:
        """The quota as a percentage of one vCPU, or 0 when uncapped."""
        if self.cpu_quota > 0 and self.cpu_period > 0:
            return round(self.cpu_quota * 100 / self.cpu_period)
        return 0


def _parse_cpuset(text: str) -> tuple[int, ...]:
    """"0-3,8" -> (0, 1, 2, 3, 8). libvirt accepts either form."""
    out: list[int] = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            try:
                out.extend(range(int(start), int(end) + 1))
            except ValueError:
                continue
        else:
            try:
                out.append(int(part))
            except ValueError:
                continue
    return tuple(sorted(set(out)))


def format_cpuset(cpus) -> str:
    """The inverse: (0,1,2,3,8) -> "0-3,8", which is what libvirt prefers."""
    ids = sorted(set(cpus))
    if not ids:
        return ""
    runs: list[str] = []
    start = previous = ids[0]
    for cpu in ids[1:]:
        if cpu == previous + 1:
            previous = cpu
            continue
        runs.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = cpu
    runs.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(runs)


def _hugepage_pools(caps: ET.Element) -> tuple[HugePagePool, ...]:
    """Pool sizes from libvirt, free counts from sysfs when we are local.

    libvirt reports how many pages exist but not how many are spare, and a
    machine asking for hugepages that are all taken simply fails to start, so
    the free count is worth the extra read.
    """
    totals: dict[int, int] = {}
    for pages in caps.findall("host/topology/cells/cell/pages"):
        try:
            size = int(pages.get("size", "0"))
            count = int((pages.text or "0").strip())
        except ValueError:
            continue
        if size > 4:  # 4 KiB is ordinary memory, not a hugepage pool
            totals[size] = totals.get(size, 0) + count

    pools = []
    for size_kb in sorted(totals):
        free = 0
        directory = HUGEPAGE_SYSFS / f"hugepages-{size_kb}kB"
        try:
            free = int((directory / "free_hugepages").read_text().strip())
        except (OSError, ValueError):
            free = 0
        pools.append(HugePagePool(size_kb, totals[size_kb], free))
    return tuple(pools)


def svc_host_topology() -> HostTopology:
    """What the host's CPUs look like, for the pinning picker."""

    def go(conn):
        caps = ET.fromstring(conn.getCapabilities())
        topo = caps.find("host/cpu/topology")
        cpus: list[HostCpu] = []
        cells = caps.findall("host/topology/cells/cell")
        for cell in cells:
            for cpu in cell.findall("cpus/cpu"):
                try:
                    cpu_id = int(cpu.get("id", "-1"))
                except ValueError:
                    continue
                cpus.append(HostCpu(
                    id=cpu_id,
                    socket=int(cpu.get("socket_id", "0") or 0),
                    core=int(cpu.get("core_id", "0") or 0),
                    siblings=_parse_cpuset(cpu.get("siblings", str(cpu_id))),
                    cell=int(cell.get("id", "0") or 0),
                ))
        return HostTopology(
            sockets=int(topo.get("sockets", "1")) if topo is not None else 1,
            cores=int(topo.get("cores", "1")) if topo is not None else 1,
            threads=int(topo.get("threads", "1")) if topo is not None else 1,
            cpus=tuple(sorted(cpus, key=lambda c: c.id)),
            cells=max(len(cells), 1),
            hugepages=_hugepage_pools(caps),
        )

    return _with_conn(go)


# How to lay vCPUs over the host's cores.
#
# "paired" gives the guest half as many physical cores, each with both its
# threads: the standard recipe for a passthrough guest, because it leaves whole
# cores free for the host and the guest can be told the truth about which of its
# CPUs are siblings.
#
# "cores" gives every vCPU a core to itself, which is faster per thread but uses
# twice the cores and leaves the host less.
PIN_PAIRED = "paired"
PIN_PER_CORE = "cores"


def auto_pin(vcpus: int, topology: HostTopology, mode: str = PIN_PAIRED,
             avoid_first_core: bool = True) -> dict[int, tuple[int, ...]]:
    """Lay vCPUs over host CPUs, uniformly where the numbers allow.

    A uniform layout matters beyond tidiness: the guest's own topology has to be
    sockets x cores x threads, so only a uniform pinning can be described to the
    guest truthfully. See guest_topology_for.
    """
    cores = topology.physical_cores()
    if not cores or vcpus <= 0:
        return {}

    threads = len(cores[0])
    if mode == PIN_PAIRED and threads > 1 and vcpus % threads == 0:
        needed = vcpus // threads
        usable = cores[1:] if avoid_first_core and len(cores) > needed else cores
        if len(usable) >= needed:
            pins = {}
            for index in range(needed):
                for thread in range(threads):
                    pins[index * threads + thread] = (usable[index][thread],)
            return pins

    usable = cores[1:] if avoid_first_core and len(cores) > vcpus else cores
    if not usable:
        usable = cores

    pins: dict[int, tuple[int, ...]] = {}
    # first pass: one vCPU per core, second pass: the sibling thread, and so on
    thread_index = 0
    vcpu = 0
    while vcpu < vcpus:
        progressed = False
        for core in usable:
            if vcpu >= vcpus:
                break
            if thread_index < len(core):
                pins[vcpu] = (core[thread_index],)
                vcpu += 1
                progressed = True
        if not progressed:
            # more vCPUs than logical cpus: share what we have, in order
            for extra in range(vcpu, vcpus):
                core = usable[extra % len(usable)]
                pins[extra] = (core[extra // len(usable) % len(core)],)
            break
        thread_index += 1
    return pins


def guest_topology_for(pins: dict[int, tuple[int, ...]],
                       topology: HostTopology) -> tuple[int, int, int] | None:
    """The guest topology matching this pinning, or None if it has none.

    A guest told it has 8 independent cores, when really its vCPUs are paired
    onto 4, schedules two busy threads onto what is one core and wonders why
    they are slow. Telling it the truth needs sockets x cores x threads, so a
    pinning that is not uniform cannot be described and returns None.
    """
    if not pins:
        return None
    host_core_of = {}
    for index, core in enumerate(topology.physical_cores()):
        for cpu in core:
            host_core_of[cpu] = index

    per_core: dict[int, int] = {}
    for cpus in pins.values():
        if len(cpus) != 1:
            return None  # pinned to a set rather than one cpu: not describable
        core = host_core_of.get(cpus[0])
        if core is None:
            return None
        per_core[core] = per_core.get(core, 0) + 1

    counts = set(per_core.values())
    if len(counts) != 1:
        return None  # some cores carry more vCPUs than others
    threads = counts.pop()
    cores = len(per_core)
    if cores * threads != len(pins):
        return None
    return (1, cores, threads)


def emulator_cpus(topology: HostTopology, pins: dict[int, tuple[int, ...]] | None = None) -> tuple[int, ...]:
    """A core the guest is not using, for the emulator thread.

    Pinning the emulator onto a core a vCPU already owns is worse than leaving
    it unpinned, so when every core is in use this returns nothing and the host
    keeps scheduling it freely.
    """
    cores = topology.physical_cores()
    if not cores:
        return ()
    taken = {cpu for cpus in (pins or {}).values() for cpu in cpus}
    if not taken:
        return cores[0]
    # best: a core the guest is not on at all
    for core in cores:
        if not (set(core) & taken):
            return core
    # next best: the sibling threads nobody is using. Shares cores with the
    # guest, but keeps the emulator off the guest's own logical CPUs.
    spare = tuple(cpu.id for cpu in topology.cpus if cpu.id not in taken)
    return spare


# ---------------------------------------------------------------- reading


def svc_get_tuning(uuid: str) -> Tuning:
    """Current tuning from the persistent definition."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        pins = {}
        for pin in root.findall("cputune/vcpupin"):
            try:
                pins[int(pin.get("vcpu", "-1"))] = _parse_cpuset(pin.get("cpuset", ""))
            except ValueError:
                continue
        emulator = root.find("cputune/emulatorpin")
        page = root.find("memoryBacking/hugepages/page")
        size_kb = 0
        if root.find("memoryBacking/hugepages") is not None:
            if page is not None:
                try:
                    size_kb = int(page.get("size", "0"))
                    if page.get("unit", "KiB") == "MiB":
                        size_kb *= 1024
                except ValueError:
                    size_kb = 0
            else:
                size_kb = -1  # hugepages on, size left to the host
        def cputune_int(tag: str) -> int:
            text = root.findtext(f"cputune/{tag}")
            try:
                return int(text) if text else 0
            except ValueError:
                return 0

        iothreads = root.findtext("iothreads")
        throttles = {}
        for disk in root.findall("devices/disk"):
            target = disk.find("target")
            iotune = disk.find("iotune")
            if target is None or iotune is None:
                continue

            def value(tag: str) -> int:
                text = iotune.findtext(tag)
                return int(text) if text and text.isdigit() else 0

            throttles[target.get("dev", "?")] = DiskThrottle(
                read_bps=value("read_bytes_sec"),
                write_bps=value("write_bytes_sec"),
                read_iops=value("read_iops_sec"),
                write_iops=value("write_iops_sec"),
            )
        return Tuning(
            vcpu_pins=pins,
            emulator_pin=_parse_cpuset(emulator.get("cpuset", "")) if emulator is not None else (),
            hugepage_size_kb=size_kb,
            iothreads=int(iothreads) if iothreads and iothreads.isdigit() else 0,
            throttles=throttles,
            cpu_shares=cputune_int("shares"),
            cpu_quota=cputune_int("quota"),
            cpu_period=cputune_int("period"),
        )

    return _with_conn(go)


# ---------------------------------------------------------------- writing


def _element(parent: ET.Element, tag: str) -> ET.Element:
    found = parent.find(tag)
    if found is None:
        found = ET.SubElement(parent, tag)
    return found


def svc_set_cpu_pinning(uuid: str, pins: dict[int, tuple[int, ...]],
                        emulator: tuple[int, ...] = ()) -> str:
    """Pin each vCPU to a set of host CPUs. Empty clears all pinning."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        existing = root.find("cputune")
        # shares and quota live in the same element and are nothing to do
        # with pinning; rebuilding it from scratch would silently drop them.
        kept = []
        if existing is not None:
            kept = [
                child for child in existing
                if child.tag in ("shares", "quota", "period",
                                 "global_quota", "global_period")
            ]
            root.remove(existing)
        if pins or emulator or kept:
            cputune = ET.SubElement(root, "cputune")
            for child in kept:
                cputune.append(child)
            for vcpu in sorted(pins):
                if not pins[vcpu]:
                    continue
                ET.SubElement(cputune, "vcpupin", {
                    "vcpu": str(vcpu), "cpuset": format_cpuset(pins[vcpu]),
                })
            if emulator:
                ET.SubElement(cputune, "emulatorpin",
                              {"cpuset": format_cpuset(emulator)})
        conn.defineXML(ET.tostring(root, encoding="unicode"))

        # a running machine can be repinned immediately, one vCPU at a time.
        # The mask is one boolean per host cpu, so its length has to match the
        # host rather than the highest cpu we happen to be using.
        if dom.isActive() and pins:
            host_cpus = conn.getInfo()[2]
            applied = 0
            for vcpu, cpus in sorted(pins.items()):
                mask = tuple(i in cpus for i in range(host_cpus))
                try:
                    dom.pinVcpuFlags(vcpu, mask, libvirt.VIR_DOMAIN_AFFECT_LIVE)
                    applied += 1
                except libvirt.libvirtError:
                    break
            if applied == len(pins):
                return _APPLIED_LIVE
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_set_cpu_limits(uuid: str, shares: int = 0, cap_pct: int = 0,
                       period: int = 100000) -> str:
    """How much host CPU this machine may take when something else wants it.

    Two different things, both in <cputune>:

    - `shares` is a weight, not a limit. It only matters when the host is
      oversubscribed, and then a machine with 2048 gets twice the CPU of one
      with the default 1024. Costs nothing when the host is idle.
    - `cap_pct` is a ceiling per vCPU, enforced whether the host is busy or
      not: 50 means each vCPU may use half a host CPU. This is the one that
      makes a guest feel slow on an idle machine, so it is off by default.

    Zero for either leaves it unset. Applied live where libvirt allows it,
    which for these it does.
    """
    if shares and not 2 <= shares <= 262144:
        raise ValueError("CPU shares must be between 2 and 262144")
    if cap_pct and not 1 <= cap_pct <= 100:
        raise ValueError("The CPU cap is a percentage of one vCPU, 1 to 100")

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        cputune = root.find("cputune")
        if cputune is None:
            cputune = ET.SubElement(root, "cputune")
        for tag in ("shares", "quota", "period"):
            found = cputune.find(tag)
            if found is not None:
                cputune.remove(found)
        if shares:
            ET.SubElement(cputune, "shares").text = str(shares)
        if cap_pct:
            # quota and period are microseconds of CPU time per vCPU
            ET.SubElement(cputune, "period").text = str(period)
            ET.SubElement(cputune, "quota").text = str(
                int(period * cap_pct / 100)
            )
        if len(cputune) == 0:
            root.remove(cputune)
        conn.defineXML(ET.tostring(root, encoding="unicode"))

        if dom.isActive():
            params = {}
            if shares:
                params["cpu_shares"] = shares
            if cap_pct:
                params["vcpu_period"] = period
                params["vcpu_quota"] = int(period * cap_pct / 100)
            elif cputune.find("quota") is None:
                params["vcpu_quota"] = -1  # lift a cap that was there
            if params:
                try:
                    dom.setSchedulerParameters(params)
                    return _APPLIED_LIVE
                except (libvirt.libvirtError, LookupError):
                    # LookupError, not libvirtError, is what a driver that
                    # has never heard of the parameter raises - the change
                    # is already in the definition either way.
                    pass
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_set_hugepages(uuid: str, size_kb: int) -> str:
    """Back the guest's memory with hugepages. 0 turns it off.

    Always a restart: the backing is chosen when QEMU allocates the memory.
    """

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        backing = root.find("memoryBacking")
        if size_kb <= 0:
            if backing is not None:
                hugepages = backing.find("hugepages")
                if hugepages is not None:
                    backing.remove(hugepages)
                if not list(backing):
                    root.remove(backing)
        else:
            if backing is None:
                backing = ET.SubElement(root, "memoryBacking")
            hugepages = _element(backing, "hugepages")
            for page in hugepages.findall("page"):
                hugepages.remove(page)
            ET.SubElement(hugepages, "page",
                          {"size": str(size_kb), "unit": "KiB"})
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_set_iothreads(uuid: str, count: int) -> str:
    """Dedicated threads for disk I/O, so it stops sharing the vCPU threads."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        existing = root.find("iothreads")
        if count <= 0:
            if existing is not None:
                root.remove(existing)
            # a disk pointing at a thread that no longer exists will not start
            for driver in root.findall("devices/disk/driver"):
                driver.attrib.pop("iothread", None)
        else:
            if existing is None:
                existing = ET.SubElement(root, "iothreads")
            existing.text = str(count)
        conn.defineXML(ET.tostring(root, encoding="unicode"))
        return _APPLIED_CONFIG

    return _with_conn(go)


def svc_set_disk_throttle(uuid: str, dev: str, limits: DiskThrottle) -> str:
    """Cap a disk's throughput and IOPS. Applies live where libvirt allows."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        params = {
            "read_bytes_sec": limits.read_bps,
            "write_bytes_sec": limits.write_bps,
            "read_iops_sec": limits.read_iops,
            "write_iops_sec": limits.write_iops,
        }
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if dom.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        dom.setBlockIoTune(dev, params, flags)
        return _APPLIED_LIVE if dom.isActive() else _APPLIED_CONFIG

    return _with_conn(go)


# ---------------------------------------------------------------- flattening


def _pool_of(conn, path: str):
    """The pool holding this volume, for refreshing its cached metadata."""
    return conn.storageVolLookupByPath(path).storagePoolLookupByVolume()


def svc_backing_chain(uuid: str) -> dict[str, str]:
    """Disk target -> the image it is layered on, for disks that have one."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        root = ET.fromstring(dom.XMLDesc(0))
        chain = {}
        for disk in root.findall("devices/disk"):
            if disk.get("device") != "disk":
                continue
            target = disk.find("target")
            source = disk.find("source")
            if target is None or source is None or not source.get("file"):
                continue
            try:
                vol = conn.storageVolLookupByPath(source.get("file"))
                parent = ET.fromstring(vol.XMLDesc(0)).findtext("backingStore/path")
            except (libvirt.libvirtError, ET.ParseError):
                parent = None
            if parent:
                chain[target.get("dev", "?")] = parent
        return chain

    return _with_conn(go)


def svc_flatten_disk(uuid: str, dev: str) -> str:
    """Pull a linked clone's backing image into its own overlay.

    Afterwards the disk stands alone: the template can be deleted and the
    machine keeps working. It costs the space the backing file was saving, and
    libvirt does the copy in the background, so this returns once it finishes.
    """

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        if not dom.isActive():
            raise RuntimeError(
                "blockPull needs the machine running - libvirt streams the "
                "backing image into the overlay while it works. Start it first."
            )
        import time

        source = None
        root = ET.fromstring(dom.XMLDesc(0))
        for disk in root.findall("devices/disk"):
            target = disk.find("target")
            if target is not None and target.get("dev") == dev:
                node = disk.find("source")
                source = node.get("file") if node is not None else None
        dom.blockPull(dev, 0, 0)
        while True:
            info = dom.blockJobInfo(dev, 0)
            if not info or info.get("cur", 0) >= info.get("end", 1):
                break
            time.sleep(0.5)

        # libvirt caches volume metadata, so without this the disk still looks
        # like it has a backing file everywhere we report the chain from.
        if source:
            try:
                _pool_of(conn, source).refresh(0)
            except libvirt.libvirtError:
                pass
        return f"{dev} is now a standalone image."

    return _with_conn(go)
