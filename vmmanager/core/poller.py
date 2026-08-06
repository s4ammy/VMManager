"""The background worker behind every list in the UI.

One persistent connection, snapshots on a timer, and libvirt events to catch
changes between ticks.
"""

from __future__ import annotations

from collections import deque
import time
import xml.etree.ElementTree as ET

import libvirt
from PySide6.QtCore import QThread, Signal

from ..logs import log
from .connection import HISTORY_LEN, STATE_NAMES, current_uri, poll_seconds

from .domains import _read_vmm_meta
from .events import DomainWatch, EventPump, ensure_registered
from .osident import detect_key
from .models import DomainSnapshot, HostSnapshot, Usage, _fmt_version

# How often the balloon driver reports guest memory, in seconds. Cheap: the
# guest writes a few counters, and nothing reads them in between.
BALLOON_STATS_PERIOD = 5


def guest_memory_kb(raw: dict) -> float:
    """What the guest is actually using, from libvirt's balloon stats.

    In order of truthfulness:

    - `available` minus `usable` is the guest's own view - everything it has
      minus what it could still hand out - which is what its task manager
      shows. Only present once the balloon driver is reporting stats.
    - `current` is the balloon's size: what the guest has been *given*, not
      what it is using. For a machine that has never been ballooned this is
      simply its maximum, which is why an unconfigured guest used to sit
      pinned at 100%.
    - `rss` is the host's resident footprint for the whole qemu process,
      guest memory plus emulator overhead, so it can exceed the guest's own
      maximum. A last resort.
    """
    available = raw.get("balloon.available")
    usable = raw.get("balloon.usable")
    if available is not None and usable is not None and available >= usable:
        return float(available - usable)
    current = raw.get("balloon.current")
    if current is not None:
        return float(current)
    return float(raw.get("balloon.rss", 0))

def stat_polling() -> dict:
    """Which statistics the user wants collected (all of them by default)."""
    try:
        from ..pages.settings import stat_polling as _pref

        return _pref()
    except Exception:  # noqa: BLE001 - preferences are optional
        return {"cpu": True, "memory": True, "disk": True, "network": True}


class PollWorker(QThread):
    """Emits snapshots of every machine, on a timer and on libvirt events."""

    updated = Signal(list, HostSnapshot)
    failed = Signal(str)
    xml_changed = Signal(str, str)  # uuid, new persistent XML

    def __init__(self) -> None:
        super().__init__()
        self._stop = False
        self._poke = False
        self._conn: libvirt.virConnect | None = None
        self._prev: dict[str, tuple[float, dict]] = {}  # uuid -> (t, raw stats)
        self._hist: dict[str, deque[Usage]] = {}
        self._host_prev: tuple[float, dict] | None = None
        self._host_hist: deque[Usage] = deque(maxlen=HISTORY_LEN)
        self._xml_hash: dict[str, int] = {}
        self._xml_tick = 0
        # What a machine is, rather than how busy it is. Only changes when
        # someone changes it, and libvirt tells us when that happens, so read
        # once and keep until an event says otherwise.
        self._facts: dict[str, tuple[list[str], list[str], str]] = {}
        self._meta: dict[str, tuple[bool, tuple[str, ...], str]] = {}
        self._flags: dict[str, tuple[bool, bool]] = {}  # autostart, has managed save
        self._balloon_asked: set[str] = set()  # told to report guest memory
        self._host_facts: tuple[str, str, str, int, int] | None = None
        self._rescan_xml = True
        self._pump = EventPump()
        self._watch = DomainWatch()

    def poke(self) -> None:
        """Refresh now, dropping the caches. Something we did changed."""
        self._poke = True
        self._forget_all()

    def _forget_all(self) -> None:
        self._rescan_xml = True
        self._balloon_asked.clear()
        self._facts.clear()
        self._meta.clear()
        self._flags.clear()

    def _forget(self, uuids) -> None:
        if uuids:
            self._rescan_xml = True
        for uuid in uuids:
            self._facts.pop(uuid, None)
            self._meta.pop(uuid, None)
            self._flags.pop(uuid, None)
            self._xml_hash.pop(uuid, None)  # so the config diff sees the change

    @property
    def event_driven(self) -> bool:
        """Whether the host is reporting changes to us."""
        return self._watch.subscribed

    def stop(self) -> None:
        self._stop = True
        self._watch.detach()
        self._pump.stop()
        self.wait(3000)
        # Close explicitly. At interpreter shutdown libvirt's module globals
        # are already gone and its destructor raises into stderr.
        if self._conn is not None:
            try:
                self._conn.close()
            except libvirt.libvirtError:
                pass
            self._conn = None

    # -- stats helpers

    def _enable_balloon_stats(self, uuid: str, dom, raw: dict) -> None:
        """Ask the balloon driver to report what the guest is really using.

        Without a stats period libvirt only knows `balloon.current` - what
        the balloon was told to hold, which is the machine's maximum for a
        guest that has never been ballooned - and `balloon.rss`, the host
        footprint of the whole qemu process. Neither is what the guest is
        using, and the first is why the memory graph sat pinned at the top.

        Set live only: the machine's definition is not ours to rewrite for
        a reading, and a restart just gets asked again on the next tick.
        """
        if "balloon.usable" in raw or "balloon.available" in raw:
            return  # already reporting
        if uuid in self._balloon_asked:
            return  # asked once; a guest with no balloon driver never answers
        self._balloon_asked.add(uuid)
        try:
            dom.setMemoryStatsPeriod(
                BALLOON_STATS_PERIOD, libvirt.VIR_DOMAIN_AFFECT_LIVE
            )
        except libvirt.libvirtError:
            pass  # no balloon device, or a driver that cannot; not fatal

    def _domain_usage(self, uuid: str, raw: dict, now: float, vcpus: int) -> Usage:
        prev = self._prev.get(uuid)
        self._prev[uuid] = (now, raw)
        mem_kb = guest_memory_kb(raw)
        if prev is None:
            return Usage(mem_mb=mem_kb / 1024)
        dt = now - prev[0]
        if dt <= 0:
            return Usage(mem_mb=mem_kb / 1024)
        praw = prev[1]

        cpu_pct = 0.0
        if "cpu.time" in raw and "cpu.time" in praw:
            cpu_ns = raw["cpu.time"] - praw["cpu.time"]
            cpu_pct = max(0.0, min(100.0, cpu_ns / (dt * 1e9 * max(vcpus, 1)) * 100))

        def rate(prefix: str, keys: tuple[str, ...], count_key: str) -> float:
            total = 0.0
            n = raw.get(count_key, 0)
            for i in range(n):
                for k in keys:
                    a = raw.get(f"{prefix}.{i}.{k}")
                    b = praw.get(f"{prefix}.{i}.{k}")
                    if a is not None and b is not None and a >= b:
                        total += a - b
            return total / dt

        disk_bps = rate("block", ("rd.bytes", "wr.bytes"), "block.count")
        net_bps = rate("net", ("rx.bytes", "tx.bytes"), "net.count")
        return Usage(cpu_pct, mem_kb / 1024, disk_bps, net_bps)

    def _host_usage(self, conn: libvirt.virConnect, now: float) -> tuple[float, float]:
        cpu_pct = 0.0
        try:
            stats = conn.getCPUStats(libvirt.VIR_NODE_CPU_STATS_ALL_CPUS)
            if self._host_prev is not None:
                p = self._host_prev[1]
                busy = sum(stats[k] - p[k] for k in ("kernel", "user") if k in stats)
                total = sum(stats[k] - p[k] for k in stats)
                if total > 0:
                    cpu_pct = max(0.0, min(100.0, busy / total * 100))
            self._host_prev = (now, stats)
        except libvirt.libvirtError:
            pass
        mem_used = 0.0
        try:
            m = conn.getMemoryStats(libvirt.VIR_NODE_MEMORY_STATS_ALL_CELLS)
            mem_used = (
                m["total"] - m.get("free", 0) - m.get("buffers", 0) - m.get("cached", 0)
            ) / 1024
        except libvirt.libvirtError:
            pass
        return cpu_pct, mem_used

    @staticmethod
    def _leases(conn: libvirt.virConnect) -> dict[str, str]:
        macs: dict[str, str] = {}
        try:
            for net in conn.listAllNetworks():
                if not net.isActive():
                    continue
                for lease in net.DHCPLeases():
                    if lease.get("mac") and lease.get("ipaddr"):
                        macs[lease["mac"].lower()] = lease["ipaddr"]
        except libvirt.libvirtError:
            pass
        return macs

    OSINFO_NS = "{http://libosinfo.org/xmlns/libvirt/domain/1.0}"

    @classmethod
    def _domain_facts(
        cls, dom: libvirt.virDomain
    ) -> tuple[list[str], list[str], str, list[str]]:
        """(macs, networks, osinfo id, disk paths) from one XML read.

        Disk paths are how a linked clone gets matched to its template: the
        overlay's backing file names the template's image. The chain itself
        lives in the volume XML, not here, so svc_backing_index reads that.
        """
        try:
            root = ET.fromstring(dom.XMLDesc(0))
        except (libvirt.libvirtError, ET.ParseError):
            return [], [], "", []
        macs = [
            m.get("address", "").lower()
            for m in root.findall(".//devices/interface/mac")
        ]
        nets = [
            s.get("network")
            for s in root.findall(".//devices/interface/source")
            if s.get("network")
        ]
        # our wizard and virt-manager both record this
        osinfo_id = ""
        for tag in (f"{cls.OSINFO_NS}os", "os"):
            node = root.find(f"metadata/{cls.OSINFO_NS}libosinfo/{tag}")
            if node is not None and node.get("id"):
                osinfo_id = node.get("id")
                break
        disks = []
        for disk in root.findall("devices/disk"):
            if disk.get("device") != "disk":
                continue
            source = disk.find("source")
            if source is not None and source.get("file"):
                disks.append(source.get("file"))
        return macs, nets, osinfo_id, disks

    # -- main loop

    def _collect(self, conn: libvirt.virConnect) -> tuple[list[DomainSnapshot], HostSnapshot]:
        now = time.monotonic()
        wanted = stat_polling()
        # Always ask for STATE and VCPU: one batched call then covers what
        # used to cost a state() and an info() per machine.
        stats_flags = (
            libvirt.VIR_DOMAIN_STATS_STATE | libvirt.VIR_DOMAIN_STATS_VCPU
        )
        if wanted.get("cpu", True):
            stats_flags |= libvirt.VIR_DOMAIN_STATS_CPU_TOTAL
        if wanted.get("memory", True):
            stats_flags |= libvirt.VIR_DOMAIN_STATS_BALLOON
        if wanted.get("network", True):
            stats_flags |= libvirt.VIR_DOMAIN_STATS_INTERFACE
        if wanted.get("disk", True):
            stats_flags |= libvirt.VIR_DOMAIN_STATS_BLOCK

        # re-read anything an event flagged
        self._forget(self._watch.take_stale())

        domain_stats: list[tuple[libvirt.virDomain, dict]] = []
        try:
            domain_stats = conn.getAllDomainStats(stats_flags)
        except libvirt.libvirtError:
            # not every driver implements it (test:/// among them)
            domain_stats = [(dom, {}) for dom in conn.listAllDomains()]
        leases = self._leases(conn)

        domains: list[DomainSnapshot] = []
        for dom, raw in domain_stats:
            uuid = dom.UUIDString()
            state_name = self._state_of(dom, raw)
            vcpus, memory_mb = self._sizing_of(dom, raw)
            hist = self._hist.setdefault(uuid, deque(maxlen=HISTORY_LEN))
            if state_name == "running" and raw:
                self._enable_balloon_stats(uuid, dom, raw)
                usage = self._domain_usage(uuid, raw, now, vcpus)
                hist.append(usage)
            else:
                usage = Usage()
                self._prev.pop(uuid, None)
                if hist:
                    hist.clear()

            macs, nets, osinfo_id, disks = self._cached_facts(dom, uuid)
            is_template, tags, icon_override = self._cached_meta(dom, uuid)
            autostart, saved = self._cached_flags(dom, uuid, state_name)
            ip = None
            if state_name == "running":
                for mac in macs:
                    if mac in leases:
                        ip = leases[mac]
                        break
            domains.append(
                DomainSnapshot(
                    uuid=uuid,
                    name=dom.name(),
                    state=state_name,
                    vcpus=vcpus,
                    memory_mb=memory_mb,
                    autostart=autostart,
                    ip=ip,
                    usage=usage,
                    history=tuple(hist),
                    has_managed_save=saved,
                    is_template=is_template,
                    tags=tags,
                    networks=tuple(nets),
                    os_key=detect_key(
                        override=icon_override, osinfo_id=osinfo_id, name=dom.name()
                    ),
                    os_icon_override=icon_override,
                    disk_paths=tuple(disks),
                )
            )

        self._watch_config_changes(conn)
        domains.sort(key=lambda d: d.name.lower())

        hostname, hyp, version, cpus, memory_mb = self._cached_host_facts(conn)
        cpu_pct, mem_used = self._host_usage(conn, now)
        self._host_hist.append(Usage(cpu_pct=cpu_pct, mem_mb=mem_used))
        host = HostSnapshot(
            hostname=hostname,
            hypervisor=hyp,
            hypervisor_version=version,
            cpus=cpus,
            memory_mb=memory_mb,
            running=sum(1 for d in domains if d.state == "running"),
            total=len(domains),
            cpu_pct=cpu_pct,
            mem_used_mb=mem_used,
            history=tuple(self._host_hist),
        )
        return domains, host

    # -- prefer the batched stats over per-machine calls

    @staticmethod
    def _state_of(dom: libvirt.virDomain, raw: dict) -> str:
        if "state.state" in raw:
            return STATE_NAMES.get(raw["state.state"], "unknown")
        return STATE_NAMES.get(dom.state()[0], "unknown")

    @staticmethod
    def _sizing_of(dom: libvirt.virDomain, raw: dict) -> tuple[int, int]:
        vcpus = raw.get("vcpu.current")
        memory_kb = raw.get("balloon.current")
        if vcpus is not None and memory_kb is not None:
            return int(vcpus), int(memory_kb) // 1024
        info = dom.info()
        return info[3], info[1] // 1024

    def _cached_facts(
        self, dom: libvirt.virDomain, uuid: str
    ) -> tuple[list[str], list[str], str, list[str]]:
        cached = self._facts.get(uuid)
        if cached is None:
            cached = self._domain_facts(dom)
            self._facts[uuid] = cached
        return cached

    def _cached_meta(
        self, dom: libvirt.virDomain, uuid: str
    ) -> tuple[bool, tuple[str, ...], str]:
        cached = self._meta.get(uuid)
        if cached is None:
            cached = _read_vmm_meta(dom)
            self._meta[uuid] = cached
        return cached

    def _cached_flags(
        self, dom: libvirt.virDomain, uuid: str, state_name: str
    ) -> tuple[bool, bool]:
        cached = self._flags.get(uuid)
        if cached is None:
            try:
                autostart = bool(dom.autostart())
            except libvirt.libvirtError:
                autostart = False
            try:
                saved = state_name == "shutoff" and bool(dom.hasManagedSaveImage(0))
            except libvirt.libvirtError:
                saved = False
            cached = (autostart, saved)
            self._flags[uuid] = cached
        return cached

    def _cached_host_facts(
        self, conn: libvirt.virConnect
    ) -> tuple[str, str, str, int, int]:
        """Hostname, hypervisor and hardware totals don't change under us."""
        if self._host_facts is None:
            node = conn.getInfo()
            self._host_facts = (
                conn.getHostname(), conn.getType(),
                _fmt_version(conn.getVersion()), node[2], node[1],
            )
        return self._host_facts

    def _watch_config_changes(self, conn: libvirt.virConnect) -> None:
        """Report persistent-XML changes for the config-history feature.

        Events normally cover this: an edit from virsh or virt-manager arrives
        as DEFINED/UPDATED. The sweep is a safety net for hosts that report
        nothing, so it runs far less often when events do work.
        """
        self._xml_tick += 1
        every = 30 if self.event_driven else 5
        if self._xml_tick % every != 1 and not self._rescan_xml:
            return
        self._rescan_xml = False
        for dom in conn.listAllDomains():
            try:
                xml = dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
            except libvirt.libvirtError:
                continue
            uuid = dom.UUIDString()
            digest = hash(xml)
            if self._xml_hash.get(uuid) != digest:
                self._xml_hash[uuid] = digest
                self.xml_changed.emit(uuid, xml)

    def run(self) -> None:
        # must be registered before we open the connection that wants events
        ensure_registered()
        self._pump.start()
        conn_uri = ""
        while not self._stop:
            try:
                if self._conn is None or not self._conn.isAlive() or conn_uri != current_uri():
                    if self._conn is not None:
                        self._watch.detach()
                        try:
                            self._conn.close()
                        except libvirt.libvirtError:
                            pass
                        # new host: drop baselines, histories and caches
                        self._prev.clear()
                        self._hist.clear()
                        self._host_prev = None
                        self._host_hist.clear()
                        self._host_facts = None
                        self._xml_hash.clear()
                        self._forget_all()
                    conn_uri = current_uri()
                    self._conn = libvirt.open(conn_uri)
                    self._watch.attach(self._conn)
                    log.info(
                        "connected to %s (%s)", conn_uri,
                        "event driven" if self.event_driven else "polling only",
                    )
                domains, host = self._collect(self._conn)
                self.updated.emit(domains, host)
            except libvirt.libvirtError as e:
                self._conn = None
                self._watch.detach()
                self.failed.emit(str(e))
            self._wait()

    def _wait(self) -> None:
        """Sleep until the interval elapses, an event lands, or we're poked.

        Waking on an event is what makes a state change show up immediately
        instead of up to an interval later.
        """
        deadline = poll_seconds()
        waited = 0.0
        while waited < deadline and not self._stop and not self._poke:
            if self._watch.changed.wait(0.1):
                self._watch.changed.clear()
                break
            waited += 0.1
        self._poke = False
