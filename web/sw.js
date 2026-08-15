// Minimal service worker: enough for Android/Chrome PWA installability,
// deliberately NO asset caching. This app has already been bitten by stale
// cached assets after deployments; the server's no-cache/ETag headers manage
// freshness, and the worker only adds a friendly offline page.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

const OFFLINE_HTML = `<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MangaBrain</title>
<body style="background:#12141a;color:#c7cdda;font-family:system-ui;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center"><h1>MangaBrain is unreachable</h1>
<p>Check your connection (or the tunnel) and pull to retry.</p></div></body>`;

self.addEventListener("fetch", (event) => {
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).catch(
        () =>
          new Response(OFFLINE_HTML, {
            status: 503,
            headers: { "Content-Type": "text/html; charset=utf-8" },
          })
      )
    );
  }
});
