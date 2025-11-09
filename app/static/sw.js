const CACHE_VERSION = "{{ BUILD_ID }}";
const CACHE_NAME = "app-cache-" + CACHE_VERSION;
const CORE = [
  "/version.json",
  "/static/js/app.js?v={{ BUILD_ID }}",
  "/static/js/auth_ui.js?v={{ BUILD_ID }}",
  "/static/js/version.js?v={{ BUILD_ID }}",
  "/static/js/state.js?v={{ BUILD_ID }}",
  "/static/js/audio_player.js?v={{ BUILD_ID }}",
  "/static/js/ws_client.js?v={{ BUILD_ID }}",
  "/static/js/transcript_view.js?v={{ BUILD_ID }}",
  "/static/js/errors.js?v={{ BUILD_ID }}",
  "/static/js/admin_logs.js?v={{ BUILD_ID }}",
  "/admin/ui/config_panel.js?v={{ BUILD_ID }}",
  "/static/css/styles.css?v={{ BUILD_ID }}",
  "/static/chip/chip.png?v={{ BUILD_ID }}"
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(CORE)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.map((key) => (key !== CACHE_NAME ? caches.delete(key) : null)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.mode === "navigate" || request.headers.get("Accept")?.includes("text/html")) {
    event.respondWith(fetch(request));
    return;
  }

  if (
    (url.pathname.startsWith("/static/") || url.pathname.startsWith("/admin/ui/")) &&
    url.searchParams.get("v")
  ) {
    event.respondWith(
      caches.open(CACHE_NAME).then(async (cache) => {
        const match = await cache.match(request, { ignoreSearch: false });
        if (match) {
          return match;
        }
        const response = await fetch(request);
        cache.put(request, response.clone());
        return response;
      })
    );
    return;
  }

  event.respondWith(
    fetch(request).catch(() => caches.match(request, { ignoreSearch: false }))
  );
});
