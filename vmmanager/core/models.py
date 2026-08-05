"""Value objects passed between the service layer and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field

@dataclass(frozen=True)
class Usage:
    cpu_pct: float = 0.0
    mem_mb: float = 0.0
    disk_bps: float = 0.0
    net_bps: float = 0.0

@dataclass(frozen=True)
class DomainSnapshot:
    uuid: str
    name: str
    state: str
    vcpus: int
    memory_mb: int
    autostart: bool
    ip: str | None = None
    usage: Usage = field(default_factory=Usage)
    history: tuple[Usage, ...] = ()
    has_managed_save: bool = False
    is_template: bool = False
    tags: tuple[str, ...] = ()
    networks: tuple[str, ...] = ()
    os_key: str = ""  # resolved OS identity, for the icon and label
    os_icon_override: str = ""  # set when the user pinned it by hand
    disk_paths: tuple[str, ...] = ()  # this machine's own disk images

@dataclass(frozen=True)
class HostSnapshot:
    hostname: str
    hypervisor: str
    hypervisor_version: str
    cpus: int
    memory_mb: int
    running: int
    total: int
    cpu_pct: float = 0.0
    mem_used_mb: float = 0.0
    history: tuple[Usage, ...] = ()

def _fmt_version(v: int) -> str:
    return f"{v // 1_000_000}.{v % 1_000_000 // 1000}.{v % 1000}"

@dataclass(frozen=True)
class DiskInfo:
    dev: str
    bus: str
    source: str
    format: str
    device: str  # disk | cdrom | floppy
    cache: str = "default"

@dataclass(frozen=True)
class NicInfo:
    mac: str
    source: str
    model: str

@dataclass(frozen=True)
class HostdevInfo:
    kind: str  # usb | pci
    ident: str  # usb: "vvvv:pppp", pci: "0000:03:00.0"

@dataclass(frozen=True)
class FsShareInfo:
    tag: str
    source: str
    driver: str  # virtiofs | 9p

@dataclass(frozen=True)
class Hardware:
    machine: str
    firmware: str
    cpu_mode: str
    vcpus: int
    memory_mb: int
    max_memory_mb: int
    disks: tuple[DiskInfo, ...]
    nics: tuple[NicInfo, ...]
    hostdevs: tuple[HostdevInfo, ...]
    filesystems: tuple[FsShareInfo, ...]
    graphics: tuple[tuple[str, str], ...]  # type, port/listen
    video: str
    boot: tuple[str, ...]  # os-level boot devs, or per-device entries
    topology: tuple[int, int, int] | None = None  # sockets, cores, threads
    sounds: tuple[str, ...] = ()
    inputs: tuple[tuple[str, str], ...] = ()  # (type, bus)
    title: str = ""
    description: str = ""
    boot_menu: bool = False
    video_accel3d: bool = False
    watchdog: tuple[str, str] | None = None  # (model, action)
    redirdevs: int = 0  # USB redirection channels
    vsock: str = ""  # cid, or "auto"
    panic: str = ""  # model
    smartcard: str = ""  # mode
    audio: str = ""  # backend type
    memory_devices: tuple[int, ...] = ()  # DIMM sizes in MiB
    controllers: tuple[tuple[str, int, str], ...] = ()  # (type, index, model)

@dataclass(frozen=True)
class SnapshotInfo:
    name: str
    description: str
    created: int
    state: str
    parent: str | None
    current: bool
    external: bool = False

@dataclass(frozen=True)
class VolumeInfo:
    name: str
    path: str
    capacity: int
    allocation: int
    format: str

@dataclass(frozen=True)
class PoolInfo:
    name: str
    active: bool
    autostart: bool
    capacity: int
    allocation: int
    available: int
    path: str
    volumes: tuple[VolumeInfo, ...]

@dataclass(frozen=True)
class LeaseInfo:
    ip: str
    mac: str
    hostname: str
    expires: int

@dataclass(frozen=True)
class NetworkInfo:
    name: str
    active: bool
    autostart: bool
    persistent: bool
    bridge: str
    mode: str
    leases: tuple[LeaseInfo, ...]

@dataclass(frozen=True)
class CloudInit:
    hostname: str
    user: str
    password: str
    ssh_key: str
    packages: tuple[str, ...] = ()

@dataclass(frozen=True)
class CreateSpec:
    name: str
    vcpus: int
    memory_mb: int
    network: str
    uefi: bool = True
    tpm: bool = False
    pool: str = "default"
    disk_gb: float = 40.0
    import_path: str | None = None  # use this image instead of a new volume
    iso_path: str | None = None
    osinfo_id: str = ""
    cloudinit: CloudInit | None = None
    # network install: a distro install tree (http/ftp/nfs). Handed to
    # virt-install, which fetches the kernel and initrd out of it for us.
    location_url: str = ""
    osinfo_short_id: str = ""  # virt-install --osinfo, required for a URL install
    kernel_args: str = ""  # extra <cmdline>, e.g. "console=ttyS0 inst.ks=..."
    # direct kernel boot: run these instead of the firmware's boot order
    kernel_path: str = ""
    initrd_path: str = ""

@dataclass(frozen=True)
class HostDevice:
    kind: str  # usb | pci
    ident: str
    label: str

@dataclass(frozen=True)
class GraphicsInfo:
    type: str  # vnc | spice | …
    host: str
    port: int
    socket: str
    has_password: bool

@dataclass(frozen=True)
class NetworkDef:
    name: str
    mode: str  # nat | isolated | bridge
    subnet: str
    dhcp_start: str
    dhcp_end: str
    bridge_dev: str

@dataclass(frozen=True)
class CheckpointInfo:
    name: str
    created: int
    parent: str | None
    disks: tuple[str, ...]

@dataclass(frozen=True)
class IommuDevice:
    address: str  # 0000:03:00.0
    group: int
    label: str
    driver: str
    is_bridge: bool
    attached_to: str | None  # domain name currently using it

@dataclass(frozen=True)
class IommuReport:
    enabled: bool
    devices: tuple[IommuDevice, ...]

    def group_members(self, group: int) -> tuple[IommuDevice, ...]:
        return tuple(d for d in self.devices if d.group == group)

@dataclass(frozen=True)
class OrphanVolume:
    pool: str
    name: str
    path: str
    capacity: int

@dataclass(frozen=True)
class CompactCandidate:
    pool: str
    name: str
    path: str
    capacity: int
    allocation: int  # bytes the image occupies now
    needed: int  # bytes its referenced data needs (lower bound)
    in_use_by: str | None  # domain name, if any
    running: bool  # that domain is up, so compaction must wait

    @property
    def slack(self) -> int:
        """Space a rewrite is *certain* to reclaim.

        A lower bound only. qemu-img cannot tell that an allocated cluster
        holds nothing but zeros (that needs reading the data), so an image
        whose freed blocks were overwritten rather than discarded often
        shrinks far more than this. The UI says as much, and the actual
        saving is reported once the rewrite finishes.
        """
        return max(0, self.allocation - self.needed)

@dataclass(frozen=True)
class BackingIndex:
    """What every volume is layered on, and how big it is.

    A linked clone is a qcow2 overlay whose backing file is its template's
    image. libvirt records that chain in the volume XML, not the domain XML.
    """

    backing_of: dict[str, str]  # overlay path -> the image beneath it
    capacity_of: dict[str, int]  # virtual size
    allocation_of: dict[str, int]  # what it actually occupies

    def clones_of(self, disk_paths) -> list[str]:
        """Overlay paths layered directly on any of these images."""
        wanted = set(disk_paths)
        return [
            path for path, parent in self.backing_of.items() if parent in wanted
        ]


@dataclass(frozen=True)
class DomainDisk:
    dev: str
    path: str
    capacity_gb: float
