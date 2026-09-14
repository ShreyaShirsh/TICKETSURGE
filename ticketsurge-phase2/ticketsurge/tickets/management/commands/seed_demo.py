"""
Seed the database with one venue, one event, and ~500 seats.

Idempotent: re-running resets the demo event's seats to AVAILABLE and removes
old demo bookings so the happy path can be re-tested cleanly.

Usage:
    python manage.py seed_demo
    python manage.py seed_demo --seats 500
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from tickets.models import Booking, Event, Seat, Venue

SECTIONS = ["A", "B", "C", "D", "E"]  # seats spread across a few sections
ROWS_PER_SECTION = 10
SEATS_PER_ROW = 10  # 5 * 10 * 10 = 500


class Command(BaseCommand):
    help = "Seed one venue, one event, and ~500 seats for local testing."

    def add_arguments(self, parser):
        parser.add_argument("--seats", type=int, default=500)

    @transaction.atomic
    def handle(self, *args, **options):
        target_seats = options["seats"]

        venue, _ = Venue.objects.get_or_create(
            name="TicketSurge Arena",
            defaults={"address": "1 Demo Street", "city": "Vellore"},
        )
        event, _ = Event.objects.get_or_create(
            name="Opening Night",
            venue=venue,
            defaults={
                "description": "Demo event seeded for Phase 1.",
                "starts_at": timezone.now() + timezone.timedelta(days=30),
            },
        )

        # Clean slate for repeatable happy-path testing.
        Booking.objects.filter(event=event).delete()
        event.seats.all().delete()

        seats = []
        count = 0
        for section in SECTIONS:
            for row in range(1, ROWS_PER_SECTION + 1):
                for number in range(1, SEATS_PER_ROW + 1):
                    if count >= target_seats:
                        break
                    seats.append(
                        Seat(
                            event=event,
                            section=section,
                            row=str(row),
                            number=number,
                            status=Seat.Status.AVAILABLE,
                        )
                    )
                    count += 1
        Seat.objects.bulk_create(seats)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded event '{event.name}' (id={event.id}) "
                f"with {count} available seats at '{venue.name}'."
            )
        )
