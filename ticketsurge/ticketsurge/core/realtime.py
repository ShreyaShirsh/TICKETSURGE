"""
Phase 6 — pushing seat-status deltas to any client subscribed to an
event's seat map. Kept as a tiny, swallow-errors-and-log wrapper so a
Channels/Redis hiccup never breaks the booking flow that calls it.
"""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def group_name(event_id: int) -> str:
    return f"event_{event_id}_seats"


def broadcast_seat_update(event_id: int, seat_id: int, action: str) -> None:
    """action is one of: held, released, booked, expired"""
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(
            group_name(event_id),
            {"type": "seat.update", "seat_id": seat_id, "action": action},
        )
    except Exception:  # pragma: no cover - best-effort broadcast
        logger.exception("Failed to broadcast seat update for seat %s", seat_id)
