# ADR 0004: Orchestration-style saga, not choreography

## Status
Accepted

## Context
The booking lifecycle (hold → decrement → payment → ticket → notify) spans
multiple steps that can each fail independently, and — per ADR 0001 — runs
across a DB transaction boundary and several async tasks. A two-phase
commit across Postgres/Oracle and RabbitMQ isn't available (and wouldn't
scale even if it were), so consistency has to come from a saga: each step
either completes or is undone by a compensating action.

## Decision
Implement the saga as **orchestration**: a single task, `run_booking_saga`,
owns the sequence and decides what happens next, calling `process_payment`,
`generate_ticket`, and `send_confirmation_notification` in order and
catching any failure to trigger `compensate_booking`. This is in contrast
to **choreography**, where each task would publish an event on completion
that the next task listens for, with no single place that knows the whole
flow.

Chosen because:
- The flow is linear and short (four steps, one compensating action). At
  this size, choreography's benefit — no central coordinator — is
  outweighed by its cost: understanding the flow means finding every
  task's event subscriptions and mentally reconstructing the sequence.
- One place (`run_booking_saga`) is enough to explain and test the entire
  compensation story: `core/tests/test_booking_flow.py`'s
  `test_saga_failure_compensates_and_releases_seat` exercises the whole
  saga in one call.
- It scales down well to a portfolio project's actual size, while still
  being the correct vocabulary term to use in an interview — the trade-off
  against choreography is exactly what a senior engineer would be asked to
  justify.

## Consequences
- `run_booking_saga` is a single point of coupling to all three downstream
  steps — acceptable here, would need revisiting if the saga grew to
  dozens of steps or needed independent scaling per step.
- The compensating action (`compensate_booking`) must itself be safe to
  run more than once — it is, by construction: releasing an already-
  released seat, or deleting an already-absent ticket, are both no-ops in
  Django's ORM (`update()` on zero matching rows, `filter().delete()` on
  zero matching rows).
- Dead-letter routing means the DB status (`FAILED_DEAD_LETTER`) and the
  message broker's dead-letter queue are supposed to agree; if they ever
  drift, `record_dead_letter` (which writes to both) is the place to
  investigate first.
