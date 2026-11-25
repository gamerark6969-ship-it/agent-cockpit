self.addEventListener("push", event => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch { /* Show a generic update. */ }
  const titles = {
    approval_request: "Approval requested", task_completed: "Task completed",
    video_review: "Your video is ready to review", video_published: "Video published",
    video_failed: "Video production needs attention",
  };
  let route = "";
  if (payload.run_id) route = `studio?run=${encodeURIComponent(payload.run_id)}`;
  else if (payload.task_id) route = `tasks/${encodeURIComponent(payload.task_id)}`;
  const url = new URL(route, self.registration.scope).href;
  event.waitUntil(self.registration.showNotification(titles[payload.type] || "Cockpit update", {
    body: String(payload.description || payload.result_summary || "Open the app to see the latest activity.").slice(0, 240),
    icon: new URL("icon-192.png", self.registration.scope).href,
    tag: payload.run_id || payload.task_id || "cockpit-update",
    data: { url },
  }));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || self.registration.scope);
  if (url.origin !== self.location.origin) return;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      if (new URL(client.url).origin === url.origin && "focus" in client) {
        await client.navigate(url.href);
        return client.focus();
      }
    }
    return self.clients.openWindow(url.href);
  })());
});
