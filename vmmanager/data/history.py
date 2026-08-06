"""Persistent stats history in SQLite, for scrub-back beyond the live ring.

The store records one row per running domain (plus one for the host, uuid '')
on every poll tick - trivial write volume, WAL keeps it off the UI's back.
Queries bucket a time range into fixed-width averages sized for a sparkline.
"""

from __future__ import annotations

import functools
import sqlite3
import time
from pathlib import Path

DB_DIR = Path.home() / ".local" / "share" / "vmmanager"
DB_PATH = DB_DIR / "stats.db"
RETENTION_DAYS = 30
ACTIVITY_KEEP_DAYS = 90  # the log of what this app did, kept longer than stats

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts   INTEGER NOT NULL,
    uuid TEXT    NOT NULL,
    cpu  REAL, mem REAL, disk REAL, net REAL
);
CREATE INDEX IF NOT EXISTS idx_samples ON samples (uuid, ts);
CREATE TABLE IF NOT EXISTS xml_history (
    uuid TEXT NOT NULL,
    ts   INTEGER NOT NULL,
    xml  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_xml ON xml_history (uuid, ts);
CREATE TABLE IF NOT EXISTS snap_schedules (
    uuid      TEXT PRIMARY KEY,
    interval_s INTEGER NOT NULL,
    keep      INTEGER NOT NULL,
    external  INTEGER NOT NULL DEFAULT 0,
    last_run  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    uuid   TEXT NOT NULL,
    ts     INTEGER NOT NULL,
    kind   TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events ON events (uuid, ts);
CREATE TABLE IF NOT EXISTS wake_schedules (
    uuid     TEXT PRIMARY KEY,
    start_hm TEXT NOT NULL DEFAULT '',
    stop_hm  TEXT NOT NULL DEFAULT '',
    days     TEXT NOT NULL DEFAULT 'all',
    last_fired TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS modes (
    uuid    TEXT NOT NULL,
    name    TEXT NOT NULL,
    xml     TEXT NOT NULL,
    note    TEXT NOT NULL DEFAULT '',
    marker  TEXT NOT NULL DEFAULT '',
    created INTEGER NOT NULL,
    PRIMARY KEY (uuid, name)
);

CREATE TABLE IF NOT EXISTS active_mode (
    uuid TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stacks (
    name     TEXT PRIMARY KEY,
    template TEXT NOT NULL,
    count    INTEGER NOT NULL,
    network  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
    ts     INTEGER NOT NULL,
    uuid   TEXT    NOT NULL DEFAULT '',
    action TEXT    NOT NULL,
    detail TEXT    NOT NULL DEFAULT '',
    ok     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_activity ON activity (ts);
CREATE TABLE IF NOT EXISTS profiles (
    name    TEXT PRIMARY KEY,
    spec    TEXT NOT NULL,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS usb_rules (
    uuid  TEXT NOT NULL,
    ident TEXT NOT NULL,
    PRIMARY KEY (uuid, ident)
);
"""
XML_KEEP = 50  # config versions kept per domain


def _when_open(default):
    """Return `default` instead of touching a closed database.

    The store is closed on shutdown while polls and UI reads may still be in
    flight, and sqlite3 raises on a closed connection rather than no-opping.
    """

    def decorate(method):
        @functools.wraps(method)
        def guarded(self, *args, **kwargs):
            if self._closed:
                return default() if callable(default) else default
            return method(self, *args, **kwargs)

        return guarded

    return decorate


class StatsStore:
    """Writer used on the UI thread; one insert batch per poll tick."""

    def __init__(self, path: Path | None = None) -> None:
        # Resolved here rather than as a default argument, which is evaluated at
        # import: with `path: Path = DB_PATH`, redirecting DB_PATH had no effect
        # on StatsStore() and the test suite wrote to the real database.
        path = path if path is not None else DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._last_prune = 0.0
        self._closed = False
        self._dedup_xml_history()

    def _dedup_xml_history(self) -> None:
        """One-time sweep: drop consecutive duplicate config versions left
        behind by earlier builds that recorded on every launch."""
        doomed: list[tuple[str, int]] = []
        for (uuid,) in self._db.execute(
            "SELECT DISTINCT uuid FROM xml_history"
        ).fetchall():
            prev = None
            for ts, xml in self._db.execute(
                "SELECT ts, xml FROM xml_history WHERE uuid = ? ORDER BY ts",
                (uuid,),
            ).fetchall():
                if xml == prev:
                    doomed.append((uuid, ts))
                prev = xml
        for uuid, ts in doomed:
            self._db.execute(
                "DELETE FROM xml_history WHERE uuid = ? AND ts = ? ", (uuid, ts)
            )
        if doomed:
            self._db.commit()

    def record(self, domains, host) -> None:
        if self._closed:
            return  # a queued poll tick can land after shutdown
        now = int(time.time())
        rows = [
            (now, d.uuid, d.usage.cpu_pct, d.usage.mem_mb, d.usage.disk_bps, d.usage.net_bps)
            for d in domains
            if d.state == "running"
        ]
        rows.append((now, "", host.cpu_pct, host.mem_used_mb, 0.0, 0.0))
        self._db.executemany("INSERT INTO samples VALUES (?,?,?,?,?,?)", rows)
        self._db.commit()
        if time.monotonic() - self._last_prune > 3600:
            self._last_prune = time.monotonic()
            cutoff = now - RETENTION_DAYS * 86400
            self._db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            self._db.execute(
                "DELETE FROM activity WHERE ts < ?",
                (now - ACTIVITY_KEEP_DAYS * 86400,),
            )
            self._db.commit()

    def record_xml(self, uuid: str, xml: str) -> None:
        """Store a config version - unless it's identical to the latest one.

        The poller's change detection is in-memory and resets every launch,
        so without this check each app start would log a duplicate.
        """
        if self._closed or self.latest_xml(uuid) == xml:
            return
        now = int(time.time())
        self._db.execute(
            "INSERT INTO xml_history (uuid, ts, xml) VALUES (?,?,?)",
            (uuid, now, xml),
        )
        self._db.execute(
            """DELETE FROM xml_history WHERE uuid = ? AND ts NOT IN (
                 SELECT ts FROM xml_history WHERE uuid = ?
                 ORDER BY ts DESC LIMIT ?)""",
            (uuid, uuid, XML_KEEP),
        )
        self._db.commit()

    @_when_open(None)
    def latest_xml(self, uuid: str) -> str | None:
        row = self._db.execute(
            "SELECT xml FROM xml_history WHERE uuid = ? ORDER BY ts DESC LIMIT 1",
            (uuid,),
        ).fetchone()
        return row[0] if row else None

    # -- snapshot schedules

    @_when_open(list)
    def schedules(self) -> list[tuple[str, int, int, bool, int]]:
        """(uuid, interval_s, keep, external, last_run) for every schedule."""
        return [
            (u, i, k, bool(e), lr)
            for u, i, k, e, lr in self._db.execute(
                "SELECT uuid, interval_s, keep, external, last_run FROM snap_schedules"
            )
        ]

    @_when_open(None)
    def schedule_for(self, uuid: str) -> tuple[int, int, bool] | None:
        row = self._db.execute(
            "SELECT interval_s, keep, external FROM snap_schedules WHERE uuid = ?",
            (uuid,),
        ).fetchone()
        return (row[0], row[1], bool(row[2])) if row else None

    @_when_open(None)
    def set_schedule(
        self, uuid: str, interval_s: int, keep: int, external: bool
    ) -> None:
        self._db.execute(
            """INSERT INTO snap_schedules (uuid, interval_s, keep, external, last_run)
               VALUES (?,?,?,?,COALESCE(
                   (SELECT last_run FROM snap_schedules WHERE uuid = ?), 0))
               ON CONFLICT(uuid) DO UPDATE
               SET interval_s = ?, keep = ?, external = ?""",
            (uuid, interval_s, keep, int(external), uuid,
             interval_s, keep, int(external)),
        )
        self._db.commit()

    @_when_open(None)
    def clear_schedule(self, uuid: str) -> None:
        self._db.execute("DELETE FROM snap_schedules WHERE uuid = ?", (uuid,))
        self._db.commit()

    @_when_open(None)
    def mark_schedule_run(self, uuid: str) -> None:
        self._db.execute(
            "UPDATE snap_schedules SET last_run = ? WHERE uuid = ?",
            (int(time.time()), uuid),
        )
        self._db.commit()

    # -- auto-attach USB rules

    @_when_open(list)
    def usb_rules(self) -> list[tuple[str, str]]:
        """(machine uuid, 'vvvv:pppp') for every auto-attach rule."""
        return list(self._db.execute("SELECT uuid, ident FROM usb_rules"))

    @_when_open(list)
    def usb_rules_for(self, uuid: str) -> list[str]:
        return [
            ident for (ident,) in self._db.execute(
                "SELECT ident FROM usb_rules WHERE uuid = ?", (uuid,)
            )
        ]

    @_when_open(None)
    def set_usb_rules(self, uuid: str, idents: list[str]) -> None:
        self._db.execute("DELETE FROM usb_rules WHERE uuid = ?", (uuid,))
        self._db.executemany(
            "INSERT INTO usb_rules (uuid, ident) VALUES (?,?)",
            [(uuid, ident) for ident in idents],
        )
        self._db.commit()

    # -- timeline events

    @_when_open(None)
    def record_event(self, uuid: str, kind: str, detail: str = "") -> None:
        self._db.execute(
            "INSERT INTO events (uuid, ts, kind, detail) VALUES (?,?,?,?)",
            (uuid, int(time.time()), kind, detail),
        )
        self._db.commit()

    # -- hardware profiles

    @_when_open(list)
    def profiles(self) -> list[tuple[str, str, int]]:
        return list(self._db.execute(
            "SELECT name, spec, created FROM profiles ORDER BY name"
        ))

    @_when_open(None)
    def save_profile(self, name: str, spec: str) -> None:
        self._db.execute(
            "INSERT INTO profiles (name, spec, created) VALUES (?,?,?)"
            " ON CONFLICT(name) DO UPDATE SET spec = excluded.spec",
            (name, spec, int(time.time())),
        )
        self._db.commit()

    @_when_open(None)
    def delete_profile(self, name: str) -> None:
        self._db.execute("DELETE FROM profiles WHERE name = ?", (name,))
        self._db.commit()

    # -- what this app did

    @_when_open(None)
    def record_activity(self, action: str, uuid: str = "", detail: str = "",
                        ok: bool = True) -> None:
        self._db.execute(
            "INSERT INTO activity (ts, uuid, action, detail, ok) VALUES (?,?,?,?,?)",
            (int(time.time()), uuid, action, detail, 1 if ok else 0),
        )
        self._db.commit()

    # -- wake / power schedules

    @_when_open(list)
    def wake_schedules(self) -> list[tuple[str, str, str, str, str]]:
        return list(
            self._db.execute(
                "SELECT uuid, start_hm, stop_hm, days, last_fired FROM wake_schedules"
            )
        )

    @_when_open(None)
    def wake_schedule_for(self, uuid: str) -> tuple[str, str, str] | None:
        row = self._db.execute(
            "SELECT start_hm, stop_hm, days FROM wake_schedules WHERE uuid = ?",
            (uuid,),
        ).fetchone()
        return tuple(row) if row else None

    @_when_open(None)
    def set_wake_schedule(self, uuid: str, start_hm: str, stop_hm: str, days: str) -> None:
        self._db.execute(
            """INSERT INTO wake_schedules (uuid, start_hm, stop_hm, days)
               VALUES (?,?,?,?)
               ON CONFLICT(uuid) DO UPDATE
               SET start_hm = ?, stop_hm = ?, days = ?""",
            (uuid, start_hm, stop_hm, days, start_hm, stop_hm, days),
        )
        self._db.commit()

    @_when_open(None)
    def clear_wake_schedule(self, uuid: str) -> None:
        self._db.execute("DELETE FROM wake_schedules WHERE uuid = ?", (uuid,))
        self._db.commit()

    @_when_open(None)
    def mark_wake_fired(self, uuid: str, stamp: str) -> None:
        self._db.execute(
            "UPDATE wake_schedules SET last_fired = ? WHERE uuid = ?", (stamp, uuid)
        )
        self._db.commit()

    # -- stacks

    @_when_open(list)
    def stacks(self) -> list[tuple[str, str, int, str]]:
        return list(
            self._db.execute("SELECT name, template, count, network FROM stacks")
        )

    @_when_open(None)
    def save_stack(self, name: str, template: str, count: int, network: str) -> None:
        self._db.execute(
            """INSERT INTO stacks (name, template, count, network) VALUES (?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET template=?, count=?, network=?""",
            (name, template, count, network, template, count, network),
        )
        self._db.commit()

    @_when_open(None)
    def delete_stack(self, name: str) -> None:
        self._db.execute("DELETE FROM stacks WHERE name = ?", (name,))
        self._db.commit()

    # -- machine modes

    @_when_open(list)
    def modes(self, uuid: str) -> list[tuple[str, str, str, int]]:
        """(name, note, marker, created) for a machine, oldest first."""
        return self._db.execute(
            "SELECT name, note, marker, created FROM modes WHERE uuid = ?"
            " ORDER BY created",
            (uuid,),
        ).fetchall()

    @_when_open(None)
    def mode_xml(self, uuid: str, name: str) -> str | None:
        row = self._db.execute(
            "SELECT xml FROM modes WHERE uuid = ? AND name = ?", (uuid, name)
        ).fetchone()
        return row[0] if row else None

    @_when_open(None)
    def save_mode(self, uuid: str, name: str, xml: str, note: str = "",
                  marker: str = "") -> None:
        self._db.execute(
            "INSERT INTO modes (uuid, name, xml, note, marker, created)"
            " VALUES (?, ?, ?, ?, ?, strftime('%s','now'))"
            " ON CONFLICT(uuid, name) DO UPDATE SET"
            " xml = excluded.xml, note = excluded.note, marker = excluded.marker",
            (uuid, name, xml, note, marker),
        )
        self._db.commit()

    @_when_open(None)
    def delete_mode(self, uuid: str, name: str) -> None:
        self._db.execute(
            "DELETE FROM modes WHERE uuid = ? AND name = ?", (uuid, name)
        )
        self._db.execute(
            "DELETE FROM active_mode WHERE uuid = ? AND name = ?", (uuid, name)
        )
        self._db.commit()

    @_when_open(None)
    def active_mode(self, uuid: str) -> str | None:
        row = self._db.execute(
            "SELECT name FROM active_mode WHERE uuid = ?", (uuid,)
        ).fetchone()
        return row[0] if row else None

    @_when_open(dict)
    def all_active_modes(self) -> dict[str, str]:
        return dict(self._db.execute("SELECT uuid, name FROM active_mode").fetchall())

    @_when_open(None)
    def set_active_mode(self, uuid: str, name: str) -> None:
        self._db.execute(
            "INSERT INTO active_mode (uuid, name) VALUES (?, ?)"
            " ON CONFLICT(uuid) DO UPDATE SET name = excluded.name",
            (uuid, name),
        )
        self._db.commit()

    def close(self) -> None:
        self._closed = True
        self._db.close()


def _read_only_connection():
    """A connection for the read helpers, or None when there is nothing yet.

    These run on the task pool and each opens its own connection. sqlite3 raises
    if the file's directory does not exist, which is the state of a profile that
    has never had a poll tick - and an exception here surfaces as a modal error
    dialog over an empty history tab.
    """
    if not DB_PATH.exists():
        return None
    return sqlite3.connect(DB_PATH)


def record_activity(action: str, uuid: str = "", detail: str = "",
                    ok: bool = True) -> None:
    """Append one line to the activity log, from wherever it happened.

    Its own short-lived connection rather than the poller's store: service
    calls come off the task pool, the scheduler daemon has no store at all,
    and neither should have to know who owns the file. WAL makes the
    concurrent write a non-event.
    """
    DB_DIR.mkdir(parents=True, exist_ok=True)
    try:
        db = sqlite3.connect(DB_PATH, timeout=2.0)
    except sqlite3.Error:
        return
    try:
        db.executescript(_SCHEMA)
        db.execute(
            "INSERT INTO activity (ts, uuid, action, detail, ok) VALUES (?,?,?,?,?)",
            (int(time.time()), uuid, action, detail, 1 if ok else 0),
        )
        db.commit()
    except sqlite3.Error:
        pass
    finally:
        db.close()


def query_events(uuid: str, limit: int = 300) -> list[tuple[int, str, str]]:
    """(ts, kind, detail) newest first, own connection for the task pool."""
    db = _read_only_connection()
    if db is None:
        return []
    try:
        return db.execute(
            "SELECT ts, kind, detail FROM events WHERE uuid = ?"
            " ORDER BY ts DESC LIMIT ?",
            (uuid, limit),
        ).fetchall()
    finally:
        db.close()


def query_activity(limit: int = 500, uuid: str = "",
                   failures_only: bool = False) -> list[tuple[int, str, str, str, int]]:
    """(ts, uuid, action, detail, ok) newest first.

    Its own connection, because the UI reads this from the task pool while
    the poller is writing on another thread.
    """
    db = _read_only_connection()
    if db is None:
        return []
    try:
        where, args = [], []
        if uuid:
            where.append("uuid = ?")
            args.append(uuid)
        if failures_only:
            where.append("ok = 0")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        args.append(limit)
        return db.execute(
            "SELECT ts, uuid, action, detail, ok FROM activity"
            f"{clause} ORDER BY ts DESC LIMIT ?", args,
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()


def xml_versions(uuid: str) -> list[tuple[int, str]]:
    """(ts, xml) versions, newest first, own connection for the task pool."""
    db = _read_only_connection()
    if db is None:
        return []
    try:
        return db.execute(
            "SELECT ts, xml FROM xml_history WHERE uuid = ? ORDER BY ts DESC",
            (uuid,),
        ).fetchall()
    finally:
        db.close()


def query_history(
    uuid: str, start_ts: float, end_ts: float, buckets: int = 120
) -> list[tuple[float, float, float, float]]:
    """Bucketed (cpu, mem, disk, net) averages; gaps come back as zeros.

    Opens its own connection so it can run on the task thread pool.
    """
    span = max(end_ts - start_ts, 1.0)
    width = span / buckets
    db = _read_only_connection()
    if db is None:
        return [(0.0, 0.0, 0.0, 0.0)] * buckets
    try:
        rows = db.execute(
            """
            SELECT CAST((ts - ?) / ? AS INTEGER) AS bucket,
                   AVG(cpu), AVG(mem), AVG(disk), AVG(net)
            FROM samples
            WHERE uuid = ? AND ts >= ? AND ts < ?
            GROUP BY bucket
            """,
            (start_ts, width, uuid, start_ts, end_ts),
        ).fetchall()
    finally:
        db.close()
    out = [(0.0, 0.0, 0.0, 0.0)] * buckets
    for bucket, cpu, mem, disk, net in rows:
        if 0 <= bucket < buckets:
            out[bucket] = (cpu or 0.0, mem or 0.0, disk or 0.0, net or 0.0)
    return out


def data_extent(uuid: str) -> tuple[float, float] | None:
    """(oldest, newest) sample timestamps for a domain, or None if no data."""
    db = _read_only_connection()
    if db is None:
        return None
    try:
        row = db.execute(
            "SELECT MIN(ts), MAX(ts) FROM samples WHERE uuid = ?", (uuid,)
        ).fetchone()
    finally:
        db.close()
    if row and row[0] is not None:
        return float(row[0]), float(row[1])
    return None
