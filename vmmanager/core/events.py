"""libvirt event subscriptions, so we're told about changes instead of asking.

libvirt needs an event implementation registered process-wide before the
connections that want events are opened, and something has to drive it.
EventPump is that something.

Callbacks run on the pump thread. They only mark a machine stale and set a
flag. No Qt, no UI.
"""

from __future__ import annotations

import threading

import libvirt

from ..logs import log

_registered = False
_register_lock = threading.Lock()


def ensure_registered() -> None:
    """Install libvirt's default event implementation, once per process."""
    global _registered
    with _register_lock:
        if _registered:
            return
        libvirt.virEventRegisterDefaultImpl()
        _registered = True
        log.debug("libvirt event implementation registered")


class EventPump(threading.Thread):
    """Runs libvirt's event loop for the life of the application."""

    def __init__(self) -> None:
        super().__init__(name="libvirt-events", daemon=True)
        self._stop = threading.Event()

    def run(self) -> None:
        ensure_registered()
        while not self._stop.is_set():
            try:
                libvirt.virEventRunDefaultImpl()
            except Exception:  # noqa: BLE001 - one bad event mustn't kill the pump
                log.exception("libvirt event loop raised; continuing")

    def stop(self) -> None:
        self._stop.set()


class DomainWatch:
    """Tracks which machines have changed under us.

    `stale` holds UUIDs to re-read; `changed` wakes the poll worker so it
    refreshes now rather than at the end of its interval.
    """

    def __init__(self) -> None:
        self.stale: set[str] = set()
        self.changed = threading.Event()
        self._lock = threading.Lock()
        self._ids: list[tuple[libvirt.virConnect, int]] = []
        self._net_ids: list[tuple[libvirt.virConnect, int]] = []

    # -- callbacks, on the pump thread

    def _touch(self, dom: libvirt.virDomain | None) -> None:
        with self._lock:
            if dom is not None:
                try:
                    self.stale.add(dom.UUIDString())
                except libvirt.libvirtError:
                    pass  # already gone
        self.changed.set()

    def _lifecycle(self, _conn, dom, event, detail, _opaque) -> None:
        # Started/stopped/paused/resumed, plus DEFINED with detail UPDATED,
        # as produced by `virsh edit` and virt-manager. So edits from outside
        # this app invalidate our cache too.
        log.debug("domain event: %s event=%s detail=%s", dom.name(), event, detail)
        self._touch(dom)

    def _metadata(self, _conn, dom, _mtype, _nsuri, _opaque) -> None:
        self._touch(dom)

    def _device(self, _conn, dom, _alias, _opaque) -> None:
        self._touch(dom)

    def _network(self, _conn, _net, _event, _detail, _opaque) -> None:
        self.changed.set()

    # -- registration

    def attach(self, conn: libvirt.virConnect) -> None:
        self.detach()
        wanted = [
            ("VIR_DOMAIN_EVENT_ID_LIFECYCLE", self._lifecycle),
            ("VIR_DOMAIN_EVENT_ID_METADATA_CHANGE", self._metadata),
            ("VIR_DOMAIN_EVENT_ID_DEVICE_ADDED", self._device),
            ("VIR_DOMAIN_EVENT_ID_DEVICE_REMOVED", self._device),
        ]
        for name, callback in wanted:
            event_id = getattr(libvirt, name, None)
            if event_id is None:
                continue  # older libvirt doesn't define it
            try:
                self._ids.append(
                    (conn, conn.domainEventRegisterAny(None, event_id, callback, None))
                )
            except libvirt.libvirtError as exc:
                # test:/// and some other drivers support only a subset
                log.debug("no %s on this connection: %s", name, exc)
        net_event = getattr(libvirt, "VIR_NETWORK_EVENT_ID_LIFECYCLE", None)
        if net_event is not None:
            try:
                self._net_ids.append(
                    (conn, conn.networkEventRegisterAny(None, net_event, self._network, None))
                )
            except libvirt.libvirtError as exc:
                log.debug("no network events on this connection: %s", exc)
        log.info(
            "subscribed to %d domain and %d network event source(s)",
            len(self._ids), len(self._net_ids),
        )

    def detach(self) -> None:
        for conn, handle in self._ids:
            try:
                conn.domainEventDeregisterAny(handle)
            except libvirt.libvirtError:
                pass
        for conn, handle in self._net_ids:
            try:
                conn.networkEventDeregisterAny(handle)
            except libvirt.libvirtError:
                pass
        self._ids.clear()
        self._net_ids.clear()

    @property
    def subscribed(self) -> bool:
        return bool(self._ids)

    def take_stale(self) -> set[str]:
        with self._lock:
            stale, self.stale = self.stale, set()
        return stale
