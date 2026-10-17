// Empty base = same origin. On Vercel the Django service is mounted at
// /api, /admin, /health and /ws on the same domain as this frontend (see the
// repo-root vercel.json); in `npm run dev` the Vite proxy forwards those paths.
const API_BASE = import.meta.env.VITE_API_BASE || "";
const WS_BASE =
  import.meta.env.VITE_WS_BASE ||
  (API_BASE
    ? API_BASE.replace(/^http/, "ws")
    : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`);

async function request(path, options = {}) {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error(data.detail || `Request failed: ${resp.status}`);
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const api = {
  listEvents: () => request("/api/events/"),
  listSeats: (eventId) => request(`/api/events/${eventId}/seats/`),
  holdSeat: (seatId) => request(`/api/seats/${seatId}/hold/`, { method: "POST" }),
  releaseHold: (seatId, holdToken) =>
    request(`/api/seats/${seatId}/release-hold/`, {
      method: "POST",
      body: JSON.stringify({ hold_token: holdToken }),
    }),
  confirmBooking: (payload) =>
    request("/api/bookings/confirm/", { method: "POST", body: JSON.stringify(payload) }),
  getBooking: (id) => request(`/api/bookings/${id}/`),
};

export function connectSeatSocket(eventId, onMessage) {
  const ws = new WebSocket(`${WS_BASE}/ws/events/${eventId}/seats/`);
  ws.onmessage = (evt) => {
    try {
      onMessage(JSON.parse(evt.data));
    } catch {
      /* ignore malformed frames */
    }
  };
  return ws;
}
