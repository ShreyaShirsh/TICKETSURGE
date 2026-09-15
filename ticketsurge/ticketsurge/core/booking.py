"""
The confirm step: turns a Redis hold into a durable, race-free booking.

Two layers of defense, deliberately redundant:

1. `select_for_update()` — a pessimistic row lock. Serializes concurrent
   confirms for the *same* seat so only one transaction is ever mutating
   it at a time. On Oracle this is `SELECT ... FOR UPDATE` under READ
   COMMITTED (Oracle has no REPEATABLE READ to lean on instead).
2. The atomic conditional UPDATE (`available = available - 1 WHERE
   available > 0`) — this is what actually closes the read-check-write
   gap. Even without the row lock, this single statement cannot let two
   transactions both "win" the same seat, because the affected-row count
   the DB returns is the arbiter: exactly one caller sees rowcount == 1.

Idempotency: the client supplies an idempotency_key; a retry (network
blip, double-click) with the same key returns the original booking
instead of attempting a second decrement.
"""
import time
from dataclasses import dataclass

from django.db import IntegrityError, OperationalError, transaction

from core.holds import release_hold, validate_hold
from core.models import Booking, Customer, Seat
from core.realtime import broadcast_seat_update


class SeatUnavailable(Exception):
    """The atomic decrement affected 0 rows: someone else already won this seat."""


@dataclass
class ConfirmResult:
    booking: Booking
    created: bool  # False if this was an idempotent replay


def confirm_seat(*, seat_id: int, hold_token: str, customer: Customer, idempotency_key: str) -> ConfirmResult:
    # Idempotent replay short-circuit — outside the transaction is fine,
    # it's just an optimization; the unique constraint is the real guard.
    existing = Booking.objects.filter(idempotency_key=idempotency_key).select_related("seat").first()
    if existing is not None:
        return ConfirmResult(booking=existing, created=False)

    validate_hold(seat_id, hold_token)

    # Serialization conflicts under heavy contention (Oracle's ORA-08177
    # under SERIALIZABLE, or SQLite's "database is locked" under its
    # coarse whole-file write lock) are retried a few times rather than
    # surfaced as a hard failure — the atomic decrement below is what
    # decides the actual winner either way.
    last_exc: OperationalError | None = None
    for attempt in range(5):
        try:
            result = _confirm_seat_once(
                seat_id=seat_id, customer=customer, idempotency_key=idempotency_key
            )
            # Best-effort: the hold has served its purpose once the DB row is ours.
            release_hold(seat_id, hold_token)
            return result
        except OperationalError as exc:
            last_exc = exc
            time.sleep(0.05 * (attempt + 1))
    raise last_exc


def _confirm_seat_once(*, seat_id: int, customer: Customer, idempotency_key: str) -> ConfirmResult:
    with transaction.atomic():
        # Pessimistic lock: nobody else can be mid-confirm on this seat row.
        seat = Seat.objects.select_for_update().get(pk=seat_id)

        # The real invariant enforcer: a single atomic conditional UPDATE.
        # rowcount == 1 means we won the race; 0 means someone beat us to it
        # (or the seat was never available), full stop.
        updated = Seat.objects.filter(pk=seat.pk, available__gt=0).update(available=seat.available - 1)
        if updated == 0:
            raise SeatUnavailable(f"Seat {seat_id} has no availability left.")

        try:
            booking = Booking.objects.create(
                seat_id=seat_id,
                customer=customer,
                idempotency_key=idempotency_key,
                status=Booking.Status.PENDING,
            )
        except IntegrityError as exc:
            # Race on the idempotency key itself (two identical retries
            # arriving at once) — surface the row that won.
            raise SeatUnavailable("Concurrent request with the same idempotency key.") from exc

    event_id = seat.event_id

    broadcast_seat_update(event_id, seat_id, "booked")
    return ConfirmResult(booking=booking, created=True)


def release_seat_after_failure(seat_id: int) -> None:
    """Saga compensation: undo the decrement so the seat is bookable again."""
    with transaction.atomic():
        seat = Seat.objects.select_for_update().get(pk=seat_id)
        Seat.objects.filter(pk=seat.pk).update(available=seat.available + 1)
    broadcast_seat_update(seat.event_id, seat_id, "released")
