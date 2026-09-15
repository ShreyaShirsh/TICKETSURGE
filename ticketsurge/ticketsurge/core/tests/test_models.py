import pytest
from django.db import IntegrityError, transaction

from core.models import Booking, Seat


@pytest.mark.django_db
def test_seat_check_constraint_blocks_negative_availability(seat):
    seat.available = 0
    seat.save()
    # The ORM update() bypasses model-level validation entirely, so this
    # is a direct test of the DB-level CHECK constraint — the last-line
    # backstop behind the atomic conditional decrement in core.booking.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Seat.objects.filter(pk=seat.pk).update(available=-1)


@pytest.mark.django_db
def test_unique_seat_per_event(event):
    Seat.objects.create(event=event, section="A", row="1", number=1)
    with pytest.raises(IntegrityError):
        Seat.objects.create(event=event, section="A", row="1", number=1)


@pytest.mark.django_db
def test_booking_idempotency_key_is_unique(seat, customer):
    Booking.objects.create(seat=seat, customer=customer, idempotency_key="dup-key")
    other_seat = Seat.objects.create(event=seat.event, section="A", row="1", number=2)
    with pytest.raises(IntegrityError):
        Booking.objects.create(seat=other_seat, customer=customer, idempotency_key="dup-key")
