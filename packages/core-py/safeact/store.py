"""
The durable store and the single-call state machine.

This file is where correctness lives. The ordering contract is:

    COMMIT(key, ATTEMPTING)   <-- durable "I am about to do it" marker
    execute the side effect
    COMMIT(key, SUCCEEDED, result)

A crash between the first COMMIT and the last leaves the row ATTEMPTING; the next
call with that key can therefore SEE that an attempt was in flight and refuse to
blindly retry (it raises InDoubtError instead of silently duplicating).

Persistence is stdlib sqlite3 only. One file on disk. No server.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .errors import FailedCallError, InDoubtError, SafeError

# State constants
ATTEMPTING = "ATTEMPTING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
IN_DOUBT = "IN_DOUBT"

# Sentinel stored in the `result` column when a call succeeded but its return value
# was not JSON-serializable. We must record SUCCEEDED regardless (the side effect
# happened — never re-run it), but we cannot replay a value we couldn't store.
_UNSERIALIZABLE = {"__safeact_unserializable__": True}


@dataclass(frozen=True)
class Record:
    """A row from the calls table — also the receipt an InDoubtError carries."""

    key: str
    state: str
    result: Optional[str]
    error: Optional[str]
    fn_name: Optional[str]
    args_hash: Optional[str]
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _args_hash(args: tuple, kwargs: dict) -> str:
    """Best-effort fingerprint for the in-doubt receipt. NEVER used for keying."""
    try:
        raw = repr((args, sorted(kwargs.items())))
    except Exception:
        raw = "<unhashable-args>"
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


# Process-wide cache so multiple @safe decorators on the same path share one Store
# (and one schema-init). Keyed by absolute-ish path string.
_STORES: dict[str, "Store"] = {}
_STORES_LOCK = threading.Lock()


def get_store(store) -> "Store":
    """Resolve a str path (or an existing Store) to a cached Store instance."""
    if isinstance(store, Store):
        return store
    if not isinstance(store, str):
        raise TypeError("store must be a file path (str) or a Store instance")
    with _STORES_LOCK:
        s = _STORES.get(store)
        if s is None:
            s = Store(store)
            _STORES[store] = s
        return s


class Store:
    def __init__(self, path: str):
        self.path = path
        # Test-only injection point. In production this is a no-op. Tests set it to
        # raise, simulating process death in the execute->record gap for a given key.
        self._gap_hook: Callable[[str], None] = lambda key: None
        self._init_schema()

    # ---- connection management -------------------------------------------------
    # One short-lived connection per atomic operation. This makes cross-thread and
    # cross-"process" (new Store on same file) use trivially correct, and models a
    # restart naturally: every call already talks to a fresh connection.

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None  # we issue BEGIN/COMMIT ourselves
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS calls (
                    key        TEXT PRIMARY KEY,
                    state      TEXT NOT NULL,
                    result     TEXT,
                    error      TEXT,
                    fn_name    TEXT,
                    args_hash  TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        finally:
            conn.close()

    # ---- the state machine -----------------------------------------------------

    def run(self, key: str, fn: Callable, args: tuple, kwargs: dict, fn_name: str | None = None) -> Any:
        """
        Execute `fn(*args, **kwargs)` at most once for `key`, durably.

        Returns the (possibly replayed) result. Raises InDoubtError if the key is in
        the unrecoverable gap, FailedCallError if the key already FAILED, or whatever
        `fn` raised on a fresh failing attempt.
        """
        if not isinstance(key, str) or not key:
            raise TypeError(
                "idempotency key must be a non-empty string, supplied as the first "
                "positional argument. Refusing to run without one."
            )
        ah = _args_hash(args, kwargs)

        # STEP 1: claim the key by committing an ATTEMPTING row. This COMMIT is the
        # crux of the whole library.
        if not self._claim(key, fn_name, ah):
            # Someone already owns this key (prior attempt, or a concurrent caller).
            return self._resolve_existing(key)

        # STEP 2: run the real side effect. Only a failure HERE means FAILED.
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            self._record_failed(key, exc)
            raise

        # ---- THE GAP -----------------------------------------------------------
        # If the process dies right here, the side effect happened but the row is
        # still ATTEMPTING. The next call will detect that and go IN_DOUBT.
        self._gap_hook(key)  # production: no-op. tests: may raise to simulate death.

        # STEP 3: record success. Guarded on state=ATTEMPTING so a concurrent path
        # that already flipped us to IN_DOUBT is not clobbered.
        self._record_succeeded(key, result)
        return result

    async def arun(self, key: str, fn: Callable, args: tuple, kwargs: dict, fn_name: str | None = None) -> Any:
        """
        Async twin of `run`: awaits an async `fn`, with the identical exactly-once
        contract (claim -> await side effect -> record). The durable record is
        written synchronously (SQLite ops are fast); only the user's side effect is
        awaited. In-doubt and ordering semantics are the same as `run`.
        """
        if not isinstance(key, str) or not key:
            raise TypeError(
                "idempotency key must be a non-empty string, supplied as the first "
                "positional argument. Refusing to run without one."
            )
        ah = _args_hash(args, kwargs)

        if not self._claim(key, fn_name, ah):
            return self._resolve_existing(key)

        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:
            self._record_failed(key, exc)
            raise

        # ---- THE GAP (same as run) ----
        self._gap_hook(key)
        self._record_succeeded(key, result)
        return result

    def _claim(self, key: str, fn_name: str | None, args_hash: str) -> bool:
        """INSERT (key, ATTEMPTING) and COMMIT. False if the key already exists."""
        now = _now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO calls (key, state, fn_name, args_hash, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, ATTEMPTING, fn_name, args_hash, now, now),
            )
            conn.execute("COMMIT")
            return True
        except sqlite3.IntegrityError:
            # UNIQUE(key) violation — the concurrency guard doing its job.
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            return False
        finally:
            conn.close()

    def _resolve_existing(self, key: str) -> Any:
        rec = self.get(key)
        if rec is None:
            # Extremely rare: row vanished between claim-fail and read. Be honest.
            raise SafeError(f"key {key!r} could not be resolved (row missing after conflict)")
        if rec.state == SUCCEEDED:
            return self._from_json(rec)  # the idempotency win: replay, do not re-run
        if rec.state == FAILED:
            raise FailedCallError(rec)   # refuse-by-default
        if rec.state == IN_DOUBT:
            raise InDoubtError(rec)
        if rec.state == ATTEMPTING:
            # We cannot distinguish a live concurrent winner from a crashed corpse
            # from one snapshot. The honest move is IN_DOUBT. (A lease/heartbeat
            # could separate the two later — EXTENSION POINT, see DESIGN §7.)
            self._mark_in_doubt(key)
            return_rec = self.get(key)
            raise InDoubtError(return_rec if return_rec else rec)
        raise SafeError(f"key {key!r} in unknown state {rec.state!r}")

    def _record_succeeded(self, key: str, result: Any) -> None:
        payload = self._to_json(result)
        now = _now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE calls SET state=?, result=?, updated_at=? WHERE key=? AND state=?",
                (SUCCEEDED, payload, now, key, ATTEMPTING),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()

    def _record_failed(self, key: str, exc: Exception) -> None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE calls SET state=?, error=?, updated_at=? WHERE key=? AND state=?",
                (FAILED, f"{type(exc).__name__}: {exc}", now, key, ATTEMPTING),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()

    def _mark_in_doubt(self, key: str) -> None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE calls SET state=?, updated_at=? WHERE key=? AND state=?",
                (IN_DOUBT, now, key, ATTEMPTING),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()

    # ---- serialization ---------------------------------------------------------

    @staticmethod
    def _to_json(result: Any) -> str:
        try:
            return json.dumps(result)
        except (TypeError, ValueError):
            # The side effect ran; we simply can't store this value. Record a marker
            # so we never re-run — replay will raise a clear error instead.
            return json.dumps(_UNSERIALIZABLE)

    @staticmethod
    def _from_json(rec: Record) -> Any:
        value = json.loads(rec.result) if rec.result is not None else None
        if value == _UNSERIALIZABLE:
            raise SafeError(
                f"key {rec.key!r} SUCCEEDED but its result was not JSON-serializable, "
                f"so it cannot be replayed. The side effect already happened — do not re-run."
            )
        return value

    # ---- read / resolve (making IN_DOUBT actionable) ---------------------------

    def get(self, key: str) -> Optional[Record]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT key, state, result, error, fn_name, args_hash, created_at, updated_at "
                "FROM calls WHERE key=?",
                (key,),
            ).fetchone()
        finally:
            conn.close()
        return Record(**dict(row)) if row else None

    def list_in_doubt(self) -> list[Record]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT key, state, result, error, fn_name, args_hash, created_at, updated_at "
                "FROM calls WHERE state=? ORDER BY created_at",
                (IN_DOUBT,),
            ).fetchall()
        finally:
            conn.close()
        return [Record(**dict(r)) for r in rows]

    def resolve(self, key: str, happened: bool, result: Any = None) -> None:
        """
        Human/reconciliation lever to close out an IN_DOUBT key after checking the
        downstream system. `happened=True` -> SUCCEEDED (with the known result);
        `happened=False` -> FAILED. This is what makes in-doubt actionable, not a
        tombstone. (Auto-reconciliation is out of scope — see DESIGN §7.)
        """
        rec = self.get(key)
        if rec is None:
            raise SafeError(f"cannot resolve unknown key {key!r}")
        if rec.state != IN_DOUBT:
            raise SafeError(f"key {key!r} is {rec.state}, not IN_DOUBT; nothing to resolve")
        now = _now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if happened:
                conn.execute(
                    "UPDATE calls SET state=?, result=?, updated_at=? WHERE key=? AND state=?",
                    (SUCCEEDED, self._to_json(result), now, key, IN_DOUBT),
                )
            else:
                conn.execute(
                    "UPDATE calls SET state=?, error=?, updated_at=? WHERE key=? AND state=?",
                    (FAILED, "resolved: did not happen", now, key, IN_DOUBT),
                )
            conn.execute("COMMIT")
        finally:
            conn.close()
