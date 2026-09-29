// Service Worker der Karten-App (/k). Cacht nur die App-Hülle – nie Status- oder Kartendaten:
// ohne Netz zeigt die App „keine aktuellen Daten“ statt veralteter Zustände.
const SHELL = "sbb-card-v1";
const FILES = ["/k", "/manifest.webmanifest", "/static/css/fonts.css", "/static/css/tokens.css", "/static/css/components.css",
  "/static/css/public.css", "/static/js/card-app.js", "/static/js/public-kit.js", "/static/img/icon.svg",
  "/static/img/app-192.png", "/static/fonts/AtkinsonHyperlegible-400.woff2", "/static/fonts/AtkinsonHyperlegible-700.woff2"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  if (!FILES.includes(url.pathname)) return;
  // Netz zuerst (immer aktuelle Version), Cache nur als Rückfall ohne Verbindung
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok) { const copy = r.clone(); caches.open(SHELL).then((c) => c.put(url.pathname, copy)); }
    return r;
  }).catch(() => caches.match(url.pathname)));
});

self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (_) { data = { body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(data.title || "Smart Bicycle Box", {
    body: data.body || "", icon: "/static/img/app-192.png", badge: "/static/img/app-192.png",
    tag: data.type || "sbb", renotify: true, requireInteraction: data.type === "waitlist.offered",
    data: { url: data.url || "/k" },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || "/k";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    const open = list.find((c) => new URL(c.url).pathname === target);
    return open ? open.focus() : self.clients.openWindow(target);
  }));
});
