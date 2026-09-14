from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Booking, Event, Seat
from .serializers import (
    BookingCreateSerializer,
    BookingSerializer,
    EventSerializer,
    HoldCreateSerializer,
    SeatSerializer,
)
from .services import (
    BookingError,
    SeatUnavailable,
    create_booking,
    get_demo_user,
    hold_seats,
)


def events_with_counts():
    """Events annotated with total and available seat counts (single query)."""
    return (
        Event.objects.select_related("venue")
        .annotate(
            seats_total=Count("seats"),
            seats_available=Count(
                "seats", filter=Q(seats__status=Seat.Status.AVAILABLE)
            ),
        )
        .order_by("starts_at", "id")  # stable order for pagination
    )


class EventListView(generics.ListAPIView):
    """GET /api/events/ — list events with seat availability."""

    queryset = events_with_counts()
    serializer_class = EventSerializer


class EventDetailView(generics.RetrieveAPIView):
    """GET /api/events/{id}/"""

    queryset = events_with_counts()
    serializer_class = EventSerializer


class EventSeatsView(generics.ListAPIView):
    """GET /api/events/{id}/seats/  (optional ?status=AVAILABLE filter)."""

    serializer_class = SeatSerializer

    def get_queryset(self):
        event = get_object_or_404(Event, pk=self.kwargs["pk"])
        qs = event.seats.all()
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        return qs


def _current_user(request):
    return request.user if request.user.is_authenticated else get_demo_user()


class HoldCreateView(APIView):
    """POST /api/holds/  { "event": <id>, "seats": [<id>, ...] }

    Reserves seats for the caller with a TTL (Redis). Returns the countdown.
    """

    def post(self, request):
        serializer = HoldCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = serializer.validated_data["event"]
        seats = serializer.validated_data["seats"]
        user = _current_user(request)

        try:
            result = hold_seats(user=user, event=event, seat_ids=[s.id for s in seats])
        except SeatUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except BookingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_201_CREATED)


class BookingCreateView(APIView):
    """POST /api/bookings/  { "event": <id>, "seats": [<id>, ...] }

    Confirms a booking. Send an ``Idempotency-Key`` header to make retries safe:
    the same key returns the original booking (200) instead of creating another.
    """

    def post(self, request):
        serializer = BookingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = serializer.validated_data["event"]
        seats = serializer.validated_data["seats"]
        user = _current_user(request)
        idempotency_key = request.headers.get("Idempotency-Key")

        try:
            booking, created = create_booking(
                user=user,
                event=event,
                seat_ids=[s.id for s in seats],
                idempotency_key=idempotency_key,
            )
        except SeatUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except BookingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(BookingSerializer(booking).data, status=code)


class BookingDetailView(generics.RetrieveAPIView):
    """GET /api/bookings/{id}/"""

    queryset = Booking.objects.prefetch_related("seats").all()
    serializer_class = BookingSerializer
