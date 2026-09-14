from django.contrib import admin

from .models import Booking, Event, Seat, Venue


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ("name", "city")
    search_fields = ("name", "city")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("name", "venue", "starts_at")
    list_filter = ("venue",)
    date_hierarchy = "starts_at"


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ("event", "section", "row", "number", "status", "booking")
    list_filter = ("event", "status", "section")
    search_fields = ("row", "number")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "event", "status", "created_at")
    list_filter = ("status", "event")
    readonly_fields = ("reference", "created_at", "updated_at")
