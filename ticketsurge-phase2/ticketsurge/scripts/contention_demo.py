"""
Standalone demo: fire N concurrent booking requests at ONE seat and print the
win/loss tally. Run: python scripts/contention_demo.py
"""
import os
import sys
import threading

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

from tickets import redis_holds  # noqa: E402
from tickets.models import Booking, Event, Seat, Venue  # noqa: E402
from tickets.services import SeatUnavailable, create_booking  # noqa: E402

User = get_user_model()
N = 50


def main():
    redis_holds.get_redis().flushdb()
    venue, _ = Venue.objects.get_or_create(name="Demo Contention Arena")
    event = Event.objects.create(
        name="Contention Demo", venue=venue, starts_at=timezone.now()
    )
    seat = Seat.objects.create(event=event, section="X", row="1", number=1)
    users = [User.objects.get_or_create(username=f"demo_racer{i}")[0] for i in range(N)]

    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(N)

    def worker(i):
        barrier.wait()
        try:
            booking, _ = create_booking(user=users[i], event=event, seat_ids=[seat.id])
            out = ("WIN", users[i].username, str(booking.reference)[:8])
        except SeatUnavailable:
            out = ("lose", users[i].username, None)
        finally:
            connection.close()
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    wins = [r for r in results if r[0] == "WIN"]
    print(f"\n{N} threads raced for 1 seat")
    print(f"  winners: {len(wins)}   losers: {len(results) - len(wins)}")
    for r in wins:
        print(f"  -> {r[1]} won, booking {r[2]}")
    seat.refresh_from_db()
    print(f"  seat final status: {seat.status}")
    print(f"  bookings created for event: {Booking.objects.filter(event=event).count()}")

    # cleanup
    Booking.objects.filter(event=event).delete()
    event.seats.all().delete()
    event.delete()
    print("  (demo rows cleaned up)")


if __name__ == "__main__":
    main()
