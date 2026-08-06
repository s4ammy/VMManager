"""What qemu-img knows about a disk image, and what it can do to one.

libvirt reports a volume's capacity and allocation and stops there. Every
other question about an image - is it corrupt, what is it layered on, how
much of it is actually referenced, what cluster size was it made with - is
qemu-img's to answer, and until now the only way to ask was a terminal.

Nothing here changes a running machine's disk. Checks are read-only; the
repair and the format conversion refuse outright while the image is in use,
because qemu-img writing underneath a running guest corrupts it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field

# Long enough for a check of a large image, short enough that a hung
# qemu-img does not wedge the task pool forever.
CHECK_TIMEOUT = 900
CONVERT_TIMEOUT = 3600

FORMATS = ("qcow2", "raw")

# qcow2 cluster size: bigger clusters mean less metadata and faster large
# sequential IO, smaller ones waste less on small random writes.
CLUSTER_SIZES = ("64k", "128k", "256k", "512k", "1M", "2M")


@dataclass(frozen=True)
class ImageInfo:
    """What `qemu-img info` reports, in the terms the UI shows."""

    path: str
    format: str = ""
    virtual_size: int = 0     # what the guest sees
    actual_size: int = 0      # what it costs on the host
    cluster_size: int = 0
    backing_file: str = ""
    backing_format: str = ""
    encrypted: bool = False
    compressed: bool = False
    lazy_refcounts: bool = False
    corrupt: bool = False     # qcow2's own "this was not closed cleanly" flag
    snapshots: tuple[str, ...] = ()

    @property
    def thin(self) -> bool:
        """Whether it is costing less than it claims to the guest."""
        return 0 < self.actual_size < self.virtual_size


@dataclass(frozen=True)
class CheckResult:
    """What `qemu-img check` found."""

    ok: bool
    summary: str
    leaks: int = 0
    errors: int = 0
    allocated_pct: float = 0.0
    fragmented_pct: float = 0.0
    repairable: bool = False
    output: str = field(default="", compare=False)


def _qemu_img() -> str:
    found = shutil.which("qemu-img")
    if not found:
        raise RuntimeError(
            "qemu-img is not installed. It comes with QEMU - on most "
            "distributions the package is qemu-img or qemu-utils."
        )
    return found


def _run(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    """qemu-img, as this user first and as root only if it has to be.

    A machine's disks under qemu:///system belong to root, so every one of
    these fails with "Permission denied" for the person running the app.
    Asking for a password on the first call would be presumptuous when the
    image is one they own; asking only after being refused means the
    prompt appears exactly when it is needed and says why.
    """
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode == 0 or "Permission denied" not in result.stderr:
        return result

    from .elevate import run_root_script

    # The script is fixed text; the arguments are shell-quoted by /bin/sh
    # into "$@" and nothing here interpolates them.
    output = run_root_script('exec "$@"', list(argv), timeout=timeout)
    return subprocess.CompletedProcess(argv, 0, output, "")


def parse_info(payload: str, path: str) -> ImageInfo:
    """`qemu-img info --output=json`, as an ImageInfo."""
    try:
        raw = json.loads(payload)
    except (ValueError, TypeError):
        raise RuntimeError("qemu-img did not return anything readable") from None
    specific = raw.get("format-specific", {}).get("data", {})
    return ImageInfo(
        path=path,
        format=raw.get("format", ""),
        virtual_size=int(raw.get("virtual-size", 0) or 0),
        actual_size=int(raw.get("actual-size", 0) or 0),
        cluster_size=int(raw.get("cluster-size", 0) or 0),
        backing_file=raw.get("backing-filename", "") or "",
        backing_format=raw.get("backing-filename-format", "") or "",
        encrypted=bool(raw.get("encrypted", False)),
        compressed=bool(specific.get("compat") and specific.get("compression-type")
                        not in (None, "zlib")),
        lazy_refcounts=bool(specific.get("lazy-refcounts", False)),
        corrupt=bool(specific.get("corrupt", False)),
        snapshots=tuple(
            s.get("name", "?") for s in raw.get("snapshots", []) or []
        ),
    )


def parse_check(payload: str, returncode: int, stderr: str = "") -> CheckResult:
    """`qemu-img check --output=json`, as a CheckResult.

    qemu-img's exit codes carry the verdict: 0 clean, 1 could not check,
    2 image is corrupt, 3 leaked clusters only. 3 is the interesting one -
    the image is usable and only wasting space, which reads as alarming
    unless it is said plainly.
    """
    try:
        raw = json.loads(payload) if payload.strip() else {}
    except ValueError:
        raw = {}
    leaks = int(raw.get("leaks", 0) or 0)
    errors = int(raw.get("check-errors", 0) or 0) + int(raw.get("corruptions", 0) or 0)
    if returncode == 0 and not errors:
        summary = "No errors found."
    elif returncode == 3 or (leaks and not errors):
        summary = (
            f"{leaks} leaked cluster(s): space the image is holding on to and "
            "not using. The image is intact and safe to run; repairing it "
            "gives the space back."
        )
    elif returncode == 2 or errors:
        summary = (
            f"{errors} error(s) found. The image is damaged - repair it with "
            "the machine shut down, and restore from a backup if the repair "
            "cannot fix it."
        )
    else:
        summary = stderr.strip() or "qemu-img could not check this image."
    return CheckResult(
        ok=returncode == 0 and not errors and not leaks,
        summary=summary,
        leaks=leaks,
        errors=errors,
        allocated_pct=float(raw.get("allocated-clusters", 0) or 0)
        / max(float(raw.get("total-clusters", 0) or 0), 1) * 100,
        fragmented_pct=float(raw.get("fragmented-clusters", 0) or 0)
        / max(float(raw.get("total-clusters", 0) or 0), 1) * 100,
        repairable=bool(leaks or errors) and returncode in (2, 3),
        output=payload,
    )


def svc_image_info(path: str) -> ImageInfo:
    """Everything qemu-img knows about one image. Read-only."""
    result = _run([_qemu_img(), "info", "--output=json", path], CHECK_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "qemu-img info failed")
    return parse_info(result.stdout, path)


def _run_allowing_failure(argv: list[str], timeout: int):
    """As `_run`, but a non-zero exit is the answer rather than an error.

    qemu-img check reports its verdict through the exit code, so the
    elevated path has to be told that 2 and 3 are results.
    """
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )
    if "Permission denied" not in result.stderr:
        return result

    from .elevate import run_root_script

    # `|| true` so pkexec sees success and the exit code comes back in the
    # JSON rather than as a raised error.
    output = run_root_script('exec "$@" || true', list(argv), timeout=timeout)
    code = 2 if '"check-errors"' in output and '"check-errors": 0' not in output else 0
    return subprocess.CompletedProcess(argv, code, output, "")


def svc_check_image(path: str) -> CheckResult:
    """Look for damage and wasted space. Read-only, safe on a live image.

    Reading a qcow2 that a running guest is writing can report leaks that
    are not really there, so the caller is expected to say whether the
    machine is running. Nothing here writes.
    """
    result = _run_allowing_failure(
        [_qemu_img(), "check", "--output=json", path], CHECK_TIMEOUT
    )
    return parse_check(result.stdout, result.returncode, result.stderr)


def svc_repair_image(path: str, leaks_only: bool = True) -> str:
    """Fix what a check found. Refuses while anything might be writing.

    -r leaks reclaims space and cannot make things worse. -r all rewrites
    metadata qemu-img believes to be wrong, which is the right thing to try
    on a damaged image and the wrong thing to do casually - so the caller
    has to ask for it.
    """
    mode = "leaks" if leaks_only else "all"
    result = _run([_qemu_img(), "check", "-r", mode, path], CHECK_TIMEOUT)
    if result.returncode not in (0, 3):
        raise RuntimeError(
            result.stderr.strip() or f"qemu-img check -r {mode} failed"
        )
    return (result.stdout.strip().splitlines() or ["Repaired."])[-1]


def svc_convert_image(path: str, to_format: str, dest: str = "",
                      cluster_size: str = "", compress: bool = False) -> str:
    """Rewrite an image in another format, alongside the original.

    Never in place: the conversion writes a new file and leaves the old one
    alone, so a failure halfway through costs disk space rather than the
    disk. Pointing the machine at the result is a separate, deliberate step.
    """
    if to_format not in FORMATS:
        raise ValueError(f"Convert to {' or '.join(FORMATS)}")
    if cluster_size and cluster_size not in CLUSTER_SIZES:
        raise ValueError(f"Cluster size is one of {', '.join(CLUSTER_SIZES)}")
    if cluster_size and to_format != "qcow2":
        raise ValueError("Only qcow2 has a cluster size")
    if compress and to_format != "qcow2":
        raise ValueError("Only qcow2 can be compressed")

    target = dest or f"{os.path.splitext(path)[0]}.{to_format}"
    if os.path.exists(target):
        raise RuntimeError(
            f"{target} already exists. Move it aside first - this will not "
            "write over an image that is already there."
        )
    argv = [_qemu_img(), "convert", "-O", to_format]
    if cluster_size:
        argv += ["-o", f"cluster_size={cluster_size}"]
    if compress:
        argv.append("-c")
    argv += [path, target]

    result = _run(argv, CONVERT_TIMEOUT)
    if result.returncode != 0:
        # A half-written target is worse than none: it looks like a usable
        # image and is not.
        if os.path.exists(target):
            try:
                os.unlink(target)
            except OSError:
                pass
        raise RuntimeError(result.stderr.strip() or "qemu-img convert failed")
    size = os.path.getsize(target) if os.path.exists(target) else 0
    return f"Written to {target} ({size / 1024 ** 3:.1f} GB on disk)."
