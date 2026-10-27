// Swath service worker: caches the app shell and vendored libraries so the app opens with no signal.
// Only same-origin GETs of "/" and "/static/*" are handled; the API always goes to the network.
const CACHE_NAME = "swath-v0.1.0";
const SHELL = ["/", "/static/app.css?v=0.1.0", "/static/app.js?v=0.1.0", "/static/vendor/leaflet/leaflet.js", "/static/vendor/leaflet/leaflet.css",
  "/static/vendor/leaflet-draw/leaflet.draw.js", "/static/vendor/leaflet-draw/leaflet.draw.css", "/static/icon-192.png", "/static/manifest.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE_NAME).then((c) => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname === "/") {   // network first, so a new version shows up at once
    e.respondWith(fetch(e.request).then((r) => { const copy = r.clone(); caches.open(CACHE_NAME).then((c) => c.put("/", copy)); return r; })
      .catch(() => caches.match("/")));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((r) => {
      if (r.ok) { const copy = r.clone(); caches.open(CACHE_NAME).then((c) => c.put(e.request, copy)); }
      return r;
    })));
  }
});
