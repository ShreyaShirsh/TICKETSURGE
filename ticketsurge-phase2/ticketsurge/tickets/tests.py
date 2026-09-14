import threading

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from tickets import redis_holds
from tickets.models import Booking, Event, Seat, Venue
from tickets.services import (
    SeatUnavailable,
    create_booking,
    hold_seats,
)

User = get_user_model()


def make_event_with_seats(n=5):
    venue = Venue.objects.create(name="Test Arena", city="Vellore")
    event = Event.objects.create(
        name="Test Event", venue=venue, starts_at=timezone.now()
    )
    for i in range(1, n + 1):
        Seat.objects.create(event=event, section="A", row="1", number=i)
    return event


def flush_redis():
    redis_holds.get_redis().flushdb()


# --- Phase 1 regression + happy path ---------------------------------------


class BookingServiceTests(APITestCase):
    def setUp(self):
        flush_redis()
        self.user = User.objects.create(username="tester")
        self.event = make_event_with_seats(5)

    def test_happy_path_confirms_booking_and_books_seats(self):
        seat_ids = list(self.event.seats.values_list("id", flat=True)[:2])
        booking, created = create_booking(
            user=self.user, event=self.event, seat_ids=seat_ids
        )
        self.assertTrue(created)
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        booked = Seat.objects.filter(id__in=seat_ids)
        self.assertTrue(all(s.status == Seat.Status.BOOKED for s in booked))

    def test_double_booking_same_seat_is_rejected(self):
        seat_id = self.event.seats.first().id
        create_booking(user=self.user, event=self.event, seat_ids=[seat_id])
        with self.assertRaises(SeatUnavailable):
            create_booking(user=self.user, event=self.event, seat_ids=[seat_id])


class ApiEndpointTests(APITestCase):
    def setUp(self):
        flush_redis()
        self.event = make_event_with_seats(5)

    def test_list_events(self):
        resp = self.client.get(reverse("event-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["results"][0]["seats_available"], 5)

    def test_create_booking_endpoint(self):
        seat_ids = list(self.event.seats.values_list("id", flat=True)[:2])
        resp = self.client.post(
            reverse("booking-create"),
            {"event": self.event.id, "seats": seat_ids},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["status"], Booking.Status.CONFIRMED)


# --- Phase 2: holds --------------------------------------------------------


class HoldTests(APITestCase):
    def setUp(self):
        flush_redis()
        self.a = User.objects.create(username="alice")
        self.b = User.objects.create(username="bob")
        self.event = make_event_with_seats(3)

    def test_hold_blocks_other_user(self):
        seat_id = self.event.seats.first().id
        hold_seats(user=self.a, event=self.event, seat_ids=[seat_id])
        # Bob cannot hold the same seat.
        with self.assertRaises(SeatUnavailable):
            hold_seats(user=self.b, event=self.event, seat_ids=[seat_id])

    def test_reholding_own_seat_is_idempotent(self):
        seat_id = self.event.seats.first().id
        hold_seats(user=self.a, event=self.event, seat_ids=[seat_id])
        # Alice re-holding her own seat succeeds (refreshes the timer).
        result = hold_seats(user=self.a, event=self.event, seat_ids=[seat_id])
        self.assertEqual(result["seats"], [seat_id])

    def test_confirm_respects_another_users_hold(self):
        seat_id = self.event.seats.first().id
        hold_seats(user=self.a, event=self.event, seat_ids=[seat_id])
        # Bob tries to confirm a seat Alice is holding -> rejected.
        with self.assertRaises(SeatUnavailable):
            create_booking(user=self.b, event=self.event, seat_ids=[seat_id])
        # Alice (the holder) can confirm it.
        booking, created = create_booking(
            user=self.a, event=self.event, seat_ids=[seat_id]
        )
        self.assertTrue(created)


# --- Phase 2: idempotency --------------------------------------------------


class IdempotencyTests(APITestCase):
    def setUp(self):
        flush_redis()
        self.user = User.objects.create(username="tester")
        self.event = make_event_with_seats(3)

    def test_same_key_returns_same_booking(self):
        seat_id = self.event.seats.first().id
        b1, c1 = create_booking(
            user=self.user, event=self.event, seat_ids=[seat_id],
            idempotency_key="abc-123",
        )
        b2, c2 = create_booking(
            user=self.user, event=self.event, seat_ids=[seat_id],
            idempotency_key="abc-123",
        )
        self.assertTrue(c1)
        self.assertFalse(c2)  # replay
        self.assertEqual(b1.id, b2.id)
        self.assertEqual(Booking.objects.count(), 1)

    def test_endpoint_idempotency_header(self):
        seat_ids = list(self.event.seats.values_list("id", flat=True)[:1])
        headers = {"HTTP_IDEMPOTENCY_KEY": "req-xyz"}
        r1 = self.client.post(
            reverse("booking-create"),
            {"event": self.event.id, "seats": seat_ids},
            format="json", **headers,
        )
        r2 = self.client.post(
            reverse("booking-create"),
            {"event": self.event.id, "seats": seat_ids},
            format="json", **headers,
        )
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r2.status_code, status.HTTP_200_OK)  # replay
        self.assertEqual(r1.data["reference"], r2.data["reference"])
        self.assertEqual(Booking.objects.count(), 1)


# --- Phase 2: the contention test (steps 16 & 17) --------------------------


class SeatContentionTest(TransactionTestCase):
    """
    Fire many simultaneous confirm requests at a SINGLE seat and assert that
    exactly one wins. Repeated over several rounds to show it holds every time.

    Uses TransactionTestCase (not TestCase) so each thread runs a real,
    committed transaction against Postgres — that is the only way the
    select_for_update row lock actually engages.
    """

    ROUNDS = 5
    THREADS = 25

    def setUp(self):
        flush_redis()
        self.users = [
            User.objects.create(username=f"racer{i}") for i in range(self.THREADS)
        ]

    def _race_for_one_seat(self):
        event = make_event_with_seats(1)
        seat = event.seats.first()

        results = []
        results_lock = threading.Lock()
        start = threading.Barrier(self.THREADS)

        def worker(i):
            start.wait()  # release all threads at once for maximum contention
            try:
                booking, created = create_booking(
                    user=self.users[i], event=event, seat_ids=[seat.id]
                )
                outcome = ("win", booking.id)
            except SeatUnavailable:
                outcome = ("lose", None)
            finally:
                connection.close()  # each thread has its own connection
            with results_lock:
                results.append(outcome)

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(self.THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        wins = [r for r in results if r[0] == "win"]
        losses = [r for r in results if r[0] == "lose"]
        return event, seat, wins, losses

    def test_exactly_one_request_wins_every_time(self):
        for round_no in range(self.ROUNDS):
            event, seat, wins, losses = self._race_for_one_seat()

            self.assertEqual(
                len(wins), 1,
                f"round {round_no}: expected exactly 1 winner, got {len(wins)}",
            )
            self.assertEqual(len(losses), self.THREADS - 1)

            seat.refresh_from_db()
            self.assertEqual(seat.status, Seat.Status.BOOKED)
            # Exactly one booking exists for this event, owning the seat.
            bookings = Booking.objects.filter(event=event)
            self.assertEqual(bookings.count(), 1)
            self.assertEqual(seat.booking_id, bookings.first().id)
