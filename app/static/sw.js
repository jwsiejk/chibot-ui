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
  "/static/js/audio/capture_runtime.js?v={{ BUILD_ID }}",
  "/static/js/audio/pcm_sender.js?v={{ BUILD_ID }}",
  "/static/js/audio/vad_client.js?v={{ BUILD_ID }}",
  "/static/js/audio/ws_audio_runtime.js?v={{ BUILD_ID }}",
  "/static/js/audio/pcm-worklet-processor.js?v={{ BUILD_ID }}",
  "/static/js/transcript_view.js?v={{ BUILD_ID }}",
  "/static/js/errors.js?v={{ BUILD_ID }}",
  "/static/js/admin_logs.js?v={{ BUILD_ID }}",
  "/static/js/ws/banner_client.js?v={{ BUILD_ID }}",
  "/static/js/ws/connection.js?v={{ BUILD_ID }}",
  "/static/js/ws/frame_parser.js?v={{ BUILD_ID }}",
  "/static/js/ws/policy_runtime.js?v={{ BUILD_ID }}",
  "/static/js/ws/session_manager.js?v={{ BUILD_ID }}",
  "/static/js/ws/telemetry.js?v={{ BUILD_ID }}",
  "/static/js/ws/transcript_bridge.js?v={{ BUILD_ID }}",
  "/static/js/ws/turns.js?v={{ BUILD_ID }}",
  "/admin/ui/config_panel.js?v={{ BUILD_ID }}",
  "/static/css/styles.css?v={{ BUILD_ID }}",
  "/static/chip/chip.png?v={{ BUILD_ID }}"
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(CORE))
  );
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

  // Only handle GET requests
  if (request.method !== "GET") return;

  const accept = request.headers.get("Accept") || "";
  const isHTML = request.mode === "navigate" || accept.includes("text/html");

  // Never cache HTML navigations; always go to network
  if (isHTML) {
    event.respondWith(fetch(request));
    return;
  }

  event.respondWith((async () => {
    const cache = await caches.open(CACHE_NAME);
    const url = new URL(request.url);

    // Treat versioned static assets as cache-first.
    // This prevents re-downloading for the same BUILD_ID, but avoids caching arbitrary requests.
    const isVersionedStatic =
      (url.pathname.startsWith("/static/") || url.pathname.startsWith("/admin/ui/")) &&
      url.searchParams.has("v");

    if (isVersionedStatic) {
      const cached = await cache.match(request, { ignoreSearch: false });
      if (cached) return cached;

      const resp = await fetch(request);

      // Only cache good responses
      if (resp.ok && resp.type !== "opaque") {
        cache.put(request, resp.clone());
      }

      return resp;
    }

    // Everything else: network-first, cache fallback (useful for transient offline)
    try {
      return await fetch(request);
    } catch (err) {
      const cached = await cache.match(request, { ignoreSearch: false });
      if (cached) return cached;
      throw err;
    }
  })());
});
