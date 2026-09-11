"use strict";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));

self.addEventListener("push", event => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_error) {
    payload = { body: event.data ? event.data.text() : "" };
  }
  const channelId = String(payload.channel_id || "");
  const options = {
    body: payload.body || "Channel 0.2 has something worth watching.",
    icon: "/static/favicon.svg",
    badge: "/static/favicon.svg",
    tag: payload.tag || "m3u-picker-alert",
    renotify: true,
    data: {
      url: payload.url || "/remote",
      channel_id: channelId,
      channel_name: String(payload.channel_name || "the channel"),
    },
  };
  if (channelId) {
    options.actions = [{ action: "play-on-02", title: "Play on 0.2" }];
  }
  event.waitUntil(self.registration.showNotification(payload.title || "Channel 0.2", options));
});
async function openRemote(url) {
  const target = new URL(url || "/remote", self.location.origin).href;
  const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  const existing = windows.find(client => client.url.startsWith(self.location.origin));
  if (existing) {
    await existing.navigate(target);
    return existing.focus();
  }
  return self.clients.openWindow(target);
}

self.addEventListener("notificationclick", event => {
  const data = event.notification.data || {};
  event.notification.close();
  if (event.action === "play-on-02" && data.channel_id) {
    event.waitUntil((async () => {
      const response = await fetch("/api/remote?mode=tv", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel_id: data.channel_id }),
        cache: "no-store",
      });
      if (!response.ok) {
        await openRemote(data.url);
      }
    })());
    return;
  }
  event.waitUntil(openRemote(data.url));
});
