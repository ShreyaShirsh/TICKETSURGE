"""
The contention test: fires many simultaneous confirm attempts at a
single seat and asserts exactly one wins. This is the load-bearing
proof for the whole project's core claim (Phase 3 "done when").

These tests call `_confirm_seat_once` directly rather than going
through the Redis hold layer, deliberately: Redis's `SET NX EX` already
guarantees only one caller can ever hold a given seat (that's covered
in test_booking_flow.py), so racing many threads for the *same* hold
token would just be testing Redis. What actually needs proving is that
the DATABASE layer — select_for_update + the atomic conditional
decrement — is itself sufficient to prevent overselling even if the
hold layer were bypassed entirely (a buggy client, a direct API call,
an admin script). That's the real, defense-in-depth claim this project
makes, and it's what these tests exercise under genuine OS-thread
concurrency against a real database connection per thread.

Run against PostgreSQL (or Oracle/MySQL) for a real test of row-level
locking under contention — SQLite's coarse whole-file write lock makes
it a poor stand-in for this specific test, even though the app code
itself is portable across all four backends.
"""
import random
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from django import db as django_db
from django.db import connection

from core.booking import SeatUnavailable, _confirm_seat_once
from core.models import Booking, Customer, Seat

N_CONCURRENT = 25

skip_on_sqlite = pytest.mark.skipif(
    connection.vendor == "sqlite",
    reason=(
        "SQLite's whole-file write lock isn't real row-level concurrency — "
        "run with DB_ENGINE=postgres (or mysql/oracle) to exercise this."
    ),
)


def _attempt_confirm(seat_id, customer_id, key):
    """Runs in a worker thread — Django gives each new thread its own
    lazily-created DB connection, so this is genuine multi-connection
    contention, not a simulation of it."""
    try:
        customer = Customer.objects.get(pk=customer_id)
        result = _confirm_seat_once(seat_id=seat_id, customer=customer, idempotency_key=key)
        return ("won", result.booking.id)
    except SeatUnavailable:
        return ("lost", None)
    finally:
        django_db.connections.close_all()


@skip_on_sqlite
@pytest.mark.django_db(transaction=True)
def test_exactly_one_winner_under_concurrent_confirms(event, customer):
    """N threads race to confirm the SAME seat. Only one may win; the
    seat's availability must never go negative and exactly one Booking
    row must exist for it afterwards."""
    seat = Seat.objects.create(event=event, section="Z", row="1", number=1, available=1)

    results = []
    with ThreadPoolExecutor(max_workers=N_CONCURRENT) as pool:
        futures = [
            pool.submit(_attempt_confirm, seat.id, customer.id, f"key-{i}-{uuid.uuid4()}")
            for i in range(N_CONCURRENT)
        ]
        for f in as_completed(futures):
            results.append(f.result())

    wins = [r for r in results if r[0] == "won"]
    losses = [r for r in results if r[0] == "lost"]

    assert len(wins) == 1, f"expected exactly one winner, got {len(wins)}: {wins}"
    assert len(losses) == N_CONCURRENT - 1

    seat.refresh_from_db()
    assert seat.available == 0, "seat availability must never go negative or double-decrement"
    assert Booking.objects.filter(seat=seat).count() == 1


@skip_on_sqlite
@pytest.mark.django_db(transaction=True)
def test_no_oversell_across_many_seats_under_load(event, customer):
    """A small flash-sale simulation: a fixed pool of seats, many more
    confirm attempts than seats, each attempt targeting a random seat.
    Total successful bookings must exactly equal total seats — the
    oversell count (successes - seats) must be zero."""
    n_seats = 10
    attempts_per_seat = 8
    seats = [
        Seat.objects.create(event=event, section="Y", row="1", number=i, available=1)
        for i in range(n_seats)
    ]

    jobs = []
    for s in seats:
        for i in range(attempts_per_seat):
            jobs.append((s.id, f"multi-key-{s.id}-{i}"))
    random.shuffle(jobs)

    def worker(seat_id, key):
        try:
            customer_local = Customer.objects.get(pk=customer.id)
            _confirm_seat_once(seat_id=seat_id, customer=customer_local, idempotency_key=key)
            return True
        except SeatUnavailable:
            return False
        finally:
            django_db.connections.close_all()

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(worker, sid, key) for sid, key in jobs]
        outcomes = [f.result() for f in as_completed(futures)]

    successes = sum(outcomes)
    assert successes == n_seats, (
        f"expected exactly {n_seats} successful bookings, got {successes} "
        f"(oversold={successes - n_seats})"
    )

    for s in seats:
        s.refresh_from_db()
        assert s.available == 0
