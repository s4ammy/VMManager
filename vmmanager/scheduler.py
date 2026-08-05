"""Scheduled snapshots and power schedules, with or without the window.

The app has always run these on a timer, which meant they only ran while
the app was open. `vmmanager --daemon` runs the same decisions in a plain
loop - a systemd user unit (packaging/vmmanager-scheduler.service) keeps it
alive across logins.

Both sides share the decision functions here, so the two cannot drift; the
daemon writes a heartbeat file each tick and the app stands its own timers
down while that heartbeat is fresh, so a schedule never fires twice.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .logs import log

HEARTBEAT_PATH = Path.home() / ".local" / "share" / "vmmanager" / "scheduler-heartbeat"
# the daemon ticks every 60 s; twice that plus slack means a stopped daemon
# hands control back to the app within a couple of minutes
HEARTBEAT_FRESH_S = 150

def beat(path: Path | None = None) -> None:
    path = path or HEARTBEAT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()

def external_scheduler_active(path: Path | None = None,
                              now: float | None = None) -> bool:
    """True while a daemon heartbeat is fresh - the app skips its own runs."""
    path = path or HEARTBEAT_PATH
    try:
        age = (now if now is not None else time.time()) - path.stat().st_mtime
    except OSError:
        return False
    return 0 <= age < HEARTBEAT_FRESH_S

def snapshots_due(
    schedules: list[tuple[str, int, int, bool, int]],
    known: set[str],
    now: float,
) -> list[tuple[str, int, bool]]:
    """(uuid, keep, external) for every schedule whose interval has passed."""
    return [
        (uuid, keep, bool(external))
        for uuid, interval_s, keep, external, last_run in schedules
        if uuid in known and now - last_run >= interval_s
    ]

def wake_actions(
    schedules: list[tuple[str, str, str, str, str]],
    facts: dict[str, tuple[str, bool]],  # uuid → (state, is_template)
    hm: str,
    date: str,
    is_weekday: bool,
) -> list[tuple[str, str, str]]:
    """(uuid, action, fired-key) for every power schedule due this minute.

    The fired-key makes the minute idempotent: it is stored, and a
    schedule whose stored key matches is one that already fired.
    """
    out = []
    for uuid, start_hm, stop_hm, days, last_fired in schedules:
        fact = facts.get(uuid)
        if fact is None:
            continue
        state, is_template = fact
        if days == "weekdays" and not is_weekday:
            continue
        if days == "weekends" and is_weekday:
            continue
        if start_hm and hm == start_hm:
            key = f"{date} {hm} start"
            if last_fired != key and state == "shutoff" and not is_template:
                out.append((uuid, "start", key))
        elif stop_hm and hm == stop_hm:
            key = f"{date} {hm} stop"
            if last_fired != key and state == "running":
                out.append((uuid, "shutdown", key))
    return out

def _domain_facts() -> dict[str, tuple[str, str, bool]]:
    """uuid → (name, state, is_template) straight from libvirt."""
    import libvirt

    from .core.connection import VMM_NS, _with_conn

    STATES = {
        libvirt.VIR_DOMAIN_RUNNING: "running",
        libvirt.VIR_DOMAIN_SHUTOFF: "shutoff",
        libvirt.VIR_DOMAIN_PAUSED: "paused",
    }

    def go(conn):
        out = {}
        for dom in conn.listAllDomains():
            state, _ = dom.state()
            is_template = False
            try:
                meta = dom.metadata(
                    libvirt.VIR_DOMAIN_METADATA_ELEMENT, VMM_NS,
                )
                is_template = "<template" in meta
            except libvirt.libvirtError:
                pass
            out[dom.UUIDString()] = (
                dom.name(), STATES.get(state, "other"), is_template,
            )
        return out

    return _with_conn(go)

def snapshot_tick(store, facts=None) -> list[str]:
    """One pass over the snapshot schedules; what it did, as sentences."""
    from .libvirt_service import svc_create_snapshot, svc_prune_snapshots

    facts = facts if facts is not None else _domain_facts()
    messages = []
    for uuid, keep, external in snapshots_due(
        store.schedules(), set(facts), time.time()
    ):
        store.mark_schedule_run(uuid)
        name = "auto-" + time.strftime("%Y%m%d-%H%M%S")
        vm_name = facts[uuid][0]
        try:
            svc_create_snapshot(uuid, name, "scheduled snapshot", external)
            pruned = svc_prune_snapshots(uuid, "auto-", keep)
            messages.append(
                f"snapshotted {vm_name}"
                + (f" · pruned {pruned}" if pruned else "")
            )
        except Exception as e:  # keep the loop alive for the other machines
            log.warning("scheduled snapshot of %s: %s", vm_name, e)
            messages.append(f"scheduled snapshot of {vm_name} failed: {e}")
    return messages

def wake_tick(store, facts=None) -> list[str]:
    """One pass over the power schedules; what it did, as sentences."""
    from .libvirt_service import svc_domain_action

    facts = facts if facts is not None else _domain_facts()
    now = time.localtime()
    actions = wake_actions(
        store.wake_schedules(),
        {u: (state, tpl) for u, (_n, state, tpl) in facts.items()},
        time.strftime("%H:%M", now),
        time.strftime("%Y-%m-%d", now),
        now.tm_wday < 5,
    )
    messages = []
    for uuid, action, key in actions:
        store.mark_wake_fired(uuid, key)
        vm_name = facts[uuid][0]
        try:
            svc_domain_action(uuid, action)
            messages.append(f"{vm_name}: power schedule {action}")
        except Exception as e:
            log.warning("power schedule %s of %s: %s", action, vm_name, e)
            messages.append(f"power schedule {action} of {vm_name} failed: {e}")
    return messages

def run_daemon(interval_s: int = 60) -> None:
    """The --daemon loop: no window, no display, just the schedules.

    QSettings (plain QtCore, works headless) supplies the same connection
    URI the app would use.
    """
    from .core.connection import current_uri, set_uri
    from .data.history import StatsStore
    from .pages.settings import active_uri

    set_uri(active_uri())
    store = StatsStore()
    log.info("scheduler daemon on %s, every %ss", current_uri(), interval_s)
    try:
        while True:
            beat()
            try:
                for message in snapshot_tick(store) + wake_tick(store):
                    log.info("%s", message)
            except Exception as e:
                # libvirtd down, most likely; keep beating and retry
                log.warning("scheduler tick failed: %s", e)
            time.sleep(interval_s)
    finally:
        store.close()
