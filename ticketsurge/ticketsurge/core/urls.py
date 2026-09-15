from rest_framework.routers import DefaultRouter

from core.views import BookingViewSet, EventViewSet, SeatViewSet

router = DefaultRouter()
router.register("events", EventViewSet, basename="event")
router.register("seats", SeatViewSet, basename="seat")
router.register("bookings", BookingViewSet, basename="booking")

urlpatterns = router.urls
