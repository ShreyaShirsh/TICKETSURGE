from django.contrib import admin

from core.models import Booking, Customer, Event, Seat, Ticket

admin.site.register(Customer)
admin.site.register(Event)
admin.site.register(Seat)
admin.site.register(Booking)
admin.site.register(Ticket)
