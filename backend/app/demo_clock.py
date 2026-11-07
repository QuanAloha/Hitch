"""A shared, adjustable demo clock.

The clock is real time plus an offset kept in PostgreSQL (`demo_state`), so the
API process and the `app.demo` CLI always agree on "now". Moving the offset
forward is how the demo "forwards time": route locks, availability checks and
the frontend (which fetches /api/demo/clock) all follow it.
"""

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from app.database import get_db_connection, initialize_database

LOCAL_TZ = ZoneInfo("America/Denver")
_CACHE_SECONDS = 1.0
_cache: tuple[float, float] | None = None  # (monotonic fetched_at, offset seconds)


def _read_offset() -> float:
    global _cache
    if _cache and time.monotonic() - _cache[0] < _CACHE_SECONDS:
        return _cache[1]
    try:
        with get_db_connection() as connection:
            row = connection.execute(
                "SELECT value FROM demo_state WHERE key = 'clock_offset_seconds'"
            ).fetchone()
        offset = float(row[0]) if row else 0.0
    except (psycopg.Error, RuntimeError):
        offset = _cache[1] if _cache else 0.0  # database down: keep the last known offset
    _cache = (time.monotonic(), offset)
    return offset


def now() -> datetime:
    """Current demo time (timezone-aware, UTC)."""
    return datetime.now(timezone.utc) + timedelta(seconds=_read_offset())


def local_now() -> datetime:
    return now().astimezone(LOCAL_TZ)


def offset_seconds() -> float:
    return _read_offset()


def set_offset(seconds: float) -> None:
    global _cache
    initialize_database()
    with get_db_connection() as connection:
        connection.execute(
            "INSERT INTO demo_state (key, value) VALUES ('clock_offset_seconds', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (Jsonb(seconds),),
        )
    _cache = (time.monotonic(), float(seconds))


def set_to(target: datetime) -> None:
    """Make `now()` equal `target` from this moment on."""
    set_offset((target - datetime.now(timezone.utc)).total_seconds())


def reset() -> None:
    set_offset(0.0)
