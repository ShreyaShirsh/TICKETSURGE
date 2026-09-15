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
    api.listEvents().then((evts) => {
      setEvents(evts);
      if (evts.length) setEventId(evts[0].id);
    });
  }, []);

  useEffect(() => {
    if (!eventId) return;
    api.listSeats(eventId).then(setSeats);

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
    return () => ws.close();
  }, [eventId]);

  // Hold expired locally -> drop it so the UI stops offering "confirm".
  useEffect(() => {
    if (remaining === 0) setHold(null);
  }, [remaining]);

  const seatMap = useMemo(() => {
    const bySection = {};
    for (const seat of seats) {
      (bySection[seat.section] ||= []).push(seat);
    }
    return bySection;
  }, [seats]);

  async function onSeatClick(seat) {
    setError(null);
    if (seat.status !== "available" || booking) return;
    try {
      const res = await api.holdSeat(seat.id);
      setHold({ seatId: seat.id, token: res.hold_token, expiresAt: res.expires_at });
    } catch (e) {
      setError(e.message);
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
      if (b.status !== "PENDING") clearInterval(interval);
    }, 1200);
  }

  async function onCancelHold() {
    if (!hold) return;
    await api.releaseHold(hold.seatId, hold.token).catch(() => {});
    setHold(null);
  }

  return (
    <div className="app">
      <header>
        <h1>TicketSurge</h1>
        <p className="tagline">Flash-sale-safe seat booking — zero overselling, guaranteed by the database.</p>
      </header>

      <section className="controls">
        <label>
          Event:{" "}
          <select value={eventId ?? ""} onChange={(e) => { setEventId(Number(e.target.value)); setHold(null); setBooking(null); }}>
            {events.map((e) => (
              <option key={e.id} value={e.id}>
                {e.name} ({e.seats_available}/{e.seats_total} available)
              </option>
            ))}
          </select>
        </label>
        <label>
          Email:{" "}
          <input value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
      </section>

      {error && <div className="error">{error}</div>}

      {hold && !booking && (
        <div className="hold-banner">
          Seat held — confirm within <strong>{formatMMSS(remaining)}</strong>
          <button onClick={onConfirm}>Confirm booking</button>
          <button className="ghost" onClick={onCancelHold}>Release</button>
        </div>
      )}

      {booking && (
        <div className={`booking-banner ${booking.status.toLowerCase()}`}>
          Booking #{booking.id}: <strong>{booking.status}</strong>
          {booking.status === "CONFIRMED" && booking.ticket && (
            <> — ticket <code>{booking.ticket.ticket_code}</code></>
          )}
          {booking.status === "FAILED_DEAD_LETTER" && (
            <> — {booking.failure_reason || "payment failed"}, seat released automatically.</>
          )}
          <button className="ghost" onClick={() => { setBooking(null); setHold(null); }}>Book another seat</button>
        </div>
      )}

      <div className="legend">
        <span><i className="dot available" /> available</span>
        <span><i className="dot held" /> held</span>
        <span><i className="dot booked" /> booked</span>
      </div>

      <div className="seat-map">
        {Object.entries(seatMap).map(([section, sectionSeats]) => (
          <div key={section} className="section">
            <h3>Section {section}</h3>
            <div className="grid">
              {sectionSeats.map((seat) => (
                <button
                  key={seat.id}
                  className={`seat ${seat.status} ${hold?.seatId === seat.id ? "selected" : ""}`}
                  disabled={seat.status !== "available" && hold?.seatId !== seat.id}
                  title={`${seat.section}-${seat.row}${seat.number} · $${(seat.price_cents / 100).toFixed(2)}`}
                  onClick={() => onSeatClick(seat)}
                >
                  {seat.row}{seat.number}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
