const CACHE_NAME = "finance-tracker-v11";

const FILES_TO_CACHE = [
    "/",
    "/static/style.css",
    "/static/i18n.js",
    "/static/script.js",
    "/static/manifest.json",
    "/static/icons/icon-192.png",
    "/static/icons/icon-512.png"
];

self.addEventListener("install", event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            // "reload" skips the browser HTTP cache, which may still hold files
            // from an earlier deploy.
            .then(cache => cache.addAll(FILES_TO_CACHE.map(url => new Request(url, { cache: "reload" }))))
    );

    self.skipWaiting();
});

self.addEventListener("activate", event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(
                keys
                    .filter(key => key !== CACHE_NAME)
                    .map(key => caches.delete(key))
            )
        )
    );

    self.clients.claim();
});

// Only the app shell is cached. API responses carry the user's data and must
// always come from the network: a cached copy would show stale operations and
// balance, and would outlive the session. Offline, the app falls back to its
// own last-rendered snapshot instead.
function isAppShell(request) {
    const url = new URL(request.url);
    return request.method === "GET"
        && url.origin === self.location.origin
        && (url.pathname === "/" || url.pathname.startsWith("/static/"));
}

self.addEventListener("fetch", event => {
    if (!isAppShell(event.request)) return;
    // Revalidate with the server (a cheap 304 when unchanged) instead of trusting
    // a copy the browser cached before Cache-Control: no-cache was sent.
    const fresh = new Request(event.request.url, { cache: "no-cache", credentials: "same-origin" });
    event.respondWith(
        fetch(fresh)
            .then(response => {
                if (response.ok) {
                    const copy = response.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
                }
                return response;
            })
            .catch(() => caches.match(event.request))
    );
});
