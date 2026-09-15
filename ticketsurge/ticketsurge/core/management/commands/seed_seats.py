from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Event, Seat

SECTIONS = ["A", "B", "C", "D", "E"]  # 5 sections x 5 rows x 20 seats = 500 seats
ROWS_PER_SECTION = 5
SEATS_PER_ROW = 20


class Command(BaseCommand):
    help = "Seed a demo event with 500 seats (the flash-sale load-test target)."

    def add_arguments(self, parser):
        parser.add_argument("--event-name", default="TicketSurge Launch Show")
        parser.add_argument("--reset", action="store_true", help="Delete existing demo data first.")
        parser.add_argument(
            "--num-seats", type=int, default=None,
            help="Override the seat count (default 500 = 5 sections x 5 rows x 20). "
                 "Use a small number (e.g. 25) to force heavy contention in a load test.",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            Event.objects.filter(name=options["event_name"]).delete()

        event, created = Event.objects.get_or_create(
            name=options["event_name"],
            defaults={
                "venue": "Central Arena",
                "starts_at": timezone.now() + timedelta(days=30),
            },
        )
        if not created and event.seats.exists():
            self.stdout.write(self.style.WARNING(f'Event "{event.name}" already has seats — skipping seed.'))
            return

        num_seats = options["num_seats"]
        seats = []
        if num_seats:
            for i in range(1, num_seats + 1):
                seats.append(
                    Seat(event=event, section="GA", row="1", number=i, price_cents=7500, available=1)
                )
        else:
            for section in SECTIONS:
                for row in range(1, ROWS_PER_SECTION + 1):
                    for number in range(1, SEATS_PER_ROW + 1):
                        price = 15000 if section in ("A", "B") else 7500
                        seats.append(
                            Seat(
                                event=event,
                                section=section,
                                row=str(row),
                                number=number,
                                price_cents=price,
                                available=1,
                            )
                        )
        Seat.objects.bulk_create(seats)
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(seats)} seats for event #{event.id} ({event.name})."))
