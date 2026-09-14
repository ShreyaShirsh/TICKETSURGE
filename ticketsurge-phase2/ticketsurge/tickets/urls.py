from django.urls import path

from . import views

urlpatterns = [
    path("events/", views.EventListView.as_view(), name="event-list"),
    path("events/<int:pk>/", views.EventDetailView.as_view(), name="event-detail"),
    path("events/<int:pk>/seats/", views.EventSeatsView.as_view(), name="event-seats"),
    path("holds/", views.HoldCreateView.as_view(), name="hold-create"),
    path("bookings/", views.BookingCreateView.as_view(), name="booking-create"),
    path("bookings/<int:pk>/", views.BookingDetailView.as_view(), name="booking-detail"),
]
