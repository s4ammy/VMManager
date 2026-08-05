"""OS-aware defaults via libosinfo (GObject introspection), with a safe
fallback when the library isn't available. Everything here runs on the
task thread pool - the loader can take a moment on first use."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class OsVariant:
    short_id: str
    name: str
    osinfo_id: str  # e.g. http://fedoraproject.org/fedora/42
    min_mem_mb: int
    rec_mem_mb: int
    rec_storage_gb: float
    rec_vcpus: int


_FALLBACK = OsVariant(
    short_id="generic",
    name="Generic Linux / other",
    osinfo_id="",
    min_mem_mb=1024,
    rec_mem_mb=4096,
    rec_storage_gb=40,
    rec_vcpus=2,
)


def _load_db():
    import gi

    gi.require_version("Libosinfo", "1.0")
    from gi.repository import Libosinfo

    loader = Libosinfo.Loader()
    loader.process_default_path()
    return Libosinfo, loader.get_db()


def _resources_of(os_obj) -> tuple[int, int, float, int]:
    min_mem, rec_mem, storage, vcpus = 1024, 4096, 40.0, 2
    try:
        mins = os_obj.get_minimum_resources()
        for i in range(mins.get_length()):
            r = mins.get_nth(i)
            if r.get_ram() > 0:
                min_mem = max(min_mem, int(r.get_ram() / 1024**2))
        recs = os_obj.get_recommended_resources()
        for i in range(recs.get_length()):
            r = recs.get_nth(i)
            if r.get_ram() > 0:
                rec_mem = int(r.get_ram() / 1024**2)
            if r.get_storage() > 0:
                storage = max(storage, r.get_storage() / 1024**3)
            if r.get_n_cpus() > 0:
                vcpus = r.get_n_cpus()
    except Exception:  # noqa: BLE001 - resource lists are optional metadata
        pass
    return min_mem, max(rec_mem, min_mem), storage, vcpus


@lru_cache(maxsize=1)
def list_os_variants() -> tuple[OsVariant, ...]:
    """All known OSes, newest-ish first, with the generic fallback appended."""
    try:
        _lo, db = _load_db()
    except Exception:  # noqa: BLE001 - libosinfo missing entirely
        return (_FALLBACK,)
    out: list[OsVariant] = []
    oses = db.get_os_list()
    for i in range(oses.get_length()):
        os_obj = oses.get_nth(i)
        min_mem, rec_mem, storage, vcpus = _resources_of(os_obj)
        out.append(
            OsVariant(
                short_id=os_obj.get_short_id() or "?",
                name=os_obj.get_name() or os_obj.get_short_id() or "?",
                osinfo_id=os_obj.get_id() or "",
                min_mem_mb=min_mem,
                rec_mem_mb=rec_mem,
                rec_storage_gb=storage,
                rec_vcpus=vcpus,
            )
        )
    out.sort(key=lambda v: v.name.lower())
    out.append(_FALLBACK)
    return tuple(out)


def detect_iso(path: str) -> str | None:
    """Identify an install ISO; returns the OS short-id or None."""
    try:
        lo, db = _load_db()
        media = lo.Media.create_from_location(f"file://{path}", None)
        if db.identify_media(media):
            os_obj = media.get_os()
            if os_obj is not None:
                return os_obj.get_short_id()
    except Exception:  # noqa: BLE001 - detection is best-effort
        pass
    return None
