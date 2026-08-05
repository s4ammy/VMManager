"""Cloud image catalog: curated distro images, downloaded + checksum-verified
straight into a storage pool, ready for the import-image wizard path."""

from __future__ import annotations

import hashlib
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

CACHE_DIR = Path.home() / ".cache" / "vmmanager" / "images"


@dataclass(frozen=True)
class CatalogImage:
    name: str
    osinfo_short_id: str
    url: str
    checksum_url: str  # "" when upstream publishes none
    checksum_algo: str  # sha256 | sha512
    user_hint: str  # conventional cloud-init default user


# The Windows guest tooling ISO. Fedora's virtio-win repo publishes no
# checksum file next to the stable ISO, so this entry downloads unverified
# over HTTPS - the downloader says so rather than pretending otherwise.
VIRTIO_WIN = CatalogImage(
    "virtio-win guest tools (Windows drivers)", "",
    "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso",
    "", "sha256", "",
)


CATALOG: tuple[CatalogImage, ...] = (
    CatalogImage(
        "Debian 13 (trixie)", "debian13",
        "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2",
        "https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS",
        "sha512", "debian",
    ),
    CatalogImage(
        "Debian 12 (bookworm)", "debian12",
        "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2",
        "https://cloud.debian.org/images/cloud/bookworm/latest/SHA512SUMS",
        "sha512", "debian",
    ),
    CatalogImage(
        "Ubuntu 24.04 LTS (noble)", "ubuntu24.04",
        "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
        "https://cloud-images.ubuntu.com/noble/current/SHA256SUMS",
        "sha256", "ubuntu",
    ),
    CatalogImage(
        "AlmaLinux 10", "almalinux10",
        "https://repo.almalinux.org/almalinux/10/cloud/x86_64/images/AlmaLinux-10-GenericCloud-latest.x86_64.qcow2",
        "https://repo.almalinux.org/almalinux/10/cloud/x86_64/images/CHECKSUM",
        "sha256", "almalinux",
    ),
    CatalogImage(
        "AlmaLinux 9", "almalinux9",
        "https://repo.almalinux.org/almalinux/9/cloud/x86_64/images/AlmaLinux-9-GenericCloud-latest.x86_64.qcow2",
        "https://repo.almalinux.org/almalinux/9/cloud/x86_64/images/CHECKSUM",
        "sha256", "almalinux",
    ),
    CatalogImage(
        "Rocky Linux 9", "rocky9",
        "https://download.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2",
        "https://download.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2.CHECKSUM",
        "sha256", "rocky",
    ),
    CatalogImage(
        "Arch Linux", "archlinux",
        "https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2",
        "https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2.SHA256",
        "sha256", "arch",
    ),
)


def _expected_checksum(checksums_text: str, filename: str, algo: str) -> str | None:
    """Handle both 'hash  filename' and 'SHA256 (filename) = hash' formats."""
    want_len = {"sha256": 64, "sha512": 128}[algo]
    for line in checksums_text.splitlines():
        if filename not in line and len(checksums_text.splitlines()) > 1:
            continue
        for token in re.split(r"[\s=()]+", line):
            if len(token) == want_len and re.fullmatch(r"[0-9a-fA-F]+", token):
                return token.lower()
    return None


class ImageDownloader(QThread):
    """Download -> verify -> hand back the local path. Cache hits are instant."""

    progress = Signal(int, str)  # percent (-1 unknown), status text
    finished_ok = Signal(str)  # local file path
    failed = Signal(str)

    def __init__(self, image: CatalogImage) -> None:
        super().__init__()
        self._image = image
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        img = self._image
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            filename = img.url.rsplit("/", 1)[-1]
            expected = None
            if img.checksum_url:
                self.progress.emit(-1, "fetching checksums…")
                with urllib.request.urlopen(img.checksum_url, timeout=30) as r:
                    checksums = r.read().decode("utf-8", "replace")
                expected = _expected_checksum(checksums, filename, img.checksum_algo)
                if expected is None:
                    raise RuntimeError("couldn't find the file's checksum upstream")

            target = CACHE_DIR / filename
            if target.exists():
                if expected is None:
                    self.finished_ok.emit(str(target))  # cached, nothing to check
                    return
                if self._digest(target) == expected:
                    self.finished_ok.emit(str(target))
                    return

            self.progress.emit(
                0, "downloading…" if expected else "downloading (no checksum published)…"
            )
            hasher = hashlib.new(img.checksum_algo)
            with urllib.request.urlopen(img.url, timeout=60) as r:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                with open(target, "wb") as f:
                    while True:
                        if self._cancel:
                            raise RuntimeError("cancelled")
                        chunk = r.read(1024 * 512)
                        if not chunk:
                            break
                        f.write(chunk)
                        hasher.update(chunk)
                        done += len(chunk)
                        if total:
                            self.progress.emit(
                                int(done * 100 / total),
                                f"downloading… {done // 1024**2} / {total // 1024**2} MB",
                            )
            if expected is not None and hasher.hexdigest().lower() != expected:
                target.unlink(missing_ok=True)
                raise RuntimeError("checksum mismatch - upstream file changed mid-download?")
            self.finished_ok.emit(str(target))
        except Exception as e:  # noqa: BLE001 - everything surfaces in the dialog
            self.failed.emit(str(e))

    def _digest(self, path: Path) -> str:
        self.progress.emit(-1, "verifying cached copy…")
        hasher = hashlib.new(self._image.checksum_algo)
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest().lower()
