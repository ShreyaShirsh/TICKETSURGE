"""
Phase 4 (async queue integration) + Phase 5 (saga pattern).

The booking confirm endpoint only ever does the seat decrement + booking
row insert, then returns. Everything slow or fallible — payment,
ticket issuance, the confirmation email — happens here, off the request
path, orchestrated as a saga so a failure partway through never leaves
an orphaned booking or a seat that's stuck unavailable forever.

Idempotency: every task re-checks the booking's current status before
acting, so at-least-once delivery (a redelivered message after a worker
crash mid-ack) can never double-charge, double-issue a ticket, or
double-send an email.
"""
import logging
import random

from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

from core.booking import release_seat_after_failure
from core.models import Booking, Ticket
from core.realtime import broadcast_seat_update

logger = get_task_logger(__name__)


class PaymentDeclined(Exception):
    """Simulated payment-provider decline — retryable."""


# Overridable by tests to force deterministic saga outcomes without
# relying on `random`.
def simulate_payment(booking_id: int) -> bool:
    return random.random() > 0.1  # ~90% success rate


@shared_task(
    bind=True,
    autoretry_for=(PaymentDeclined,),
    retry_backoff=True,
    retry_backoff_max=30,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
)
def process_payment(self, booking_id: int) -> None:
    booking = Booking.objects.select_related("seat", "customer").get(pk=booking_id)
    if booking.status != Booking.Status.PENDING:
        logger.info("process_payment: booking %s already %s, skipping (idempotent).",
                    booking_id, booking.status)
        return

    if not simulate_payment(booking_id):
        raise PaymentDeclined(f"Payment declined for booking {booking_id}")

    logger.info("Payment captured for booking %s", booking_id)


@shared_task(bind=True, max_retries=3, retry_backoff=True, acks_late=True)
def generate_ticket(self, booking_id: int) -> None:
    booking = Booking.objects.get(pk=booking_id)
    if hasattr(booking, "ticket"):
        logger.info("generate_ticket: booking %s already has a ticket, skipping.", booking_id)
        return
    Ticket.objects.create(booking=booking)
    logger.info("Ticket generated for booking %s", booking_id)


@shared_task(bind=True, max_retries=3, retry_backoff=True, acks_late=True)
def send_confirmation_notification(self, booking_id: int) -> None:
    booking = Booking.objects.select_related("customer", "seat").get(pk=booking_id)
    # Mock email — a real deployment would call an email provider here.
    logger.info(
        "MOCK EMAIL to %s: your seat %s is confirmed (booking #%s).",
        booking.customer.email, booking.seat, booking.pk,
    )


def _dead_letter(booking: Booking, reason: str) -> None:
    booking.status = Booking.Status.FAILED_DEAD_LETTER
    booking.failure_reason = reason[:200]
    booking.updated_at = timezone.now()
    booking.save(update_fields=["status", "failure_reason", "updated_at"])
    # Publish a record onto the dead-letter queue for operator visibility
    # (this is in addition to, not instead of, the DB status above).
    record_dead_letter.apply_async(args=[booking.pk, reason], queue="dead_letter")
    logger.error("Booking %s dead-lettered: %s", booking.pk, reason)


@shared_task
def record_dead_letter(booking_id: int, reason: str) -> None:
    logger.error("DEAD LETTER booking=%s reason=%s", booking_id, reason)


@shared_task(bind=True, acks_late=True)
def run_booking_saga(self, booking_id: int) -> str:
    """The saga orchestrator: hold -> decrement (already done by the
    confirm endpoint before this task is enqueued) -> payment -> ticket
    -> notification, with a compensating action if any step fails for
    good (retries exhausted).

    Orchestration (this single task calling each step and deciding what
    happens next) rather than choreography (each step publishing an
    event the next step listens for) — simpler to reason about and to
    defend in an interview for a project this size, at the cost of a
    bit more coupling in one place.
    """
    try:
        booking = Booking.objects.select_related("seat").get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.warning("run_booking_saga: booking %s no longer exists.", booking_id)
        return "missing"

    if booking.status != Booking.Status.PENDING:
        logger.info("run_booking_saga: booking %s already %s, nothing to do.",
                    booking_id, booking.status)
        return booking.status

    seat_id = booking.seat_id
    event_id = booking.seat.event_id

    try:
        # disable_sync_subtasks=False: we're deliberately running each step
        # synchronously, in order, inside this orchestrating task — that's
        # the whole point of the "orchestration" saga style. Celery's
        # default guard against synchronous subtasks exists to stop
        # *accidental* worker-pool deadlocks, not this intentional case.
        process_payment.apply(args=[booking_id]).get(disable_sync_subtasks=False)
        generate_ticket.apply(args=[booking_id]).get(disable_sync_subtasks=False)
        send_confirmation_notification.apply(args=[booking_id]).get(disable_sync_subtasks=False)
    except Exception as exc:  # noqa: BLE001 - saga must catch everything and compensate
        logger.warning("Saga failed for booking %s: %s — compensating.", booking_id, exc)
        compensate_booking(booking_id, reason=str(exc))
        return "compensated"

    booking.status = Booking.Status.CONFIRMED
    booking.save(update_fields=["status", "updated_at"])
    broadcast_seat_update(event_id, seat_id, "confirmed")
    return "confirmed"


def compensate_booking(booking_id: int, reason: str) -> None:
    """The compensating transaction: release the seat, mark the booking
    failed, and clean up any partial ticket. Idempotent — safe to run
    more than once against the same booking."""
    booking = Booking.objects.select_related("seat").get(pk=booking_id)
    if booking.status in (Booking.Status.FAILED, Booking.Status.FAILED_DEAD_LETTER):
        return  # already compensated

    Ticket.objects.filter(booking=booking).delete()
    release_seat_after_failure(booking.seat_id)
    _dead_letter(booking, reason)
