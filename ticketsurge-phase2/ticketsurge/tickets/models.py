import uuid

from django.conf import settings
from django.db import models


class TimeStamped(models.Model):
    """Reusable created/updated audit columns."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Venue(TimeStamped):
    """A physical place where events are held."""

    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=120, blank=True)

    def __str__(self) -> str:
        return self.name


class Event(TimeStamped):
    """A specific happening at a venue at a point in time."""

    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="events")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    starts_at = models.DateTimeField()

    class Meta:
        ordering = ["starts_at"]
        indexes = [models.Index(fields=["starts_at"])]

    def __str__(self) -> str:
        return f"{self.name} @ {self.venue.name}"


class Seat(TimeStamped):
    """
    A single seat made available for one event.

    Design note: for Phase 1 we attach seats directly to an Event and carry the
    availability status on the seat itself. This matches the entity list and the
    "seats for an event" query. A more normalised model would separate a
    physical Seat (belongs to Venue) from a per-event availability row; that is
    noted as a possible refactor but deliberately deferred to keep Phase 1 tight.

    The `status` field is the seat's availability. The nullable `booking` FK
    records which booking currently owns the seat. Keeping status on the row is
    what lets Phase 2 lock individual seat rows (SELECT ... FOR UPDATE) to
    prevent overselling under concurrency.
    """

    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        HELD = "HELD", "Held"        # reserved temporarily (Phase 3)
        BOOKED = "BOOKED", "Booked"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="seats")
    section = models.CharField(max_length=32)
    row = models.CharField(max_length=8)
    number = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.AVAILABLE
    )
    booking = models.ForeignKey(
        "Booking",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="seats",
    )

    class Meta:
        constraints = [
            # A physical position can exist only once per event.
            models.UniqueConstraint(
                fields=["event", "section", "row", "number"],
                name="uniq_seat_position_per_event",
            )
        ]
        indexes = [
            # The hot path: list seats for an event, filter by availability.
            models.Index(fields=["event", "status"]),
        ]
        ordering = ["section", "row", "number"]

    def __str__(self) -> str:
        return f"{self.section}-{self.row}{self.number}"


class Booking(TimeStamped):
    """
    A user's claim on one or more seats for a single event.

    Booking.status is the lifecycle of the order; Seat.status is the state of
    each seat. In Phase 1 (happy path) a created booking goes straight to
    CONFIRMED and its seats to BOOKED. Later phases introduce PENDING holds,
    payment, expiry, and cancellation via the queue/saga layers.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        CANCELLED = "CANCELLED", "Cancelled"
        EXPIRED = "EXPIRED", "Expired"

    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bookings"
    )
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="bookings")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self) -> str:
        return f"Booking {self.reference} ({self.status})"


class IdempotencyKey(TimeStamped):
    """
    Records that a given idempotency key has been used, and which booking it
    produced. A client that retries the same booking request (network blip,
    double-click) sends the same key and gets the original booking back instead
    of a second booking or a confusing 409.

    The row is written inside the booking transaction, so a failed attempt rolls
    back and leaves no trace — a legitimate retry can try again. Once a booking
    commits, the key is durably tied to it and all further retries replay it.
    """

    key = models.CharField(max_length=255, unique=True)
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="idempotency_keys",
    )

    def __str__(self) -> str:
        return self.key
