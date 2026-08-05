"""CPU pinning arithmetic, cpuset formatting, and reading tuning back.

Tests name the layout mode explicitly rather than relying on the default, since
the default is a judgement call that has already changed once: pairing sibling
threads now wins over spreading across cores, because it leaves the host whole
cores and can be described to the guest.
"""

from __future__ import annotations

import pytest

from vmmanager.core.tuning import (
    HostCpu,
    HostTopology,
    HugePagePool,
    _parse_cpuset,
    auto_pin,
    emulator_cpus,
    format_cpuset,
)


def topology(cores: int, threads: int = 2, sockets: int = 1) -> HostTopology:
    """A host where core N owns logical cpus N and N+cores, as x86 reports."""
    cpus = []
    for thread in range(threads):
        for core in range(cores):
            cpu_id = core + thread * cores
            cpus.append(HostCpu(
                id=cpu_id, socket=core * sockets // max(cores, 1), core=core,
                siblings=tuple(core + t * cores for t in range(threads)), cell=0,
            ))
    return HostTopology(
        sockets=sockets, cores=cores, threads=threads,
        cpus=tuple(sorted(cpus, key=lambda c: c.id)), cells=1,
        hugepages=(HugePagePool(2048, 0, 0), HugePagePool(1048576, 32, 32)),
    )


# ---------------------------------------------------------------- cpusets


@pytest.mark.parametrize("text,expected", [
    ("2", (2,)),
    ("2,10", (2, 10)),
    ("0-3", (0, 1, 2, 3)),
    ("0-3,8", (0, 1, 2, 3, 8)),
    ("8,0-2", (0, 1, 2, 8)),
    ("", ()),
    ("nonsense", ()),
    ("2, 10 ", (2, 10)),
    ("2,2,2", (2,)),
])
def test_cpuset_parsing(text, expected):
    assert _parse_cpuset(text) == expected


@pytest.mark.parametrize("cpus,expected", [
    ((2,), "2"),
    ((2, 10), "2,10"),
    ((0, 1, 2, 3), "0-3"),
    ((0, 1, 2, 3, 8), "0-3,8"),
    ((0, 1, 3, 4, 5, 9), "0-1,3-5,9"),
    ((), ""),
])
def test_cpuset_formatting(cpus, expected):
    assert format_cpuset(cpus) == expected


def test_cpuset_round_trips():
    for text in ("0-3,8", "2", "1,3,5", "0-15"):
        assert format_cpuset(_parse_cpuset(text)) == text


# ---------------------------------------------------------------- topology


def test_physical_cores_group_sibling_threads():
    cores = topology(8).physical_cores()
    assert len(cores) == 8
    assert cores[0] == (0, 8), "cpu 0 and 8 are one core on this layout"
    assert cores[7] == (7, 15)


def test_a_host_without_threads_has_one_cpu_per_core():
    assert topology(4, threads=1).physical_cores() == [(0,), (1,), (2,), (3,)]


# ---------------------------------------------------------------- auto pinning


def test_per_core_mode_gives_every_vcpu_its_own_core():
    """The alternative to pairing: no two vCPUs contend for one core."""
    pins = auto_pin(4, topology(8), "cores")
    chosen = [cpus[0] for _v, cpus in sorted(pins.items())]
    cores = {cpu % 8 for cpu in chosen}
    assert len(cores) == 4, f"expected 4 distinct cores, got {chosen}"


def test_the_hosts_first_core_is_left_free_when_there_is_room():
    pins = auto_pin(4, topology(8), "cores")
    used = {cpu for cpus in pins.values() for cpu in cpus}
    assert 0 not in used and 8 not in used


def test_the_first_core_is_used_when_every_core_is_needed():
    pins = auto_pin(8, topology(8), "cores")
    used = {cpu for cpus in pins.values() for cpu in cpus}
    assert 0 in used
    assert len(pins) == 8


def test_more_vcpus_than_cores_pairs_siblings():
    pins = auto_pin(12, topology(8), "cores")
    assert len(pins) == 12
    used = sorted(cpu for cpus in pins.values() for cpu in cpus)
    assert len(set(used)) == 12, "each vCPU should get a distinct logical cpu"


def test_more_vcpus_than_logical_cpus_still_pins_them_all():
    """Oversubscribed, but every vCPU must still get an answer."""
    pins = auto_pin(20, topology(8), "cores")
    assert len(pins) == 20
    assert all(cpus for cpus in pins.values())


def test_no_vcpus_means_no_pinning():
    assert auto_pin(0, topology(8)) == {}


def test_a_host_with_no_cpus_reported_pins_nothing():
    empty = HostTopology(1, 1, 1, (), 1, ())
    assert auto_pin(4, empty) == {}
    assert emulator_cpus(empty) == ()


# ---------------------------------------------------------------- emulator


def test_the_emulator_gets_a_core_the_guest_is_not_on():
    host = topology(8)
    pins = auto_pin(4, host)
    emulator = emulator_cpus(host, pins)
    guest = {cpu for cpus in pins.values() for cpu in cpus}
    assert emulator
    assert not (set(emulator) & guest)


def test_the_emulator_falls_back_to_unused_sibling_threads():
    """With one vCPU per core there is no free core, but siblings are idle."""
    host = topology(8)
    pins = auto_pin(8, host, "cores")
    emulator = emulator_cpus(host, pins)
    guest = {cpu for cpus in pins.values() for cpu in cpus}
    assert emulator, "idle siblings are better than leaving it unpinned"
    assert not (set(emulator) & guest)


def test_the_emulator_is_left_unpinned_when_nothing_is_free():
    host = topology(8)
    pins = auto_pin(16, host, "cores")
    assert emulator_cpus(host, pins) == ()


# ---------------------------------------------------------------- round trip


def test_pinning_reads_back_as_written(testconn, domain):
    from vmmanager.libvirt_service import svc_get_tuning, svc_set_cpu_pinning

    uuid = domain.UUIDString()
    svc_set_cpu_pinning(uuid, {0: (2,), 1: (3, 11)}, emulator=(0, 8))
    tuning = svc_get_tuning(uuid)
    assert tuning.vcpu_pins == {0: (2,), 1: (3, 11)}
    assert tuning.emulator_pin == (0, 8)
    assert tuning.pinned


def test_clearing_pinning_removes_the_whole_element(testconn, domain):
    import xml.etree.ElementTree as ET

    import libvirt

    from vmmanager.libvirt_service import svc_get_tuning, svc_set_cpu_pinning

    uuid = domain.UUIDString()
    svc_set_cpu_pinning(uuid, {0: (2,)})
    svc_set_cpu_pinning(uuid, {})
    assert svc_get_tuning(uuid).vcpu_pins == {}
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    assert root.find("cputune") is None


def test_hugepage_backing_reads_back(testconn, domain):
    from vmmanager.libvirt_service import svc_get_tuning, svc_set_hugepages

    uuid = domain.UUIDString()
    svc_set_hugepages(uuid, 1048576)
    assert svc_get_tuning(uuid).hugepage_size_kb == 1048576

    svc_set_hugepages(uuid, 0)
    assert svc_get_tuning(uuid).hugepage_size_kb == 0


def test_iothreads_read_back_and_clear(testconn, domain):
    from vmmanager.libvirt_service import svc_get_tuning, svc_set_iothreads

    uuid = domain.UUIDString()
    svc_set_iothreads(uuid, 3)
    assert svc_get_tuning(uuid).iothreads == 3

    svc_set_iothreads(uuid, 0)
    assert svc_get_tuning(uuid).iothreads == 0


def test_turning_iothreads_off_unhooks_the_disks(testconn, domain):
    """A disk pointing at a thread that no longer exists will not start."""
    import xml.etree.ElementTree as ET

    import libvirt

    from vmmanager.libvirt_service import svc_set_iothreads

    svc_set_iothreads(domain.UUIDString(), 2)
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    for driver in root.findall("devices/disk/driver"):
        driver.set("iothread", "1")
    testconn.defineXML(ET.tostring(root, encoding="unicode"))

    svc_set_iothreads(domain.UUIDString(), 0)
    root = ET.fromstring(
        testconn.lookupByName("test").XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
    )
    assert not [d for d in root.findall("devices/disk/driver") if d.get("iothread")]


def test_flattening_a_machine_that_is_off_says_why(testconn, domain):
    """blockPull streams while the guest runs; refusing early beats a libvirt
    error nobody can act on."""
    from vmmanager.libvirt_service import svc_flatten_disk

    domain.destroy()
    with pytest.raises(RuntimeError, match="running"):
        svc_flatten_disk(domain.UUIDString(), "vda")


# ---------------------------------------------------------------- guest topology


def test_paired_mode_puts_two_vcpus_on_each_core():
    from vmmanager.core.tuning import PIN_PAIRED

    host = topology(8)
    pins = auto_pin(8, host, PIN_PAIRED)
    core_of = {cpu: i for i, core in enumerate(host.physical_cores()) for cpu in core}
    per_core: dict[int, int] = {}
    for cpus in pins.values():
        per_core[core_of[cpus[0]]] = per_core.get(core_of[cpus[0]], 0) + 1
    assert set(per_core.values()) == {2}
    assert len(per_core) == 4, "8 vCPUs paired should occupy 4 cores"


def test_paired_mode_leaves_whole_cores_for_the_host():
    """The point of pairing: the host keeps cores rather than scraps."""
    from vmmanager.core.tuning import PIN_PAIRED

    host = topology(8)
    pins = auto_pin(12, host, PIN_PAIRED)
    used = {cpu for cpus in pins.values() for cpu in cpus}
    free_cores = [c for c in host.physical_cores() if not (set(c) & used)]
    assert len(free_cores) == 2, f"expected 2 whole cores spare, got {free_cores}"


def test_paired_mode_falls_back_when_the_count_is_odd():
    """Five vCPUs cannot be paired, so it must still return a full mapping."""
    from vmmanager.core.tuning import PIN_PAIRED

    pins = auto_pin(5, topology(8), PIN_PAIRED)
    assert len(pins) == 5


@pytest.mark.parametrize("vcpus,mode,expected", [
    (8, "paired", (1, 4, 2)),
    (12, "paired", (1, 6, 2)),
    (2, "paired", (1, 1, 2)),
    (4, "cores", (1, 4, 1)),
    (8, "cores", (1, 8, 1)),
])
def test_guest_topology_describes_the_pinning(vcpus, mode, expected):
    from vmmanager.core.tuning import guest_topology_for

    host = topology(8)
    assert guest_topology_for(auto_pin(vcpus, host, mode), host) == expected


def test_a_lopsided_layout_has_no_guest_topology():
    """12 vCPUs one-per-core over 8 cores doubles up 4 of them; that cannot be
    expressed as sockets x cores x threads, so say so rather than lie."""
    from vmmanager.core.tuning import guest_topology_for

    host = topology(8)
    pins = auto_pin(12, host, "cores")
    assert guest_topology_for(pins, host) is None


def test_a_vcpu_pinned_to_several_cpus_has_no_guest_topology():
    from vmmanager.core.tuning import guest_topology_for

    host = topology(8)
    assert guest_topology_for({0: (1, 9), 1: (2,)}, host) is None


def test_no_pinning_means_no_guest_topology():
    from vmmanager.core.tuning import guest_topology_for

    assert guest_topology_for({}, topology(8)) is None


def test_the_product_of_the_guest_topology_is_the_vcpu_count():
    """libvirt rejects a topology whose product is not the vCPU count."""
    from vmmanager.core.tuning import guest_topology_for

    host = topology(8)
    for vcpus in (2, 4, 6, 8, 12, 16):
        pins = auto_pin(vcpus, host, "paired")
        topo = guest_topology_for(pins, host)
        if topo is not None:
            sockets, cores, threads = topo
            assert sockets * cores * threads == vcpus


# ---------------------------------------------------------------- topology modes


@pytest.fixture
def tuning_dialog(qapp):
    """The dialog against the synthetic 8-core, 2-thread host defined above.

    libvirt's test driver reports 16 CPUs but each as its own single-thread
    core, so sibling pairing never engages there and the paths that depend on
    it would go untested.
    """
    from vmmanager.core.tuning import Tuning
    from vmmanager.dialogs import TuningDialog

    def build(vcpus: int = 8, guest=(1, 8, 1)):
        return TuningDialog(None, "probe", vcpus, topology(8), Tuning(),
                            (), guest_topology=guest)

    return build


def test_keep_mode_changes_nothing(tuning_dialog):
    d = tuning_dialog()
    d._autofill()
    d.topology_mode.setCurrentIndex(0)
    assert d.guest_topology() is None
    assert d.topology_problem() is None


def test_auto_mode_follows_the_pinning(tuning_dialog):
    from vmmanager.core.tuning import guest_topology_for

    d = tuning_dialog()
    d._autofill()
    d.topology_mode.setCurrentIndex(1)
    assert d.guest_topology() == guest_topology_for(d.pins(), d._topology)


def test_auto_mode_yields_nothing_without_pinning(tuning_dialog):
    d = tuning_dialog()
    d.topology_mode.setCurrentIndex(1)
    assert d.guest_topology() is None
    assert "Nothing to match" in d.topology_note.text()


def test_manual_mode_returns_what_was_typed(tuning_dialog):
    d = tuning_dialog(vcpus=8)
    d.topology_mode.setCurrentIndex(2)
    d.sockets.setValue(2)
    d.cores.setValue(2)
    d.threads.setValue(2)
    assert d.guest_topology() == (2, 2, 2)
    assert d.topology_problem() is None


def test_manual_mode_starts_from_the_machines_current_topology(tuning_dialog):
    d = tuning_dialog(vcpus=8, guest=(2, 2, 2))
    assert d.manual_topology() == (2, 2, 2)


def test_manual_mode_refuses_a_topology_that_misses_vcpus(tuning_dialog):
    """Not because libvirt rejects it - it does not. svc_set_cpu sets the vCPU
    count to the product, so a mismatch here would quietly turn an 8-vCPU
    machine into a 12-vCPU one. Verified against a real machine."""
    d = tuning_dialog(vcpus=8)
    d.topology_mode.setCurrentIndex(2)
    d.sockets.setValue(2)
    d.cores.setValue(3)
    d.threads.setValue(2)   # 12, not 8
    problem = d.topology_problem()
    assert problem and "12 vCPUs" in problem
    assert "change the vCPU count" in problem


def test_manual_mode_warns_when_it_contradicts_the_pinning(tuning_dialog):
    """Allowed - the user may mean it - but it must not pass silently."""
    d = tuning_dialog(vcpus=8)
    d._autofill()                      # pairs, so 4 cores of 2 threads
    d.topology_mode.setCurrentIndex(2)
    d.sockets.setValue(1)
    d.cores.setValue(8)
    d.threads.setValue(1)              # says no sharing at all
    assert d.topology_problem() is None
    assert "disagrees with the pinning" in d.topology_note.text()


def test_manual_mode_agreeing_with_the_pinning_says_nothing_alarming(tuning_dialog):
    d = tuning_dialog(vcpus=8)
    d._autofill()
    d.topology_mode.setCurrentIndex(2)
    d.sockets.setValue(1)
    d.cores.setValue(4)
    d.threads.setValue(2)
    assert d.topology_problem() is None
    assert "disagrees" not in d.topology_note.text()


def test_the_manual_fields_only_show_in_manual_mode(tuning_dialog, qapp):
    d = tuning_dialog()
    d.show()
    qapp.processEvents()
    d.topology_mode.setCurrentIndex(1)
    assert not d._manual_row.isVisible()
    d.topology_mode.setCurrentIndex(2)
    assert d._manual_row.isVisible()
