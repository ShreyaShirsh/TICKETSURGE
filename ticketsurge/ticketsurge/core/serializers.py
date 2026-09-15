from rest_framework import serializers

from core.models import Booking, Customer, Event, Seat, Ticket


class EventSerializer(serializers.ModelSerializer):
    seats_available = serializers.SerializerMethodField()
    seats_total = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = ["id", "name", "venue", "starts_at", "seats_available", "seats_total"]

    def get_seats_available(self, obj):
        return obj.seats.filter(available__gt=0).count()

    def get_seats_total(self, obj):
        return obj.seats.count()


class SeatSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()

    class Meta:
        model = Seat
        fields = ["id", "event", "section", "row", "number", "price_cents", "available", "status"]

    def get_status(self, obj):
        return "available" if obj.available > 0 else "booked"


class TicketSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ticket
        fields = ["ticket_code", "issued_at"]


class BookingSerializer(serializers.ModelSerializer):
    ticket = TicketSerializer(read_only=True)
    seat = SeatSerializer(read_only=True)

    class Meta:
        model = Booking
        fields = [
            "id", "seat", "status", "idempotency_key", "failure_reason",
            "created_at", "updated_at", "ticket",
        ]


class HoldRequestSerializer(serializers.Serializer):
    pass  # no body needed — seat comes from the URL


class HoldResponseSerializer(serializers.Serializer):
    seat_id = serializers.IntegerField()
    hold_token = serializers.CharField()
    expires_at = serializers.FloatField()


class ConfirmRequestSerializer(serializers.Serializer):
    seat_id = serializers.IntegerField()
    hold_token = serializers.CharField()
    idempotency_key = serializers.CharField(max_length=64)
    customer_email = serializers.EmailField()
    customer_name = serializers.CharField(max_length=120, required=False, allow_blank=True)


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "email", "display_name"]
