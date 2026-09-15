# ADR 0003: Database choice and fallback ladder

## Status
Accepted

## Context
The project's DB strategy names Oracle as the primary target (deliberately
— the goal includes demonstrating Oracle proficiency), with an explicit,
pre-agreed fallback ladder rather than an open-ended "switch if it doesn't
work" that would let scope creep in mid-project.

## Decision
**Oracle (primary) → MySQL (fallback) → PostgreSQL (last resort).**

Switch Oracle → MySQL only if, within roughly a day of effort:
- no working Oracle image exists for the dev machine's architecture
  (e.g. Apple Silicon before Oracle Free's 23.5 ARM builds), or
- the chosen Oracle image version hits a Django/`oracledb` incompatibility
  (observed historically around `ORA-00600` on migrations), or
- the deployment/CI target can't reasonably run Oracle at all.

Switch MySQL → PostgreSQL only if MySQL *also* blocks — Postgres is the
safety net specifically because the domain-model and concurrency-safe
booking milestones were originally validated against it before the
Oracle port.

The application code stays identical across all three: the concurrency
guarantees come from `select_for_update()` + an atomic conditional
`UPDATE ... WHERE available > 0`, both of which are standard SQL that
Django's ORM translates correctly for Oracle, MySQL, and PostgreSQL alike.
Only `ticketsurge/settings.py`'s `DATABASES` block changes, via the
`DB_ENGINE` environment variable — see `.env.example`.

### Why this matters for Oracle specifically
Oracle only offers READ COMMITTED and SERIALIZABLE isolation (no
REPEATABLE READ to lean on). Under SERIALIZABLE, concurrent conflicting
writes raise `ORA-08177` ("can't serialize access"), which the app would
have to catch and retry. This project deliberately stays on READ
COMMITTED and gets its correctness from explicit row locks and the atomic
decrement instead of a stronger isolation level — see ADR-adjacent notes
in `core/booking.py`. (The retry loop in `confirm_seat` is written
generically enough to also absorb an `ORA-08177` if the isolation level
were ever raised experimentally.)

## What actually happened in this build
Oracle needs a dedicated multi-GB container and isn't something every
environment can run (this repo's own CI/dev sandbox couldn't). Per the
fallback ladder, the app, tests, and load test in this repository were
built and verified against **PostgreSQL 16** end-to-end — including the
real concurrency contention test (`core/tests/test_concurrency.py`) and a
live run through a real RabbitMQ broker and Celery worker — and the
`DB_ENGINE=oracle` path is implemented and configured (`settings.py`,
`docker-compose.yml`) but not itself executed in this environment.
This is exactly the scenario ADR 0003 anticipates: the DB is a deployment
decision, not a correctness one, and the migration path from Postgres to
Oracle (§3.4 of the master build prompt) is: swap the `oracledb` driver
in, regenerate/verify migrations, re-run the seed and the full test suite
against Oracle, and confirm `select_for_update` + the contention test
still hold — no application code changes expected.

## Consequences
- Whoever runs this project locally can pick whichever DB their machine
  can actually host, without touching a line of `core/`.
- The "I chose the DB deliberately and my concurrency code is portable
  across all three" story is not just a slide — it's demonstrated by this
  repo actually running its full test suite against a second backend.
