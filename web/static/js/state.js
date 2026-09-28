// Gemeinsamer Zustand des Portals und Hilfen für Rollen/Timer.
export const state = { me: null };

const RANK = { viewer: 1, operator: 2, admin: 3, owner: 4 };
export const can = (role) => !!state.me?.tenant && (RANK[state.me.user.role] || 0) >= RANK[role];

// Timer der aktuellen Seite; werden beim Seitenwechsel beendet.
let timers = [];
export function every(ms, fn) {
  fn();
  const id = setInterval(fn, ms);
  timers.push(id);
  return id;
}
export function clearTimers() {
  timers.forEach(clearInterval);
  timers = [];
}

export function go(path) {
  if (location.hash !== "#" + path) location.hash = "#" + path;
}
