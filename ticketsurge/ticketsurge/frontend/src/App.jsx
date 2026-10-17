import { useEffect, useMemo, useRef, useState } from "react";
import { api, connectSeatSocket } from "./api";
import "./App.css";

function useCountdown(expiresAt) {
  const [remaining, setRemaining] = useState(null);
  useEffect(() => {
    if (!expiresAt) {
      setRemaining(null);
      return;
    }
    const tick = () => setRemaining(Math.max(0, Math.round(expiresAt - Date.now() / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [expiresAt]);
  return remaining;
}

function formatMMSS(totalSeconds) {
  if (totalSeconds == null) return "";
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatPrice(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}

function formatDate(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

const STATUS_TEXT = {
  PENDING: "Processing payment…",
  CONFIRMED: "Booking confirmed",
  FAILED: "Booking failed",
  FAILED_DEAD_LETTER: "Payment failed",
};

export default function App() {
  const [events, setEvents] = useState([]);
  const [eventId, setEventId] = useState(null);
  const [seats, setSeats] = useState([]);
  const [hold, setHold] = useState(null); // { seatId, token, expiresAt }
  const [booking, setBooking] = useState(null);
  const [email, setEmail] = useState("fan@example.com");
  const [error, setError] = useState(null);
  const wsRef = useRef(null);
  const remaining = useCountdown(hold?.expiresAt);

  useEffect(() => {
    api
      .listEvents()
      .then((evts) => {
        setEvents(evts);
        if (evts.length) setEventId(evts[0].id);
      })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!eventId) return;
    api.listSeats(eventId).then(setSeats).catch((e) => setError(e.message));

    wsRef.current?.close();
    const ws = connectSeatSocket(eventId, (msg) => {
      if (msg.type !== "seat_update") return;
      setSeats((prev) =>
        prev.map((s) =>
          s.id === msg.seat_id
            ? { ...s, status: msg.action === "released" ? "available" : msg.action === "held" ? "held" : "booked" }
            : s
        )
      );
    });
    wsRef.current = ws;

    // Fallback: if the socket can't connect or drops (no channel layer,
    // serverless instance recycled, flaky network), poll the seat list so
    // the map still stays current.
    let cancelled = false;
    let pollId = null;
    ws.onclose = () => {
      if (cancelled || pollId) return;
      pollId = setInterval(() => {
        api.listSeats(eventId).then(setSeats).catch(() => {});
      }, 3000);
    };
    return () => {
      cancelled = true;
      clearInterval(pollId);
      ws.close();
    };
  }, [eventId]);

  // Hold expired locally -> drop it so the UI stops offering "confirm".
  useEffect(() => {
    if (remaining === 0) setHold(null);
  }, [remaining]);

  // { section: { price, rows: { row: [seats sorted by number] } } }
  const seatMap = useMemo(() => {
    const sections = {};
    for (const seat of seats) {
      const sec = (sections[seat.section] ||= { price: seat.price_cents, rows: {} });
      (sec.rows[seat.row] ||= []).push(seat);
    }
    for (const sec of Object.values(sections)) {
      for (const row of Object.values(sec.rows)) row.sort((a, b) => a.number - b.number);
    }
    return sections;
  }, [seats]);

  const event = events.find((e) => e.id === eventId);
  const chosenSeat = booking?.seat ?? (hold ? seats.find((s) => s.id === hold.seatId) : null);

  function setSeatStatus(seatId, status) {
    setSeats((prev) => prev.map((s) => (s.id === seatId ? { ...s, status } : s)));
  }

  async function onSeatClick(seat) {
    setError(null);
    if (seat.status !== "available" || booking || hold) return;
    try {
      const res = await api.holdSeat(seat.id);
      setHold({ seatId: seat.id, token: res.hold_token, expiresAt: res.expires_at });
      setSeatStatus(seat.id, "held");
    } catch (e) {
      setError(e.message);
      if (e.status === 409) api.listSeats(eventId).then(setSeats).catch(() => {});
    }
  }

  async function onConfirm() {
    if (!hold) return;
    setError(null);
    try {
      const res = await api.confirmBooking({
        seat_id: hold.seatId,
        hold_token: hold.token,
        idempotency_key: `web-${hold.seatId}-${hold.token}`,
        customer_email: email,
      });
      setBooking(res);
      pollBooking(res.id);
    } catch (e) {
      setError(e.message);
      setHold(null);
    }
  }

  function pollBooking(id) {
    const interval = setInterval(async () => {
      const b = await api.getBooking(id);
      setBooking(b);
      if (b.status !== "PENDING") {
        clearInterval(interval);
        api.listSeats(eventId).then(setSeats).catch(() => {});
      }
    }, 1200);
  }

  async function onCancelHold() {
    if (!hold) return;
    await api.releaseHold(hold.seatId, hold.token).catch(() => {});
    setSeatStatus(hold.seatId, "available");
    setHold(null);
  }

  function onEventChange(id) {
    setEventId(id);
    setHold(null);
    setBooking(null);
    setError(null);
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">T</span>
          TicketSurge
        </div>
        <p className="tagline">Flash-sale-safe seat booking — no overselling, ever.</p>
      </header>

      <section className="event-card">
        <div>
          <h1>{event?.name ?? "Loading events…"}</h1>
          {event && (
            <p className="event-meta">
              {event.venue} · {formatDate(event.starts_at)} ·{" "}
              <span className="availability">
                {event.seats_available}/{event.seats_total}
              </span>{" "}
              seats left
            </p>
          )}
        </div>
        {events.length > 1 && (
          <label className="field">
            Event
            <select value={eventId ?? ""} onChange={(e) => onEventChange(Number(e.target.value))}>
              {events.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </section>

      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}

      <div className="layout">
        <section className="panel">
          <div className="map-head">
            <h2>Choose a seat</h2>
            <div className="legend">
              <span><i className="swatch" /> Available</span>
              <span><i className="swatch held" /> Held</span>
              <span><i className="swatch booked" /> Sold</span>
            </div>
          </div>

          <div className="stage">STAGE</div>

          {Object.entries(seatMap).map(([section, { price, rows }]) => (
            <div key={section} className="section">
              <div className="section-head">
                <h3>Section {section}</h3>
                <span className="section-price">{formatPrice(price)}</span>
              </div>
              <div className="rows">
                {Object.entries(rows).map(([row, rowSeats]) => (
                  <div key={row} className="row">
                    <span className="row-label">R{row}</span>
                    {rowSeats.map((seat) => {
                      const selected = hold?.seatId === seat.id;
                      return (
                        <button
                          key={seat.id}
                          className={`seat ${seat.status} ${selected ? "selected" : ""}`}
                          disabled={!selected && (seat.status !== "available" || !!hold || !!booking)}
                          title={`Section ${seat.section}, row ${seat.row}, seat ${seat.number} · ${formatPrice(seat.price_cents)}`}
                          aria-label={`Section ${seat.section} row ${seat.row} seat ${seat.number}, ${seat.status}`}
                          onClick={() => onSeatClick(seat)}
                        >
                          {seat.number}
                        </button>
                      );
                    })}
                    <span className="row-label">R{row}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </section>

        <aside className="panel checkout">
          <h2>Your booking</h2>

          {!chosenSeat && (
            <p className="muted">
              Pick an available seat on the map. It’s held for you for 7 minutes while you check out.
            </p>
          )}

          {chosenSeat && (
            <dl className="summary">
              <dt>Section</dt>
              <dd>{chosenSeat.section}</dd>
              <dt>Row</dt>
              <dd>{chosenSeat.row}</dd>
              <dt>Seat</dt>
              <dd>{chosenSeat.number}</dd>
              <dt>Price</dt>
              <dd>{formatPrice(chosenSeat.price_cents)}</dd>
            </dl>
          )}

          {hold && !booking && (
            <>
              <div className="timer">
                Seat held — <strong>{formatMMSS(remaining)}</strong> left to confirm
              </div>
              <label className="field">
                Email
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
              </label>
              <div className="actions">
                <button className="btn" onClick={onConfirm} disabled={!email}>
                  Confirm booking
                </button>
                <button className="btn ghost" onClick={onCancelHold}>
                  Release seat
                </button>
              </div>
            </>
          )}

          {booking && (
            <>
              <div className={`status ${booking.status.toLowerCase()}`}>
                <strong>{STATUS_TEXT[booking.status] ?? booking.status}</strong>
                {booking.status === "CONFIRMED" && booking.ticket && (
                  <p>
                    Ticket <code>{booking.ticket.ticket_code}</code>
                  </p>
                )}
                {booking.status === "FAILED_DEAD_LETTER" && (
                  <p>{booking.failure_reason || "Payment failed"} — the seat was released.</p>
                )}
                <p className="muted">Booking #{booking.id}</p>
              </div>
              <button
                className="btn ghost"
                onClick={() => {
                  setBooking(null);
                  setHold(null);
                }}
              >
                Book another seat
              </button>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
