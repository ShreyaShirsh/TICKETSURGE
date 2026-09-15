import json

from channels.generic.websocket import AsyncWebsocketConsumer

from core.realtime import group_name


class SeatMapConsumer(AsyncWebsocketConsumer):
    """Clients connect at /ws/events/<event_id>/seats/ and receive a
    message every time a seat in that event is held, released, booked,
    or its hold expires. Kept deliberately dumb: the client re-fetches
    seat state on connect via the REST API and treats these messages as
    deltas, not as the source of truth."""

    async def connect(self):
        self.event_id = self.scope["url_route"]["kwargs"]["event_id"]
        self.group = group_name(self.event_id)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group, self.channel_name)

    async def seat_update(self, event):
        await self.send(
            text_data=json.dumps(
                {"type": "seat_update", "seat_id": event["seat_id"], "action": event["action"]}
            )
        )
