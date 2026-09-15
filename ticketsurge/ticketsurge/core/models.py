import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Customer(models.Model):
    """Kept separate from Django's auth User so the domain model stands on
    its own (and stays simple to port across DB backends)."""

    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.display_name or self.email


class Event(models.Model):
    name = models.CharField(max_length=200)
    venue = models.CharField(max_length=200)
    starts_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at"]
        indexes = [models.Index(fields=["starts_at"])]

    def __str__(self):
        return self.name


class Seat(models.Model):
    """One row = one physical, assignable seat.

    `available` is intentionally a small integer rather than a boolean:
    it is what the atomic conditional-decrement statement in
    `core.holds.confirm_seat` acts on (`available = available - 1 WHERE
    available > 0`), which is the actual invariant enforcer against
    overselling — the CHECK constraint below is the last-line backstop,
    not the primary defense.
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="seats")
    section = models.CharField(max_length=40)
    row = models.CharField(max_length=10)
    number = models.PositiveIntegerField()
    price_cents = models.PositiveIntegerField(default=5000)
    available = models.PositiveSmallIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(available__gte=0), name="seat_available_gte_0"),
            models.UniqueConstraint(
                fields=["event", "section", "row", "number"], name="unique_seat_per_event"
            ),
        ]
        indexes = [
            models.Index(fields=["event", "available"]),
        ]
        ordering = ["section", "row", "number"]

    def __str__(self):
        return f"{self.event.name} {self.section}-{self.row}{self.number}"

    @property
    def is_available(self):
        return self.available > 0


class Booking(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        FAILED = "FAILED", "Failed"
        FAILED_DEAD_LETTER = "FAILED_DEAD_LETTER", "Failed (dead-lettered)"
        CANCELLED = "CANCELLED", "Cancelled"

    seat = models.OneToOneField(Seat, on_delete=models.PROTECT, related_name="booking")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="bookings")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    idempotency_key = models.CharField(max_length=64, unique=True)
    failure_reason = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status"])]

    def __str__(self):
        return f"Booking#{self.pk} seat={self.seat_id} status={self.status}"


class Ticket(models.Model):
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name="ticket")
    ticket_code = models.CharField(max_length=36, default=uuid.uuid4, unique=True, editable=False)
    issued_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Ticket({self.ticket_code})"
