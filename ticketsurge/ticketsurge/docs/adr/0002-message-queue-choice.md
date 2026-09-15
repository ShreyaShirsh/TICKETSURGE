# ADR 0002: RabbitMQ + Celery for the async queue

## Status
Accepted

## Context
Given ADR 0001's split, something has to carry "run these side-effects"
messages from the web process to background workers, survive a worker
crash mid-task, and give us retry/backoff and a place to put messages that
never succeed (poison messages).

## Decision
RabbitMQ as the broker, Celery as the task framework, both because this
project's explicit goal is to learn message-queue architecture from
scratch, and because the combination gives first-class support for
everything the saga needs without hand-rolling it:

- **acks_late** — a task is only acknowledged (removed from the queue)
  after it finishes, so a worker crash mid-task leads to redelivery, not
  silent message loss.
- **At-least-once delivery** — the corollary of the above: a task can run
  more than once. Every task in `core/tasks.py` is written to be
  idempotent (checks the booking's current status before acting) so a
  redelivery is a no-op, not a double-charge or a duplicate ticket.
- **retry_backoff / retry_jitter** — `process_payment` retries a simulated
  transient decline with exponential backoff, rather than hammering the
  (simulated) payment provider immediately.
- **Dead-lettering** — the `ticketsurge` queue is declared with
  `x-dead-letter-exchange` pointing at a `dead_letter` queue/exchange, so
  a message that exhausts its retries doesn't just vanish; it lands
  somewhere an operator (or, here, `core.tasks.record_dead_letter`) can
  see it, and the booking is marked `FAILED_DEAD_LETTER` rather than
  stuck `PENDING` forever.

## Alternatives considered
- **Redis as a broker** (via Celery's Redis transport): simpler to run
  (we're already running Redis for holds/channels), but weaker delivery
  guarantees and no native dead-lettering — we'd have to build that by
  hand. Passed over specifically because the message-queue mechanics
  *are* the thing this project is meant to teach.
- **Kafka**: a legitimate choice for very high-throughput event streaming,
  but its operational complexity and log-based (not queue-based) model
  are a mismatch for "process this booking's side-effects exactly enough
  times" — Celery/RabbitMQ's task-queue model fits the problem shape
  better here.

## Consequences
- Two more services to run (RabbitMQ + Celery workers), on top of Redis —
  more moving parts than a synchronous-only design, which is the
  deliberate trade-off ADR 0001 already accepts.
- Every task must be written idempotently from day one; this isn't
  optional cleanup, it's a correctness requirement of at-least-once
  delivery.
