"""
Phase 3 — the concurrency-safe two-step booking flow.

Step 1 (this module's `place_hold` / `release_hold`): a short-lived,
best-effort claim on a seat, held in Redis with `SET NX EX` so it costs
nothing on the database and expires on its own if the client disappears.

Step 2 (`confirm_seat`, `core.tasks`, `core.saga`): the durable write,
guarded by a row lock *and* an atomic conditional decrement so the
booking is correct even if two confirms race past the Redis check (a
client could in principle skip the hold; the DB is the real source of
truth).
"""
import secrets
import time

import redis
from django.conf import settings

_redis_client = None


def get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.REDIS_HOLDS_DB, decode_responses=True)
    return _redis_client


def _hold_key(seat_id: int) -> str:
    return f"seat:hold:{seat_id}"


class HoldConflict(Exception):
    """Someone else already holds (or has booked) this seat."""


class InvalidHold(Exception):
    """The hold token doesn't match, or the hold has expired."""


def place_hold(seat_id: int, ttl_seconds: int | None = None) -> tuple[str, float]:
    """Atomically claim a seat in Redis. Returns (hold_token, expires_at_epoch).

    Uses SET key value NX EX ttl — a single atomic Redis command, so two
    concurrent holders can never both win.
    """
    ttl = ttl_seconds or settings.SEAT_HOLD_TTL_SECONDS
    token = secrets.token_urlsafe(16)
    r = get_redis()
    ok = r.set(_hold_key(seat_id), token, nx=True, ex=ttl)
    if not ok:
        raise HoldConflict(f"Seat {seat_id} is already held.")
    return token, time.time() + ttl


def get_hold_owner(seat_id: int) -> str | None:
    return get_redis().get(_hold_key(seat_id))


def validate_hold(seat_id: int, hold_token: str) -> None:
    owner = get_hold_owner(seat_id)
    if owner is None:
        raise InvalidHold(f"No active hold on seat {seat_id} (expired or never placed).")
    if owner != hold_token:
        raise InvalidHold(f"Hold token does not match the current holder of seat {seat_id}.")


def release_hold(seat_id: int, hold_token: str | None = None) -> None:
    """Release a hold. If hold_token is given, only releases if it still
    matches (avoids releasing someone else's newer hold on the same seat
    after our TTL already lapsed)."""
    r = get_redis()
    key = _hold_key(seat_id)
    if hold_token is None:
        r.delete(key)
        return
    # Lua script for check-and-delete atomicity (classic Redis distributed-lock pattern).
    script = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    r.eval(script, 1, key, hold_token)
