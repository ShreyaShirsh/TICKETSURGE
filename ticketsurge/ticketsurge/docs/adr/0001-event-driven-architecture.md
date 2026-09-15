# ADR 0001: Event-driven architecture over pure request/response

## Status
Accepted

## Context
A ticket-booking flow has one fast, correctness-critical step (claiming a
seat) and several slow, fallible steps that follow it (charging a card,
issuing a ticket, sending a confirmation email). A naive synchronous
implementation does all of this inside a single HTTP request: the client
waits on the payment provider's round trip before it gets a response, and
if the email provider is slow or down, booking itself becomes slow or down.

## Decision
Split the booking flow into a fast synchronous path and a slow asynchronous
path:

- **Synchronous** (must be fast, must be correct): hold the seat, confirm
  the seat (row lock + atomic decrement + booking row), return.
- **Asynchronous** (can be slow, must be eventually consistent): payment,
  ticket generation, confirmation notification — run as Celery tasks off
  the request path, published to RabbitMQ.

The confirm endpoint returns as soon as the booking exists as `PENDING`;
the client learns the outcome (`CONFIRMED` / `FAILED_DEAD_LETTER`) via
polling or the WebSocket seat-map push.

## Consequences
- Booking latency is decoupled from payment-provider latency — a slow
  payment gateway makes bookings take longer to *finalize*, not longer to
  *acknowledge*.
- A `PENDING` state now exists and must be surfaced honestly to the client
  (the frontend shows "pending" before "confirmed", not a fake instant
  success).
- Failure handling moves from "the request just failed" to "the request
  succeeded, but the saga behind it may still compensate" — which is why
  ADR 0002 and the Phase 5 saga exist at all.
- Consistency vs. availability tension shows up explicitly at the
  seat-decrement boundary: we choose to decrement immediately (favoring a
  fast "no availability" answer) rather than waiting for payment to
  succeed first (which would hold the seat far longer and reduce
  throughput under contention).
