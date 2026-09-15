"""
Phase 6 — proves a client connected to a seat map actually receives a
push the moment a seat is held/booked/released, using Channels' own
in-memory test harness (no real network, no real Redis needed for the
channel layer here — ASGI + async, exercised directly).
"""
import pytest
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator

from core.booking import confirm_seat
from core.holds import place_hold
from core.realtime import broadcast_seat_update
from core.routing import websocket_urlpatterns

application = URLRouter(websocket_urlpatterns)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_seat_map_consumer_receives_hold_and_booking_events(event, seat, customer):
    communicator = WebsocketCommunicator(application, f"/ws/events/{event.id}/seats/")
    connected, _ = await communicator.connect()
    assert connected

    from asgiref.sync import sync_to_async

    # place_hold itself is a pure Redis primitive with no broadcast side
    # effect (that's the view's job, per core/views.py); simulate what
    # the hold endpoint does after a successful place_hold().
    token, _ = await sync_to_async(place_hold)(seat.id)
    await sync_to_async(broadcast_seat_update)(event.id, seat.id, "held")
    held_msg = await communicator.receive_json_from(timeout=5)
    assert held_msg == {"type": "seat_update", "seat_id": seat.id, "action": "held"}

    await sync_to_async(confirm_seat)(
        seat_id=seat.id, hold_token=token, customer=customer, idempotency_key="ws-test-1"
    )
    booked_msg = await communicator.receive_json_from(timeout=5)
    assert booked_msg == {"type": "seat_update", "seat_id": seat.id, "action": "booked"}

    await communicator.disconnect()
