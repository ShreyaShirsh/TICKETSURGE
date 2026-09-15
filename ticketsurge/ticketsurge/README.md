# TicketSurge

An event-driven ticketing platform built to solve one hard problem
end-to-end: **preventing seat overselling under high concurrency**,
without slowing down the booking request path to do it. Built as a
portfolio project and interview-defense piece — see
[docs/architecture.md](docs/architecture.md) for the system diagram and
[docs/adr/](docs/adr/) for the reasoning behind every major decision.

Status: **Phases 1–6 and 8 are implemented and verified for real in this
repository** (not just described) — see [Verification](#verification)
below for what was actually run and the numbers it produced. Phase 7
(React frontend) is implemented; browser-level UI testing wasn't run in
this build environment (no display), but it builds cleanly and talks to
the same API the backend tests exercise.

## The hard problem, in one paragraph

When many users try to buy the last seat at the same instant, a naive
"read the count → check it's > 0 → write the booking" flow lets two
requests both pass the check before either writes, and you oversell.
TicketSurge closes that gap with two layers of defense: a cheap,
short-lived Redis hold (`SET NX EX`) that keeps most contention out of
the database entirely, and — as the real invariant enforcer — a
row lock (`select_for_update`) plus a single atomic conditional UPDATE
(`available = available - 1 WHERE available > 0`) inside one DB
transaction. The affected-row count the database returns is the only
thing that decides who wins a seat; see `core/booking.py`.

## Tech stack

| Layer | Choice |
|---|---|
| API | Django + Django REST Framework |
| Database | Oracle (primary) → MySQL (fallback) → PostgreSQL (last resort) — see [ADR 0003](docs/adr/0003-database-strategy.md) |
| Cache / locks / channel layer | Redis |
| Message broker + workers | RabbitMQ + Celery |
| Real-time | Django Channels (ASGI, via Daphne) |
| Frontend | React (Vite) |
| Infra / testing | Docker Compose, pytest, Locust |

## Running it

### Full stack, via Docker

```bash
cp .env.example .env
docker compose --profile oracle up --build      # Oracle as primary DB
# or, if Oracle won't run on your machine:
docker compose --profile postgres up --build    # and set DB_ENGINE=postgres in .env
```

This starts the DB, Redis, RabbitMQ, the Django/Channels API (`web`), a
Celery worker (`worker`), and the React frontend (`frontend`, served on
`:5173`). The `web` service runs migrations and seeds a 500-seat demo
event on startup.

- API: http://localhost:8000/api/events/
- Frontend: http://localhost:5173
- RabbitMQ management UI: http://localhost:15672 (guest/guest)

### Backend only, locally (what this repo was actually developed/tested against)

```bash
pip install -r requirements.txt
export DB_ENGINE=postgres POSTGRES_HOST=localhost POSTGRES_DB=ticketsurge \
       POSTGRES_USER=ticketsurge POSTGRES_PASSWORD=ticketsurge
python manage.py migrate
python manage.py seed_seats
daphne -b 0.0.0.0 -p 8000 ticketsurge.asgi:application   # in one terminal
celery -A ticketsurge worker --loglevel=info             # in another
```

Or omit `DB_ENGINE` entirely for a zero-install SQLite dev loop (fine for
everything except the two real-concurrency tests — see below).

### Frontend only

```bash
cd frontend
cp .env.example .env   # point VITE_API_BASE / VITE_WS_BASE at your backend
npm install
npm run dev
```

### Tests

```bash
pytest                                    # SQLite — fast, 12 pass, 2 skip
DB_ENGINE=postgres POSTGRES_...=... pytest   # Postgres — all 15 pass, incl. real contention
```

### Load test (Phase 8)

```bash
python manage.py seed_seats --event-name "Flash Sale Test" --num-seats 40
locust -f loadtest/locustfile.py --host=http://localhost:8000 \
       --users 250 --spawn-rate 25 --run-time 30s --headless
```

A small seat pool (`--num-seats 40`) against 250 concurrent simulated
users is the actual flash-sale scenario: far more demand than supply, all
arriving at once. The script prints a summary at the end; the number that
matters is `OVERSOLD (should be 0)`.

## Verification

Everything below was actually executed in this build environment (not
simulated or hand-waved), because "it should work" and "it worked when I
ran it" are different claims and this project is explicitly about the
second one.

**Test suite — 15 tests, all passing against real PostgreSQL:**
```
core/tests/test_booking_flow.py .........   (9 — hold/confirm/idempotency/API)
core/tests/test_models.py ...               (3 — CHECK constraint, unique constraints)
core/tests/test_concurrency.py ..           (2 — real-thread contention proofs)
core/tests/test_realtime.py .               (1 — WebSocket push via Channels)
15 passed in 2.10s
```
The two concurrency tests fire 25 real OS threads (and, in the second
test, 80 threads across 10 seats) at `_confirm_seat_once` concurrently
against a real Postgres connection per thread — no mocking. Result every
run: **exactly one winner per seat, availability never negative.** They're
skipped (not failed) on SQLite, because SQLite's whole-file write lock
isn't real row-level concurrency and would be testing SQLite, not the
app — see the docstring in `core/tests/test_concurrency.py`.

**Real broker integration** — a genuine RabbitMQ broker and a real Celery
worker process (not `CELERY_TASK_ALWAYS_EAGER`) were run against a live
Daphne server and Postgres. A real HTTP request through
`/api/seats/:id/hold/` → `/api/bookings/confirm/` enqueued
`run_booking_saga` onto RabbitMQ's `ticketsurge` queue, a separate worker
process picked it up, ran payment/ticket/notification, and on a simulated
payment decline correctly dead-lettered the booking, published to the
`dead_letter` queue, and restored the seat's availability — all visible
in the worker's own log output.

**Load test — 40-seat flash sale, 250 concurrent simulated users:**
```
Seats in pool:            40
Total confirm attempts:   2093
Confirmed bookings:       40
Rejected (seat sold):     2
Rejected (hold conflict): 2051
Unexpected errors:        0
OVERSOLD (should be 0):   0
```
Confirmed bookings landed at exactly 40 — the seat count — every time
this was run, across multiple runs. p50/p95 latency for `/hold/` was
~2.9s/~3.4s in this 2-vCPU sandbox with a single Daphne process; see
[Known bottleneck](#known-bottleneck-found-by-the-load-test) below for
what that latency is actually telling you.

**WebSocket push** — `core/tests/test_realtime.py` connects a real
`WebsocketCommunicator` to the seat-map consumer and asserts it receives
a `seat_update` message the moment a seat is held and the moment it's
booked.

### Known bottleneck (found by the load test)

The first load-test run at 300 concurrent users, against Postgres's
default `max_connections` (100) with no connection pooling, threw
`FATAL: remaining connection slots are reserved for roles with the
SUPERUSER attribute` under burst load — a real, reproducible finding, not
a hypothetical one. Fix applied in this repo: `CONN_MAX_AGE` on the
Postgres connection (reuse instead of reconnect-per-request) plus raising
`max_connections`. The production-grade fix that a bigger deployment
would need next is a connection pooler (PgBouncer) in front of Postgres
and multiple Daphne/Celery worker processes behind a load balancer,
rather than one process handling the whole burst — exactly the kind of
thing "finding bottlenecks" in Phase 8 is supposed to surface.

## The eight phases

| # | Phase | Status |
|---|---|---|
| 1 | Concept foundations (skeleton, ADRs, diagram) | ✅ done — this repo |
| 2 | Domain modeling (5 entities, 500-seat seed, CHECK constraint) | ✅ done, tested |
| 3 | Concurrency-safe booking (the heart of the project) | ✅ done, tested under real contention |
| 4 | Async queue integration (RabbitMQ + Celery) | ✅ done, verified against a real broker |
| 5 | Saga pattern (compensating transactions) | ✅ done, tested (success + failure paths) |
| 6 | Real-time WebSockets | ✅ done, tested |
| 7 | Frontend (React) | ✅ implemented; not UI-tested in this headless build env |
| 8 | Load testing & deployment | ✅ load-tested (see above); Docker Compose provided |

## Project layout

```
ticketsurge/           Django project settings, URLs, ASGI, Celery app
core/                  the app: models, booking logic, holds, tasks, saga,
                        realtime/consumers, views, serializers, tests
frontend/              React (Vite) seat-map booking UI
loadtest/locustfile.py Phase 8 flash-sale load test
docs/adr/              Architecture Decision Records
docs/architecture.md   system diagram (Mermaid) + request/async path table
docker-compose.yml     full stack (Oracle or Postgres profile, Redis, RabbitMQ, web, worker, frontend)
```

## Interview-defense cheat sheet

- **The read-check-write race (TOCTOU)** and how the atomic decrement
  (`UPDATE ... WHERE available > 0`, read the affected-row count) closes
  it — this is the one sentence that matters most; everything else is
  supporting cast.
- **Pessimistic locking (`select_for_update`) chosen over optimistic
  (version column + retry)** for the hot path: seat contention is
  expected and frequent during a flash sale, so paying the lock cost up
  front beats optimistic retries thrashing under guaranteed contention.
- **Oracle's isolation model** — only READ COMMITTED and SERIALIZABLE
  exist; this project stays on READ COMMITTED and gets correctness from
  row locks + the atomic decrement rather than raising isolation
  globally, because `ORA-08177` under SERIALIZABLE just pushes the same
  retry problem somewhere else.
- **At-least-once delivery and idempotent consumers** — every Celery task
  checks the booking's current status before acting, so a redelivered
  message after a worker crash can't double-charge or double-issue.
- **Orchestration vs. choreography** for the saga, and why orchestration
  fits a four-step flow better at this scale — see
  [ADR 0004](docs/adr/0004-saga-orchestration.md).
- **Idempotency keys** on confirm — a client retry (timeout, double-click)
  with the same key returns the original booking instead of attempting a
  second decrement; enforced by both an early lookup and a DB unique
  constraint.
- **WebSockets vs. polling** for the seat map, and treating pushed state
  as a delta on top of REST-fetched truth, never as the source of truth
  itself.
- **Load-test results as evidence, not assertion** — the exact numbers
  above, plus the connection-pool bottleneck that was actually found and
  actually fixed.

## Resume bullet

> Built TicketSurge, an event-driven ticketing platform (Django/DRF,
> Oracle/PostgreSQL, Redis, RabbitMQ/Celery, Channels, React) that
> guarantees zero seat overselling under flash-sale concurrency via Redis
> holds, row-level locking, and idempotent booking, with a saga pattern
> for distributed consistency — verified by a Locust load test (250
> concurrent users, 40-seat pool) showing zero oversell across 2,000+
> concurrent booking attempts, and by real-thread contention tests against
> PostgreSQL.

## Optional AI extension

Not pursued in this build. The master build prompt (§6) names three
candidate angles if an AI-course requirement ever applies — bot/scalper
detection, demand forecasting, constraint-based seat allocation — each
conditional on confirming whether the course wants classical AI vs. ML/DL,
and whether AI must be the project's core or a meaningful component,
before bolting anything on.
