"""Backwards-compatible façade over the :mod:`vmmanager.core` package.

The service layer lives in ``core/`` now, split by concern. This module keeps
``from .libvirt_service import svc_...`` working for existing callers.
"""

from __future__ import annotations

from .core import *  # noqa: F401,F403
from .core import (  # noqa: F401 - explicit for the names UI code imports directly
    DEFAULT_URI,
    current_uri,
    poll_seconds,
    set_poll_seconds,
    set_uri,
)
