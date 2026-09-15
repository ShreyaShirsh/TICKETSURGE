# TicketSurge — system architecture

```mermaid
flowchart TB
    subgraph Client
        React["React frontend\n(seat map, hold countdown, live status)"]
    end

    subgraph API["Django + DRF (ASGI via Daphne)"]
        HTTP["HTTP API\n/api/seats/:id/hold/\n/api/bookings/confirm/"]
        WS["WebSocket consumer\n/ws/events/:id/seats/"]
    end

    subgraph Data["Data layer"]
        DB[("Oracle / MySQL / PostgreSQL\nSeat, Booking, Ticket\nrow locks + CHECK constraint")]
        Redis[("Redis\nDB0: seat holds (SET NX EX)\nDB2: Channels layer")]
    end

    subgraph Async["Async queue (Phase 4/5)"]
        RMQ[["RabbitMQ\nticketsurge queue -> dead_letter queue"]]
        Worker["Celery workers\nrun_booking_saga\n-> process_payment\n-> generate_ticket\n-> send_confirmation_notification\n(compensate_booking on failure)"]
    end

    React -- "1. hold seat" --> HTTP
    HTTP -- "SET NX EX" --> Redis
    React -- "2. confirm booking" --> HTTP
    HTTP -- "select_for_update +\natomic decrement +\nBooking.create()" --> DB
    HTTP -- "3. enqueue on commit" --> RMQ
    RMQ --> Worker
    Worker -- "payment / ticket / notify" --> DB
    Worker -- "release seat on failure" --> DB
    Worker -. "poison message" .-> RMQ
    HTTP -- "broadcast seat_update" --> Redis
    Redis -- "channel layer" --> WS
    WS -- "live seat deltas" --> React
```

## Request-path vs. async-path summary

| Step | Where | Store touched | Latency budget |
|---|---|---|---|
| Hold a seat | HTTP, synchronous | Redis only | milliseconds |
| Confirm (decrement + create Booking) | HTTP, synchronous, one DB transaction | Primary DB | milliseconds |
| Payment / ticket / notification | Celery worker, asynchronous | Primary DB | seconds (off the request path) |
| Seat-map push | Django Channels, asynchronous | Redis channel layer | near-real-time |

The two synchronous DB writes above are deliberately the *only* two
things standing between "many users click buy at once" and "exactly one
booking exists per seat" — see `core/booking.py` and
`docs/adr/0003-database-strategy.md` for why that's sufficient without a
distributed lock service.
