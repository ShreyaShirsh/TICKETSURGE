from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from core.booking import SeatUnavailable, confirm_seat
from core.holds import HoldConflict, InvalidHold, place_hold, validate_hold
from core.models import Booking, Ticket
from core.tasks import run_booking_saga


@pytest.mark.django_db
def test_place_hold_then_second_hold_conflicts(seat):
    token, _ = place_hold(seat.id)
    validate_hold(seat.id, token)  # does not raise
    with pytest.raises(HoldConflict):
        place_hold(seat.id)


@pytest.mark.django_db
def test_confirm_without_valid_hold_is_rejected(seat, customer):
    with pytest.raises(InvalidHold):
        confirm_seat(seat_id=seat.id, hold_token="not-a-real-token", customer=customer, idempotency_key="k")


@pytest.mark.django_db
def test_confirm_decrements_seat_and_creates_pending_booking(seat, customer):
    token, _ = place_hold(seat.id)
    result = confirm_seat(seat_id=seat.id, hold_token=token, customer=customer, idempotency_key="k1")
    seat.refresh_from_db()
    assert seat.available == 0
    assert result.created is True
    assert result.booking.status == Booking.Status.PENDING


@pytest.mark.django_db
def test_confirm_is_idempotent_on_retry(seat, customer):
    token, _ = place_hold(seat.id)
    first = confirm_seat(seat_id=seat.id, hold_token=token, customer=customer, idempotency_key="same-key")
    # Simulate a client retry with the identical idempotency key (e.g. a
    # timed-out response the client resends). No second decrement.
    second = confirm_seat(seat_id=seat.id, hold_token=token, customer=customer, idempotency_key="same-key")
    seat.refresh_from_db()
    assert seat.available == 0
    assert first.booking.id == second.booking.id
    assert second.created is False


@pytest.mark.django_db
def test_confirm_fails_once_seat_already_booked(seat, customer):
    token, _ = place_hold(seat.id)
    confirm_seat(seat_id=seat.id, hold_token=token, customer=customer, idempotency_key="k1")
    # A second, different confirm attempt against the now-sold seat
    # (bypassing the hold check entirely) must still be rejected by the
    # atomic decrement — this is the real invariant, not the hold.
    from core.booking import _confirm_seat_once
    with pytest.raises(SeatUnavailable):
        _confirm_seat_once(seat_id=seat.id, customer=customer, idempotency_key="k2")


@pytest.mark.django_db
def test_saga_success_confirms_booking_and_issues_ticket(seat, customer):
    token, _ = place_hold(seat.id)
    result = confirm_seat(seat_id=seat.id, hold_token=token, customer=customer, idempotency_key="k1")
    with patch("core.tasks.simulate_payment", return_value=True):
        run_booking_saga.apply(args=[result.booking.id])
    result.booking.refresh_from_db()
    assert result.booking.status == Booking.Status.CONFIRMED
    assert Ticket.objects.filter(booking=result.booking).exists()


@pytest.mark.django_db
def test_saga_failure_compensates_and_releases_seat(seat, customer):
    token, _ = place_hold(seat.id)
    result = confirm_seat(seat_id=seat.id, hold_token=token, customer=customer, idempotency_key="k1")
    with patch("core.tasks.simulate_payment", return_value=False):
        run_booking_saga.apply(args=[result.booking.id])

    result.booking.refresh_from_db()
    seat.refresh_from_db()

    assert result.booking.status == Booking.Status.FAILED_DEAD_LETTER
    assert seat.available == 1  # fully restored — no seat stuck unavailable
    assert not Ticket.objects.filter(booking=result.booking).exists()  # no orphaned ticket


@pytest.mark.django_db
def test_full_api_flow_hold_confirm(event, seat, django_capture_on_commit_callbacks):
    client = APIClient()

    hold_resp = client.post(f"/api/seats/{seat.id}/hold/")
    assert hold_resp.status_code == 201
    token = hold_resp.data["hold_token"]

    # The saga is enqueued via transaction.on_commit(); a plain @django_db
    # test never actually commits, so on_commit hooks don't fire unless we
    # capture and run them explicitly (this fixture does both).
    with django_capture_on_commit_callbacks(execute=True):
        confirm_resp = client.post(
            "/api/bookings/confirm/",
            {
                "seat_id": seat.id,
                "hold_token": token,
                "idempotency_key": "api-key-1",
                "customer_email": "api@example.com",
                "customer_name": "API Tester",
            },
            format="json",
        )
    assert confirm_resp.status_code == 201
    assert confirm_resp.data["status"] == "PENDING"

    # Saga already ran (CELERY_TASK_ALWAYS_EAGER=1 in tests via fixture).
    booking = Booking.objects.get(pk=confirm_resp.data["id"])
    assert booking.status == Booking.Status.CONFIRMED


@pytest.mark.django_db
def test_api_rejects_hold_on_already_booked_seat(seat, customer):
    token, _ = place_hold(seat.id)
    confirm_seat(seat_id=seat.id, hold_token=token, customer=customer, idempotency_key="k1")

    client = APIClient()
    resp = client.post(f"/api/seats/{seat.id}/hold/")
    assert resp.status_code == 409
