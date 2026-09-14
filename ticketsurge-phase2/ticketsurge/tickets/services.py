"""
Booking business logic.

Two layers of concurrency control, with clearly different jobs:

1. Redis hold  (tickets/redis_holds.py) — optimistic, fast, and the checkout
   timer. Rejects obviously-conflicting requests before they reach the DB and
   reserves seats during a user's think/pay time WITHOUT holding a database
   lock open across that time.

2. Postgres row lock (select_for_update, below) — the authoritative guarantee.
   Two transactions cannot hold the same seat row lock at once; the second
   blocks, then re-reads the row, sees it is already BOOKED, and loses. This is
   what makes overselling impossible even if Redis is bypassed, flushed, or a
   hold expires at the wrong moment.

You cannot hold a DB row lock for the 7-minute checkout window (it would pin a
connection and block everyone), which is exactly why the two layers exist.
"""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from . import redis_holds
from .models import Booking, Event, IdempotencyKey, Seat


class BookingError(Exception):
    """Base class for booking failures the API turns into 4xx responses."""


class SeatNotInEvent(BookingError):
    pass


class SeatUnavailable(BookingError):
    pass


def _validate_seats_in_event(event: Event, seat_ids: list[int], seats: list[Seat]):
    if len(seats) != len(seat_ids):
        found = {s.id for s in seats}
        missing = [sid for sid in seat_ids if sid not in found]
        raise SeatNotInEvent(f"Seats not part of this event: {missing}")


def hold_seats(*, user, event: Event, seat_ids: list[int]):
    """
    Phase-2 step 13: reserve seats for a user with a TTL.

    Validates the seats belong to the event and are currently AVAILABLE in the
    database, then takes an all-or-nothing Redis hold. Returns the TTL so the
    client can show a countdown.
    """
    seat_ids = list(dict.fromkeys(seat_ids))
    if not seat_ids:
        raise BookingError("At least one seat is required.")

    seats = list(Seat.objects.filter(event=event, id__in=seat_ids))
    _validate_seats_in_event(event, seat_ids, seats)

    already_booked = [str(s) for s in seats if s.status == Seat.Status.BOOKED]
    if already_booked:
        raise SeatUnavailable(f"Seats already booked: {already_booked}")

    ok, conflict = redis_holds.acquire_holds(seat_ids, user.id)
    if not ok:
        raise SeatUnavailable(f"Seat {conflict} is being held by another user.")

    from django.conf import settings

    return {"seats": seat_ids, "hold_ttl_seconds": settings.SEAT_HOLD_TTL_SECONDS}


@transaction.atomic
def create_booking(
    *, user, event: Event, seat_ids: list[int], idempotency_key: str | None = None
) -> tuple[Booking, bool]:
    """
    Confirm a booking (Phase-2 steps 14 & 15). Returns (booking, created).

    ``created`` is False when an idempotency key replays a prior booking.
    """
    seat_ids = list(dict.fromkeys(seat_ids))
    if not seat_ids:
        raise BookingError("At least one seat is required.")

    # --- Idempotency ---------------------------------------------------------
    # Lock the key row so two concurrent requests with the same key serialize
    # here. The unique constraint makes a duplicate insert block until the first
    # transaction commits; the loser then reads the committed row and replays.
    idem = None
    if idempotency_key:
        idem = (
            IdempotencyKey.objects.select_for_update()
            .filter(key=idempotency_key)
            .first()
        )
        if idem is None:
            try:
                idem = IdempotencyKey.objects.create(key=idempotency_key)
            except IntegrityError:
                idem = (
                    IdempotencyKey.objects.select_for_update()
                    .get(key=idempotency_key)
                )
        if idem.booking_id:
            return idem.booking, False  # replay the original result

    # --- Respect active holds by other users ---------------------------------
    # Courtesy check so we don't confirm a seat someone else is mid-checkout on.
    # Not the correctness guarantee (that's the row lock below); a stale/absent
    # hold simply falls through to the database check.
    owner_token = f"user:{user.id}"
    for sid in seat_ids:
        holder = redis_holds.holder_of(sid)
        if holder is not None and holder != owner_token:
            raise SeatUnavailable(f"Seat {sid} is being held by another user.")

    # --- Authoritative gate: lock the seat rows ------------------------------
    seats = list(
        Seat.objects.select_for_update()
        .filter(event=event, id__in=seat_ids)
        .order_by("id")  # stable lock order avoids deadlocks across requests
    )
    _validate_seats_in_event(event, seat_ids, seats)

    taken = [str(s) for s in seats if s.status != Seat.Status.AVAILABLE]
    if taken:
        raise SeatUnavailable(f"Seats no longer available: {taken}")

    booking = Booking.objects.create(
        user=user, event=event, status=Booking.Status.CONFIRMED
    )
    Seat.objects.filter(id__in=[s.id for s in seats]).update(
        status=Seat.Status.BOOKED, booking=booking
    )

    if idem is not None:
        idem.booking = booking
        idem.save(update_fields=["booking"])

    # Seats are permanently booked now; drop the transient holds we own.
    transaction.on_commit(lambda: redis_holds.release_holds(seat_ids, user.id))

    return booking, True


def get_demo_user():
    """Phase-1 convenience: attribute unauthenticated bookings to a demo user."""
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username="demo", defaults={"email": "demo@ticketsurge.local"}
    )
    return user
