"""Libvirt service layer.

Split by concern: models and connection settings at the bottom, then
one module per area of libvirt (domains, devices, storage, networks,
snapshots, guest agent, host devices, console) and the poll worker on
top. Import from here; the submodules are free to import each other in
dependency order.
"""

from __future__ import annotations

from .models import *  # noqa: F401,F403
from .connection import *  # noqa: F401,F403
from .xmlutil import *  # noqa: F401,F403
from .convert import *  # noqa: F401,F403
from .devices import *  # noqa: F401,F403
from .guest import *  # noqa: F401,F403
from .networks import *  # noqa: F401,F403
from .nwfilter import *  # noqa: F401,F403
from .snapshots import *  # noqa: F401,F403
from .create import *  # noqa: F401,F403
from .storage import *  # noqa: F401,F403
from .imgtools import *  # noqa: F401,F403
from .features import *  # noqa: F401,F403
from .modes import *  # noqa: F401,F403
from .tuning import *  # noqa: F401,F403
from .startcheck import *  # noqa: F401,F403
from .console import *  # noqa: F401,F403
from .hostdev import *  # noqa: F401,F403
from .elevate import *  # noqa: F401,F403
from .vfio import *  # noqa: F401,F403
from .hooks import *  # noqa: F401,F403
from .mdev import *  # noqa: F401,F403
from .domains import *  # noqa: F401,F403
from .inspect import *  # noqa: F401,F403
from .compare import *  # noqa: F401,F403
from .profiles import *  # noqa: F401,F403
from .osident import *  # noqa: F401,F403
from .poller import *  # noqa: F401,F403

# Every write in this package goes through the activity log. Wrapping here
# rather than at each definition means a service function written tomorrow
# is recorded the day it lands, and the modules themselves stay unaware of
# it. Callers see the same names either way.
from .activity import wrap_module as _wrap_writes  # noqa: E402

RECORDED = _wrap_writes(globals())

from .connection import (  # noqa: F401,E402
    DEFAULT_URI,
    current_uri,
    poll_seconds,
    set_poll_seconds,
    set_uri,
)
