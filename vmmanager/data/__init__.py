"""Local data: the stats/history store, the image catalog, OS metadata.

Nothing here talks to libvirt; these are the app's own persistence and the
outside-world lookups (downloads, libosinfo).
"""

from __future__ import annotations

from .catalog import CATALOG, VIRTIO_WIN, CatalogImage, ImageDownloader  # noqa: F401
from .history import (  # noqa: F401
    DB_PATH,
    StatsStore,
    data_extent,
    query_events,
    query_history,
    xml_versions,
)
from .osinfo import OsVariant, detect_iso, list_os_variants  # noqa: F401
