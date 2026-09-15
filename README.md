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
the environment this was built in (no display), but it builds cleanly and
talks to the same API the backend tests exercise.

## Table of contents

- [The hard problem, in one paragraph](#the-hard-problem-in-one-paragraph)
- [Tech stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Running it](#running-it)
- [Configuration reference](#configuration-reference)
- [API reference](#api-reference)
- [Tests](#tests)
- [Load test (Phase 8)](#load-test-phase-8)
- [Verification](#verification)
- [The eight phases](#the-eight-phases)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Interview-defense cheat sheet](#interview-defense-cheat-sheet)
- [Resume bullet](#resume-bullet)
- [Optional AI extension](#optional-ai-extension)
- [License](#license)

## The hard problem, in one paragraph

When many users try to buy the last seat at the same instant, a naive
"read the count → check it's > 0 → write the booking" flow lets two
requests both pass the check before either writes, and you oversell.
TicketSurge closes that gap with two layers of defense: a cheap,
short-lived Redis hold (`SET NX EX`) that keeps most contention out of
the database entirely, and — as the real invariant enforcer — a row lock
(`select_for_update`) plus a single atomic conditional UPDATE
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

## Prerequisites

| Tool | Needed for |
|---|---|
| Docker + Docker Compose | the full-stack path (recommended for first run) |
| Python 3.11+ | running the backend directly, or the test suite |
| Node 18+ / npm | running the frontend directly |
| PostgreSQL 16 (or Oracle 23c Free, or MySQL 8) | a non-Docker backend run |
| Redis 7 | seat holds + the Channels layer (required in every mode) |
| RabbitMQ 3.13 | the Celery broker (required unless `CELERY_TASK_ALWAYS_EAGER=1`) |

If you just want to see it run, the Docker path needs nothing else
installed. If you want to hack on the backend directly, you need Python,
Redis, and (unless you're fine with `CELERY_TASK_ALWAYS_EAGER=1` and no
real async behavior) RabbitMQ and a real database — SQLite is supported
for the fastest possible dev loop, with the caveat noted below.

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

To stop everything: `docker compose down` (add `-v` to also drop the
Oracle/Postgres data volume and start clean next time).

### Backend only, locally (what this repo was actually developed/tested against)

```bash
pip install -r requirements.txt

# Redis and RabbitMQ still need to be running somewhere reachable —
# either via `docker compose up redis rabbitmq`, or installed locally.

export DB_ENGINE=postgres POSTGRES_HOST=localhost POSTGRES_DB=ticketsurge \
       POSTGRES_USER=ticketsurge POSTGRES_PASSWORD=ticketsurge
python manage.py migrate
python manage.py seed_seats
daphne -b 0.0.0.0 -p 8000 ticketsurge.asgi:application   # in one terminal
celery -A ticketsurge worker --loglevel=info             # in another
```

Or omit `DB_ENGINE` entirely for a zero-install SQLite dev loop (fine for
everything except the two real-concurrency tests — see
[Troubleshooting](#troubleshooting)).

### Frontend only

```bash
cd frontend
cp .env.example .env   # point VITE_API_BASE / VITE_WS_BASE at your backend
npm install
npm run dev
```

## Configuration reference

Every setting is read from the environment (`.env` for Docker Compose, or
your shell for a local run) — see `.env.example` for the full file with
defaults filled in.

| Variable | Default | Meaning |
|---|---|---|
| `DJANGO_SECRET_KEY` | dev key | Django's `SECRET_KEY`; set a real one in production |
| `DJANGO_DEBUG` | `1` | Django debug mode |
| `DJANGO_ALLOWED_HOSTS` | `*` | comma-separated allowed hosts |
| `DB_ENGINE` | `sqlite` | `oracle` \| `mysql` \| `postgres` \| `sqlite` — see [ADR 0003](docs/adr/0003-database-strategy.md) |
| `ORACLE_DSN` / `ORACLE_APP_USER` / `ORACLE_APP_PASSWORD` / `ORACLE_SYS_PASSWORD` | — | used when `DB_ENGINE=oracle` |
| `MYSQL_DB` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_HOST` / `MYSQL_PORT` | — | used when `DB_ENGINE=mysql` |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_HOST` / `POSTGRES_PORT` | — | used when `DB_ENGINE=postgres` |
| `POSTGRES_CONN_MAX_AGE` | `60` | seconds a Postgres connection is reused before reconnecting — see [Known bottleneck](#known-bottleneck-found-by-the-load-test) |
| `REDIS_URL` | `redis://localhost:6379/0` | general Redis connection |
| `REDIS_HOLDS_URL` | `redis://localhost:6379/1` | seat-hold keys (`SET NX EX`) |
| `REDIS_CHANNELS_URL` | same as `REDIS_URL` | Django Channels layer backend |
| `SEAT_HOLD_TTL_SECONDS` | `420` (7 min) | how long a seat hold lasts before it auto-expires |
| `CELERY_BROKER_URL` | `amqp://guest:guest@localhost:5672//` | RabbitMQ connection |
| `CELERY_TASK_ALWAYS_EAGER` | `0` | set to `1` to run Celery tasks synchronously in-process (no broker/worker needed — used by the test suite) |

## API reference

Base URL: `http://localhost:8000`. All bodies are JSON.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health/` | liveness check |
| `GET` | `/api/events/` | list events, with live seat-availability counts |
| `GET` | `/api/events/:id/seats/` | all seats for one event |
| `GET` | `/api/seats/?event=:id` | same, via the seats endpoint |
| `POST` | `/api/seats/:id/hold/` | place a 7-minute Redis hold on a seat |
| `POST` | `/api/seats/:id/release-hold/` | voluntarily release a hold early |
| `POST` | `/api/bookings/confirm/` | turn a hold into a durable booking |
| `GET` | `/api/bookings/:id/` | check a booking's status (poll this while `PENDING`) |
| `WS` | `/ws/events/:id/seats/` | live seat-status deltas (`held` / `released` / `booked` / `confirmed`) |

### Walkthrough: hold → confirm → poll

```bash
# 1. Hold a seat (fails 409 if it's already held or booked)
curl -X POST http://localhost:8000/api/seats/1/hold/
# => {"seat_id": 1, "hold_token": "kJ3x...", "expires_at": 1735000000.0}

# 2. Confirm it — fast, returns PENDING immediately; the saga runs async
curl -X POST http://localhost:8000/api/bookings/confirm/ \
  -H "Content-Type: application/json" \
  -d '{
        "seat_id": 1,
        "hold_token": "kJ3x...",
        "idempotency_key": "any-client-generated-uuid",
        "customer_email": "fan@example.com"
      }'
# => {"id": 42, "status": "PENDING", "seat": {...}, "ticket": null, ...}

# 3. Poll until the saga finishes (or subscribe to the WebSocket instead)
curl http://localhost:8000/api/bookings/42/
# => {"id": 42, "status": "CONFIRMED", "ticket": {"ticket_code": "..."}, ...}
#    or "status": "FAILED_DEAD_LETTER" with a "failure_reason" if the
#    simulated payment declined and retries were exhausted.
```

Retrying step 2 with the **same** `idempotency_key` (a timed-out response,
a double-click) returns the original booking instead of attempting a
second decrement — see `core/booking.py::confirm_seat`.

### WebSocket message shape

```json
{"type": "seat_update", "seat_id": 1, "action": "held"}
```
`action` is one of `held`, `released`, `booked`, `confirmed`. Treat these
as deltas on top of state you already fetched via REST, not as the source
of truth on their own.

## Tests

```bash
pytest                                       # SQLite — fast, 12 pass, 2 skip
DB_ENGINE=postgres POSTGRES_...=... pytest   # Postgres — all 15 pass, incl. real contention
```

| File | Covers |
|---|---|
| `core/tests/test_models.py` | CHECK constraint, unique constraints |
| `core/tests/test_booking_flow.py` | hold/confirm, idempotency, saga success + failure, full API flow |
| `core/tests/test_concurrency.py` | real OS-thread contention proofs (Postgres/MySQL/Oracle only) |
| `core/tests/test_realtime.py` | WebSocket push via Channels |

## Load test (Phase 8)

```bash
python manage.py seed_seats --event-name "Flash Sale Test" --num-seats 40
locust -f loadtest/locustfile.py --host=http://localhost:8000 \
       --users 250 --spawn-rate 25 --run-time 30s --headless
```

A small seat pool (`--num-seats 40`) against 250 concurrent simulated
users is the actual flash-sale scenario: far more demand than supply, all
arriving at once. The script prints a summary at the end; the number that
matters is `OVERSOLD (should be 0)`. Drop `--headless` (and add
`--web-port 8089`) to drive it interactively from Locust's web UI instead.

## Verification

Everything below was actually executed in the environment this repo was
built in (not simulated or hand-waved), because "it should work" and "it
worked when I ran it" are different claims and this project is explicitly
about the second one.

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
~2.9s/~3.4s in a resource-constrained 2-vCPU sandbox with a single Daphne
process; see [Known bottleneck](#known-bottleneck-found-by-the-load-test)
below for what that latency is actually telling you, and expect
meaningfully better numbers on real hardware or with multiple app workers.

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
| 7 | Frontend (React) | ✅ implemented; not UI-tested in a headless build env |
| 8 | Load testing & deployment | ✅ load-tested (see above); Docker Compose provided; not deployed publicly |

## Project layout

```
ticketsurge/            Django project settings, URLs, ASGI, Celery app
core/
  models.py              Customer, Event, Seat, Booking, Ticket
  holds.py                Redis SET NX EX seat-hold primitive
  booking.py              confirm_seat: row lock + atomic decrement + idempotency
  tasks.py                Celery tasks: payment, ticket, notification, saga, dead-letter
  realtime.py              Channels broadcast helper
  consumers.py             WebSocket seat-map consumer
  views.py / serializers.py / urls.py    the REST API
  management/commands/seed_seats.py      demo/load-test data seeding
  tests/                   pytest suite (models, booking flow, concurrency, realtime)
frontend/                React (Vite) seat-map booking UI
loadtest/locustfile.py   Phase 8 flash-sale load test
docs/adr/                Architecture Decision Records
docs/architecture.md     system diagram (Mermaid) + request/async path table
docker-compose.yml       full stack (Oracle or Postgres profile, Redis, RabbitMQ, web, worker, frontend)
.env.example             every environment variable, with working defaults
```

## Troubleshooting

- **`OperationalError: database is locked` running tests on SQLite** —
  expected and by design; SQLite's whole-file write lock can't show real
  row-level concurrency, so the two tests in `test_concurrency.py` are
  skipped there (not failed). Run with `DB_ENGINE=postgres` to exercise
  them for real.
- **`FATAL: remaining connection slots are reserved...` under load** —
  see [Known bottleneck](#known-bottleneck-found-by-the-load-test) above;
  raise Postgres's `max_connections` and/or lower concurrent load, or add
  a pooler for a real fix.
- **Oracle container won't start / no image for your CPU architecture** —
  per [ADR 0003](docs/adr/0003-database-strategy.md), fall back to
  `docker compose --profile postgres up` and set `DB_ENGINE=postgres`; no
  application code changes needed.
- **Celery tasks never run** — confirm RabbitMQ is up
  (`docker compose ps rabbitmq`, or check `rabbitmqctl status` locally)
  and that a `worker` process is actually running; without either, a
  booking will sit in `PENDING` forever. For quick local debugging without
  a broker at all, set `CELERY_TASK_ALWAYS_EAGER=1`.
- **WebSocket connects but never receives updates** — check
  `REDIS_CHANNELS_URL` points at the same Redis the API process is using,
  and that you're running the app via Daphne (`ticketsurge.asgi:application`),
  not `manage.py runserver` (which doesn't serve WebSockets).

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

## License

No license file is included yet — add one (MIT is a reasonable default
for a portfolio project) before making the repository public if you want
to be explicit about reuse terms.
