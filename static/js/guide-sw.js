self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(self.clients.claim());
});

// Keep Guide navigations under a Guide-owned worker without touching media
// requests. In particular, /guide/listen must stream directly to the audio
// element rather than being proxied through a service worker.
self.addEventListener("fetch", event => {
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request));
  }
});
