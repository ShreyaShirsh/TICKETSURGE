from django.conf import settings
from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from core.booking import SeatUnavailable, confirm_seat
from core.holds import HoldConflict, InvalidHold, place_hold, release_hold
from core.models import Booking, Customer, Event, Seat
from core.realtime import broadcast_seat_update
from core.serializers import (
    BookingSerializer,
    ConfirmRequestSerializer,
    EventSerializer,
    HoldResponseSerializer,
    SeatSerializer,
)
from core.tasks import run_booking_saga


class HealthView(APIView):
    def get(self, request):
        return Response({"status": "ok"})


class EventViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Event.objects.all()
    serializer_class = EventSerializer

    @action(detail=True, methods=["get"])
    def seats(self, request, pk=None):
        event = self.get_object()
        seats = event.seats.all()
        return Response(SeatSerializer(seats, many=True).data)


class SeatViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Seat.objects.all()
    serializer_class = SeatSerializer
    filterset_fields = ["event"]

    def get_queryset(self):
        qs = super().get_queryset()
        event_id = self.request.query_params.get("event")
        if event_id:
            qs = qs.filter(event_id=event_id)
        return qs

    @action(detail=True, methods=["post"])
    def hold(self, request, pk=None):
        """Phase 3, step 1: a cheap, short-lived Redis claim (SET NX EX).
        Rejects fast if the seat is already sold or already held."""
        seat = self.get_object()
        if not seat.is_available:
            return Response({"detail": "Seat is already booked."}, status=status.HTTP_409_CONFLICT)
        try:
            token, expires_at = place_hold(seat.id)
        except HoldConflict as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        broadcast_seat_update(seat.event_id, seat.id, "held")
        data = HoldResponseSerializer({"seat_id": seat.id, "hold_token": token, "expires_at": expires_at}).data
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="release-hold")
    def release_hold_action(self, request, pk=None):
        seat = self.get_object()
        hold_token = request.data.get("hold_token")
        release_hold(seat.id, hold_token)
        broadcast_seat_update(seat.event_id, seat.id, "released")
        return Response(status=status.HTTP_204_NO_CONTENT)


class BookingViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Booking.objects.select_related("seat", "customer", "ticket")
    serializer_class = BookingSerializer

    @action(detail=False, methods=["post"])
    def confirm(self, request):
        """Phase 3, step 2: the durable, race-free write. Fast — the
        booking is created as PENDING and the slow side-effects are
        handed off to the Celery saga before this returns."""
        req = ConfirmRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        v = req.validated_data

        customer, _ = Customer.objects.get_or_create(
            email=v["customer_email"],
            defaults={"display_name": v.get("customer_name") or v["customer_email"]},
        )

        try:
            result = confirm_seat(
                seat_id=v["seat_id"],
                hold_token=v["hold_token"],
                customer=customer,
                idempotency_key=v["idempotency_key"],
            )
        except InvalidHold as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except SeatUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        if result.created:
            # Enqueue after the DB transaction commits so the worker never
            # races ahead of the booking row it's about to operate on.
            transaction.on_commit(lambda: run_booking_saga.delay(result.booking.id))

        serializer = BookingSerializer(result.booking)
        http_status = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
        return Response(serializer.data, status=http_status)
