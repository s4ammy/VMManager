"""Connection settings, constants, and the short-lived-connection helper."""

from __future__ import annotations

from pathlib import Path

import libvirt

libvirt.registerErrorHandler(lambda _ctx, _err: None, None)

DEFAULT_URI = "qemu:///system"

HISTORY_LEN = 120  # 4 minutes at 2s

_uri = DEFAULT_URI

_poll_seconds = 2.0

def current_uri() -> str:
    return _uri

def set_uri(uri: str) -> None:
    global _uri
    _uri = uri or DEFAULT_URI

def poll_seconds() -> float:
    return _poll_seconds

def set_poll_seconds(seconds: float) -> None:
    global _poll_seconds
    _poll_seconds = max(0.5, min(60.0, seconds))

STATE_NAMES = {
    libvirt.VIR_DOMAIN_NOSTATE: "unknown",
    libvirt.VIR_DOMAIN_RUNNING: "running",
    libvirt.VIR_DOMAIN_BLOCKED: "blocked",
    libvirt.VIR_DOMAIN_PAUSED: "paused",
    libvirt.VIR_DOMAIN_SHUTDOWN: "shutting-down",
    libvirt.VIR_DOMAIN_SHUTOFF: "shutoff",
    libvirt.VIR_DOMAIN_CRASHED: "crashed",
    libvirt.VIR_DOMAIN_PMSUSPENDED: "suspended",
}

VMM_NS = "http://vmmanager/xmlns/1.0"

def _with_conn(fn):
    conn = libvirt.open(current_uri())
    try:
        return fn(conn)
    finally:
        conn.close()

KEY_COMBOS: dict[str, list[int]] = {
    "Ctrl+Alt+Del": [29, 56, 111],
    "Ctrl+Alt+Backspace": [29, 56, 14],
    "Ctrl+Alt+F1": [29, 56, 59],
    "Ctrl+Alt+F2": [29, 56, 60],
    "Ctrl+Alt+F3": [29, 56, 61],
    "Ctrl+Alt+F7": [29, 56, 65],
    "PrintScreen": [99],
}

MIN_COMPACT_SIZE = 64 * 1024**2  # ignore images too small to bother with

COMPACT_TMP = Path.home() / ".cache" / "vmmanager" / "compact"


# -- connection URIs
#
# libvirt drivers we can reasonably drive from this UI. "session" means the
# per-user QEMU instance: no root, but its networking is limited to user-mode
# and its storage lives under the user's home.

HYPERVISORS = {
    "qemu-system": {
        "label": "QEMU/KVM (system)",
        "scheme": "qemu", "path": "/system",
        "note": "The usual choice: system-wide machines, needs libvirt group.",
    },
    "qemu-session": {
        "label": "QEMU/KVM (user session)",
        "scheme": "qemu", "path": "/session",
        "note": "Your own machines, no root. User-mode networking only.",
    },
    "xen": {
        "label": "Xen", "scheme": "xen", "path": "/",
        "note": "Connects to a Xen host's hypervisor.",
    },
    "lxc": {
        "label": "LXC (libvirt containers)", "scheme": "lxc", "path": "/",
        "note": "Libvirt's own container driver, not Docker or LXD.",
    },
    "bhyve": {
        "label": "Bhyve (FreeBSD)", "scheme": "bhyve", "path": "/system",
        "note": "For a FreeBSD host running bhyve.",
    },
    "vz": {
        "label": "Virtuozzo", "scheme": "vz", "path": "/system",
        "note": "Virtuozzo/OpenVZ containers and machines.",
    },
}


def build_uri(
    hypervisor: str, host: str = "", user: str = "",
    transport: str = "ssh", keyfile: str = "",
) -> str:
    """Assemble a libvirt URI from the pieces the connection dialog collects."""
    spec = HYPERVISORS.get(hypervisor)
    if spec is None:
        raise ValueError(f"unknown hypervisor {hypervisor}")
    scheme, path = spec["scheme"], spec["path"]
    if not host:
        return f"{scheme}://{path}" if path.startswith("//") else f"{scheme}://{path}"
    prefix = f"{scheme}+{transport}" if transport else scheme
    userpart = f"{user}@" if user else ""
    query = f"?keyfile={keyfile}" if keyfile and transport == "ssh" else ""
    return f"{prefix}://{userpart}{host}{path}{query}"


def svc_probe_uri(uri: str) -> str:
    """Open a URI once and report what answered, for the connection dialog."""
    conn = libvirt.open(uri)
    try:
        from .models import _fmt_version

        return (
            f"{conn.getType()} {_fmt_version(conn.getVersion())} on "
            f"{conn.getHostname()} - {len(conn.listAllDomains())} machine(s)"
        )
    finally:
        conn.close()
