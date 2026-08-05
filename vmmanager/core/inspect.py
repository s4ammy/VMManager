"""Look inside a guest: OS, hostname, and what is installed.

Two routes, because neither covers every case:

* **libguestfs** mounts the machine's disks read-only *without booting it*.
  That is the only way to inspect a machine that is shut off, or one that has
  no agent, and it is what virt-manager uses. It is an optional dependency:
  the python ``guestfs`` bindings ship with libguestfs rather than pip, so its
  absence is reported as something the user can fix, not as an error.
* **The guest agent**, for a running machine. No extra packages on the host,
  and it sees the live system, but it needs qemu-guest-agent inside.

`svc_inspect` picks whichever can answer and says which one it used.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

import libvirt

from .connection import _with_conn
from .guest import _agent_cmd, svc_guest_exec

try:  # pragma: no cover - depends on the host having libguestfs
    import guestfs  # type: ignore

    GUESTFS_AVAILABLE = True
except ImportError:
    GUESTFS_AVAILABLE = False


@dataclass(frozen=True)
class Inspection:
    source: str = ""  # "libguestfs" | "guest agent"
    os_type: str = ""  # linux | windows | …
    distro: str = ""  # debian, fedora, windows …
    version: str = ""
    product_name: str = ""
    hostname: str = ""
    package_format: str = ""
    mountpoints: tuple[tuple[str, str], ...] = ()  # (device, mountpoint)
    applications: tuple[tuple[str, str], ...] = ()  # (name, version)
    note: str = ""

    @property
    def summary(self) -> str:
        parts = [p for p in (self.product_name or self.distro, self.version) if p]
        return " ".join(parts) or self.os_type or "unknown"


def inspection_backends() -> dict[str, bool]:
    """What can inspect on this host right now, for the UI to explain."""
    return {
        "libguestfs": GUESTFS_AVAILABLE,
        "virt-inspector": shutil.which("virt-inspector") is not None,
    }


LIBGUESTFS_HINT = (
    "Offline inspection needs libguestfs with its Python bindings "
    "(Arch: libguestfs; Debian/Fedora: python3-libguestfs). Without it, only "
    "running machines with a guest agent can be inspected."
)


def _disk_paths(conn, uuid: str) -> list[str]:
    import xml.etree.ElementTree as ET

    dom = conn.lookupByUUIDString(uuid)
    root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    paths = []
    for disk in root.findall("devices/disk"):
        if disk.get("device") != "disk":
            continue
        source = disk.find("source")
        if source is not None and source.get("file"):
            paths.append(source.get("file"))
    return paths


def _inspect_offline(paths: list[str], with_apps: bool) -> Inspection:
    """Mount the disks read-only through libguestfs and describe the OS."""
    g = guestfs.GuestFS(python_return_dict=True)
    try:
        for path in paths:
            g.add_drive_opts(path, readonly=1)
        g.launch()
        roots = g.inspect_os()
        if not roots:
            return Inspection(
                source="libguestfs",
                note="No operating system found on these disks.",
            )
        root = roots[0]
        mounts = g.inspect_get_mountpoints(root)
        apps: tuple[tuple[str, str], ...] = ()
        if with_apps:
            # applications need the filesystems actually mounted
            for mountpoint, device in sorted(mounts.items(), key=lambda kv: len(kv[0])):
                try:
                    g.mount_ro(device, mountpoint)
                except RuntimeError:
                    pass
            try:
                apps = tuple(
                    (a.get("app2_name", ""), a.get("app2_version", ""))
                    for a in g.inspect_list_applications2(root)
                )
            except RuntimeError:
                apps = ()
        major = g.inspect_get_major_version(root)
        minor = g.inspect_get_minor_version(root)
        version = f"{major}.{minor}" if minor else str(major)
        return Inspection(
            source="libguestfs",
            os_type=g.inspect_get_type(root),
            distro=g.inspect_get_distro(root),
            version=version,
            product_name=g.inspect_get_product_name(root),
            hostname=g.inspect_get_hostname(root),
            package_format=g.inspect_get_package_format(root),
            mountpoints=tuple((dev, mp) for mp, dev in mounts.items()),
            applications=apps,
        )
    finally:
        try:
            g.close()
        except Exception:  # noqa: BLE001, teardown must not mask a result
            pass


def _inspect_via_agent(uuid: str, with_apps: bool) -> Inspection:
    """Ask the running guest about itself through qemu-guest-agent."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        info = {}
        try:
            info = dom.guestInfo(0, 0)
        except libvirt.libvirtError:
            pass
        os_id = info.get("os.id", "")
        mounts = []
        count = int(info.get("fs.count", 0) or 0)
        for i in range(count):
            mounts.append(
                (info.get(f"fs.{i}.name", "?"), info.get(f"fs.{i}.mountpoint", "?"))
            )
        apps: tuple[tuple[str, str], ...] = ()
        if with_apps:
            listing = ""
            for probe in (
                "command -v dpkg-query >/dev/null && "
                "dpkg-query -W -f='${Package}\\t${Version}\\n'",
                "command -v rpm >/dev/null && rpm -qa --qf '%{NAME}\\t%{VERSION}\\n'",
                "command -v pacman >/dev/null && pacman -Q | tr ' ' '\\t'",
            ):
                try:
                    rc, out, _err = svc_guest_exec(uuid, probe, timeout=90)
                except Exception:  # noqa: BLE001 - agent may lack guest-exec
                    break
                if rc == 0 and out.strip():
                    listing = out
                    break
            rows = []
            for line in listing.splitlines():
                if "\t" in line:
                    name, _, ver = line.partition("\t")
                    rows.append((name.strip(), ver.strip()))
            apps = tuple(sorted(rows))
        return Inspection(
            source="guest agent",
            os_type=info.get("os.type", ""),
            distro=os_id,
            version=info.get("os.version-id", "") or info.get("os.version", ""),
            product_name=info.get("os.pretty-name", ""),
            hostname=info.get("hostname", ""),
            package_format=(
                "deb" if os_id in ("debian", "ubuntu")
                else "rpm" if os_id in ("fedora", "rhel", "centos", "almalinux")
                else ""
            ),
            mountpoints=tuple(mounts),
            applications=apps,
        )

    return _with_conn(go)


def svc_inspect(uuid: str, with_apps: bool = True) -> Inspection:
    """Describe the guest, using whichever backend can answer.

    A running machine is asked directly; that is quick and needs nothing on the
    host. Otherwise its disks are read offline, which needs libguestfs.
    """
    running = _with_conn(lambda c: bool(c.lookupByUUIDString(uuid).isActive()))

    if running:
        try:
            result = _inspect_via_agent(uuid, with_apps)
            if result.hostname or result.product_name or result.distro:
                return result
        except libvirt.libvirtError:
            pass  # no agent; fall through to the offline path

    if not GUESTFS_AVAILABLE:
        raise RuntimeError(
            ("The guest agent did not answer. " if running else "")
            + LIBGUESTFS_HINT
        )
    paths = _with_conn(lambda c: _disk_paths(c, uuid))
    if not paths:
        raise RuntimeError("This machine has no file-backed disks to inspect")
    if running:
        # libguestfs opens the images read-only, but a running guest is still
        # writing to them, so anything we read may be a torn snapshot.
        result = _inspect_offline(paths, with_apps)
        return Inspection(
            **{**result.__dict__, "note": (
                "Read from the disks of a running machine, so this may be "
                "slightly out of date."
            )}
        )
    return _inspect_offline(paths, with_apps)
