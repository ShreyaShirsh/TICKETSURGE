from rest_framework import serializers

from .models import Booking, Event, Seat, Venue


class VenueSerializer(serializers.ModelSerializer):
    class Meta:
        model = Venue
        fields = ["id", "name", "address", "city"]


class EventSerializer(serializers.ModelSerializer):
    venue = VenueSerializer(read_only=True)
    # Populated by queryset annotations in the view (avoids N+1 counts).
    seats_total = serializers.IntegerField(read_only=True)
    seats_available = serializers.IntegerField(read_only=True)

    class Meta:
        model = Event
        fields = [
            "id", "name", "description", "starts_at",
            "venue", "seats_total", "seats_available",
        ]


class SeatSerializer(serializers.ModelSerializer):
    class Meta:
        model = Seat
        fields = ["id", "section", "row", "number", "status"]


class BookingCreateSerializer(serializers.Serializer):
    """Input validation for POST /api/bookings/."""

    event = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all())
    seats = serializers.PrimaryKeyRelatedField(
        queryset=Seat.objects.all(), many=True, allow_empty=False
    )


class HoldCreateSerializer(serializers.Serializer):
    """Input validation for POST /api/holds/ (reserve seats with a TTL)."""

    event = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all())
    seats = serializers.PrimaryKeyRelatedField(
        queryset=Seat.objects.all(), many=True, allow_empty=False
    )


class BookingSerializer(serializers.ModelSerializer):
    seats = SeatSerializer(many=True, read_only=True)

    class Meta:
        model = Booking
        fields = ["id", "reference", "status", "event", "seats", "created_at"]
