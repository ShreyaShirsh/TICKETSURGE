"""
Short-lived seat holds in Redis.

A hold is the *optimistic* first gate and the checkout-timer UX: when a user
picks seats we reserve them for a few minutes so nobody else can start checking
out with the same seats. Holds auto-expire via a TTL, so an abandoned checkout
frees its seats with no cleanup job.

Correctness note: Redis holds do NOT guarantee no overselling on their own.
Redis is not transactional with Postgres and can be flushed or restarted. The
authoritative guarantee lives in the confirm step's Postgres row locks
(see services.create_booking). Holds reduce contention and power the timer;
the database is the source of truth.
"""

from django.conf import settings
import redis

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def _key(seat_id: int) -> str:
    return f"seat:{seat_id}"


def _owner(user_id: int) -> str:
    return f"user:{user_id}"


# Release only if WE still own the key. Doing get-then-del non-atomically would
# risk deleting a hold that expired and was re-acquired by someone else between
# our GET and DEL. The Lua script makes compare-and-delete atomic.
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


def acquire_holds(seat_ids: list[int], user_id: int) -> tuple[bool, int | None]:
    """
    Try to hold every seat with SET seat:{id} user:{id} NX EX <ttl>.

    All-or-nothing across seats. Re-holding a seat you already own is idempotent
    and refreshes its TTL. If any seat is held by someone else we roll back only
    the holds this call actually created (never someone's pre-existing hold) and
    report the conflicting seat. Returns (True, None) or (False, seat_id).
    """
    r = get_redis()
    ttl = settings.SEAT_HOLD_TTL_SECONDS
    owner = _owner(user_id)
    newly_acquired: list[int] = []
    for sid in seat_ids:
        if r.set(_key(sid), owner, nx=True, ex=ttl):
            newly_acquired.append(sid)
        elif r.get(_key(sid)) == owner:
            r.expire(_key(sid), ttl)  # already ours — refresh the timer
        else:
            for got in newly_acquired:
                release_hold(got, user_id)
            return False, sid
    return True, None


def release_hold(seat_id: int, user_id: int) -> bool:
    r = get_redis()
    return bool(r.eval(_RELEASE_LUA, 1, _key(seat_id), _owner(user_id)))


def release_holds(seat_ids: list[int], user_id: int) -> None:
    for sid in seat_ids:
        release_hold(sid, user_id)


def holder_of(seat_id: int) -> str | None:
    """Return the raw owner token ('user:{id}') holding this seat, or None."""
    return get_redis().get(_key(seat_id))
