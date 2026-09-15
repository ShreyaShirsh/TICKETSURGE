"""
Phase 8 — flash-sale load test.

Simulates thousands of concurrent users hammering hold + confirm on a
small, fixed pool of seats (the actual flash-sale scenario: far more
demand than supply, all arriving at once). The pass/fail bar for this
project isn't throughput or latency — it's simpler and stricter: the
number of CONFIRMED bookings for the target event must never exceed
the number of seats that were available when the test started.

Usage:
    # 1. Seed a small, dedicated flash-sale event (tiny pool on purpose
    #    -- the whole point is to force contention):
    DJANGO_SETTINGS_MODULE=ticketsurge.settings python manage.py seed_seats \
        --event-name "Flash Sale Test" --reset

    # 2. Point Locust at a running stack (docker compose up, or a local
    #    daphne/celery/redis/rabbitmq/db combo) and run headless:
    locust -f loadtest/locustfile.py --host=http://localhost:8000 \
        --users 500 --spawn-rate 100 --run-time 1m --headless

    # A summary of confirmed vs. rejected vs. oversold is printed when
    # the run stops (see `on_test_stop` below).
"""
import random

from locust import HttpUser, between, events, task

EVENT_NAME = "Flash Sale Test"

# Populated once per worker process the first time a user needs it.
_seat_ids: list[int] = []
_stats = {"confirmed": 0, "rejected_sold_out": 0, "rejected_hold_conflict": 0, "errors": 0}


def _load_seat_ids(client):
    global _seat_ids
    if _seat_ids:
        return _seat_ids
    resp = client.get("/api/events/", name="/api/events/ (setup)")
    events_ = resp.json()
    event = next((e for e in events_ if e["name"] == EVENT_NAME), events_[0])
    resp = client.get(f"/api/events/{event['id']}/seats/", name="/api/events/:id/seats/ (setup)")
    _seat_ids = [s["id"] for s in resp.json()]
    return _seat_ids


class FlashSaleUser(HttpUser):
    """Each simulated user: pick a random seat in the tiny pool, try to
    hold it, and if that succeeds, immediately try to confirm it. Most
    users are expected to lose the race — that's the point."""

    wait_time = between(0.01, 0.2)

    def on_start(self):
        _load_seat_ids(self.client)

    @task
    def try_to_buy_a_seat(self):
        if not _seat_ids:
            return
        seat_id = random.choice(_seat_ids)

        with self.client.post(
            f"/api/seats/{seat_id}/hold/", name="/api/seats/:id/hold/", catch_response=True
        ) as hold_resp:
            if hold_resp.status_code == 409:
                _stats["rejected_hold_conflict"] += 1
                hold_resp.success()  # expected outcome under contention, not a failure
                return
            if hold_resp.status_code != 201:
                _stats["errors"] += 1
                hold_resp.failure(f"unexpected hold status {hold_resp.status_code}")
                return
            hold_resp.success()
            token = hold_resp.json()["hold_token"]

        payload = {
            "seat_id": seat_id,
            "hold_token": token,
            "idempotency_key": f"locust-{self.environment.runner.user_count if self.environment.runner else 0}-{seat_id}-{random.random()}",
            "customer_email": f"loadtest-{random.randint(1, 10_000_000)}@example.com",
        }
        with self.client.post(
            "/api/bookings/confirm/", json=payload, name="/api/bookings/confirm/", catch_response=True
        ) as confirm_resp:
            if confirm_resp.status_code in (200, 201):
                _stats["confirmed"] += 1
                confirm_resp.success()
            elif confirm_resp.status_code == 409:
                _stats["rejected_sold_out"] += 1
                confirm_resp.success()  # expected: someone else won this seat first
            else:
                _stats["errors"] += 1
                confirm_resp.failure(f"unexpected confirm status {confirm_resp.status_code}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    total_attempts = sum(_stats.values())
    oversold = max(0, _stats["confirmed"] - len(_seat_ids)) if _seat_ids else "unknown"
    print("\n" + "=" * 60)
    print("FLASH-SALE LOAD TEST — RESULT")
    print("=" * 60)
    print(f"Seats in pool:            {len(_seat_ids)}")
    print(f"Total confirm attempts:   {total_attempts}")
    print(f"Confirmed bookings:       {_stats['confirmed']}")
    print(f"Rejected (seat sold):     {_stats['rejected_sold_out']}")
    print(f"Rejected (hold conflict): {_stats['rejected_hold_conflict']}")
    print(f"Unexpected errors:        {_stats['errors']}")
    print(f"OVERSOLD (should be 0):   {oversold}")
    print("=" * 60)
    if isinstance(oversold, int) and oversold > 0:
        environment.process_exit_code = 1
