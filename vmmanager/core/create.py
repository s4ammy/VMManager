"""Machine creation: spec, cloud-init seed, and the domain XML we build."""

from __future__ import annotations

from pathlib import Path
import subprocess

import libvirt

from .connection import _with_conn, current_uri
from .devices import _detect_format
from .models import CloudInit, CreateSpec
from .xmlesc import x

def _build_seed_iso(name: str, ci: CloudInit) -> bytes:
    """NoCloud seed ISO (volid 'cidata') built with xorrisofs."""
    import tempfile
    from pathlib import Path

    users = f"""  - name: {ci.user}
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: [wheel, sudo]
    shell: /bin/bash
    lock_passwd: false
"""
    if ci.ssh_key.strip():
        users += f"""    ssh_authorized_keys:
      - {ci.ssh_key.strip()}
"""
    chpasswd = ""
    if ci.password:
        chpasswd = f"""chpasswd:
  expire: false
  users:
    - {{name: {ci.user}, password: {ci.password}, type: text}}
ssh_pwauth: true
"""
    packages = ""
    if ci.packages:
        packages = "packages:\n" + "".join(f"  - {p}\n" for p in ci.packages)
    user_data = f"""#cloud-config
hostname: {ci.hostname or name}
users:
{users}{chpasswd}{packages}"""
    meta_data = f"instance-id: {name}\nlocal-hostname: {ci.hostname or name}\n"

    with tempfile.TemporaryDirectory(prefix="vmm-seed-") as tmp:
        (Path(tmp) / "user-data").write_text(user_data)
        (Path(tmp) / "meta-data").write_text(meta_data)
        iso_path = Path(tmp) / "seed.iso"
        result = subprocess.run(
            [
                "xorrisofs", "-output", str(iso_path), "-volid", "cidata",
                "-joliet", "-rational-rock", "user-data", "meta-data",
            ],
            cwd=tmp,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "xorrisofs failed")
        return iso_path.read_bytes()

def svc_create_vm_from_url(spec: CreateSpec) -> str:
    """Install from a distro install tree over the network.

    Fetching the kernel and initrd out of a tree, matching them to the distro
    and passing the right kernel arguments is exactly what virt-install already
    does well, so we drive it rather than reimplement it. It runs detached and
    leaves a defined, booting domain behind.
    """
    import shutil

    if shutil.which("virt-install") is None:
        raise RuntimeError("virt-install is not installed")
    if not spec.location_url:
        raise RuntimeError("No install tree URL given")

    argv = [
        "virt-install",
        "--connect", current_uri(),
        "--name", spec.name,
        "--memory", str(spec.memory_mb),
        "--vcpus", str(spec.vcpus),
        "--location", spec.location_url,
        "--network", f"network={spec.network},model=virtio",
        "--graphics", "vnc,listen=127.0.0.1",
        "--video", "virtio",
        "--console", "pty,target_type=serial",
        "--channel", "unix,target.type=virtio,target.name=org.qemu.guest_agent.0",
        "--noautoconsole",
        "--wait", "-1",
    ]
    if spec.osinfo_short_id:
        argv += ["--osinfo", spec.osinfo_short_id]
    else:
        argv += ["--osinfo", "detect=on,require=off"]
    if spec.uefi:
        argv += ["--boot", "uefi"]
    if spec.tpm:
        argv += ["--tpm", "backend.type=emulator,backend.version=2.0,model=tpm-crb"]
    if spec.import_path:
        argv += ["--disk", f"path={spec.import_path}"]
    else:
        argv += [
            "--disk",
            f"pool={spec.pool},size={max(1, int(spec.disk_gb))},format=qcow2,bus=virtio",
        ]
    if spec.kernel_args:
        argv += ["--extra-args", spec.kernel_args]

    # virt-install stays attached for the whole install, so run it detached and
    # let the poller show the machine appearing.
    subprocess.Popen(
        argv, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return (
        f"Installing {spec.name} from {spec.location_url} - it will appear "
        "shortly and boot into the installer."
    )


def svc_create_vm(spec: CreateSpec) -> None:
    if spec.location_url:
        svc_create_vm_from_url(spec)
        return
    seed_data = (
        _build_seed_iso(spec.name, spec.cloudinit) if spec.cloudinit else None
    )

    def go(conn):
        try:
            conn.lookupByName(spec.name)
            raise RuntimeError(f"A machine named '{spec.name}' already exists")
        except libvirt.libvirtError:
            pass

        created_vol = None
        if spec.import_path:
            disk_path = spec.import_path
            disk_fmt = _detect_format(conn, disk_path)
        else:
            pool = conn.storagePoolLookupByName(spec.pool)
            size = int(spec.disk_gb * 1024**3)
            created_vol = pool.createXML(
                f"""<volume>
  <name>{x(spec.name)}.qcow2</name>
  <capacity unit='bytes'>{size}</capacity>
  <target><format type='qcow2'/></target>
</volume>""",
                0,
            )
            disk_path = created_vol.path()
            disk_fmt = "qcow2"

        firmware = " firmware='efi'" if spec.uefi else ""
        nvram = ""
        # direct kernel boot bypasses the firmware entirely
        direct_boot = ""
        if spec.kernel_path:
            direct_boot = f"<kernel>{x(spec.kernel_path)}</kernel>"
            if spec.initrd_path:
                direct_boot += f"<initrd>{x(spec.initrd_path)}</initrd>"
            if spec.kernel_args:
                direct_boot += f"<cmdline>{x(spec.kernel_args)}</cmdline>"
        cdroms = ""
        boot_cd = ""
        if spec.iso_path:
            cdroms += f"""
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{x(spec.iso_path)}'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>"""
            boot_cd = "<boot dev='cdrom'/>"
        seed_path = None
        if seed_data is not None:
            # park the seed in the target pool so permissions just work; a
            # virtio disk, not a SATA cdrom - minimal cloud kernels (Debian
            # cloud-amd64 et al.) ship without AHCI and would never see it
            seed_path = svc_upload_volume_conn(conn, spec.pool, f"{spec.name}-seed.iso", seed_data)
            cdroms += f"""
    <disk type='file' device='disk'>
      <driver name='qemu' type='raw'/>
      <source file='{x(seed_path)}'/>
      <target dev='vdz' bus='virtio'/>
      <readonly/>
    </disk>"""
        tpm = ""
        if spec.tpm:
            tpm = "<tpm model='tpm-crb'><backend type='emulator' version='2.0'/></tpm>"
        metadata = ""
        if spec.osinfo_id:
            metadata = f"""
  <metadata>
    <libosinfo:libosinfo xmlns:libosinfo="http://libosinfo.org/xmlns/libvirt/domain/1.0">
      <libosinfo:os id="{x(spec.osinfo_id)}"/>
    </libosinfo:libosinfo>
  </metadata>"""

        # root ports beyond what the base devices use, so disks/NICs/hostdevs
        # can hot-plug without a reboot
        root_ports = "\n".join(
            f"    <controller type='pci' index='{i}' model='pcie-root-port'/>"
            for i in range(1, 13)
        )
        xml = f"""<domain type='kvm'>
  <name>{x(spec.name)}</name>{metadata}
  <memory unit='MiB'>{spec.memory_mb}</memory>
  <vcpu>{spec.vcpus}</vcpu>
  <os{firmware}>
    <type arch='x86_64' machine='q35'>hvm</type>
    {nvram}
    {direct_boot}
    {boot_cd}<boot dev='hd'/>
  </os>
  <features><acpi/><apic/></features>
  <cpu mode='host-passthrough'/>
  <clock offset='utc'/>
  <devices>
    <controller type='pci' index='0' model='pcie-root'/>
{root_ports}
    <disk type='file' device='disk'>
      <driver name='qemu' type='{x(disk_fmt)}' discard='unmap'/>
      <source file='{x(disk_path)}'/>
      <target dev='vda' bus='virtio'/>
    </disk>{cdroms}
    <interface type='network'>
      <source network='{x(spec.network)}'/>
      <model type='virtio'/>
    </interface>
    <graphics type='vnc' autoport='yes' listen='127.0.0.1'/>
    <video><model type='virtio'/></video>
    <input type='tablet' bus='usb'/>
    <console type='pty'/>
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>
    {tpm}
    <memballoon model='virtio'/>
    <rng model='virtio'><backend model='random'>/dev/urandom</backend></rng>
  </devices>
</domain>"""
        try:
            conn.defineXML(xml)
        except libvirt.libvirtError:
            if created_vol is not None:
                created_vol.delete(0)
            raise

    _with_conn(go)

def svc_upload_volume_conn(conn, pool_name: str, name: str, data: bytes, fmt: str = "raw") -> str:
    """Same as svc_upload_volume but reusing an open connection."""
    pool = conn.storagePoolLookupByName(pool_name)
    try:
        pool.storageVolLookupByName(name).delete(0)
    except libvirt.libvirtError:
        pass
    vol = pool.createXML(
        f"""<volume>
  <name>{x(name)}</name>
  <capacity unit='bytes'>{len(data)}</capacity>
  <target><format type='{x(fmt)}'/></target>
</volume>""",
        0,
    )
    stream = conn.newStream()
    vol.upload(stream, 0, len(data))
    sent = 0
    try:
        while sent < len(data):
            sent += stream.send(data[sent : sent + 262144])
        stream.finish()
    except libvirt.libvirtError:
        stream.abort()
        vol.delete(0)
        raise
    return vol.path()
