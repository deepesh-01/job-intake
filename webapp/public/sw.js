// Minimal service worker — present so the browser treats the site as
// installable (Chrome/Edge require a registered SW for the install
// prompt; Safari just needs the manifest).
//
// We deliberately do NOT cache `/api/*` responses — the Sheet is the
// truth and stale data would mislead. Static assets (JS bundle, icons,
// fonts) are cached on first hit and served stale-while-revalidate so
// repeat opens feel instant.

const CACHE = "job-intake-v1"
const STATIC = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"]

self.addEventListener("install", (event) => {
  // Skip waiting so a new SW takes over immediately on next page load.
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(STATIC).catch(() => {})),
  )
  self.skipWaiting()
})

self.addEventListener("activate", (event) => {
  event.waitUntil(
    Promise.all([
      // Clean old cache versions
      caches.keys().then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
      ),
      self.clients.claim(),
    ]),
  )
})

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url)

  // API + non-GET: always go to network (no cache).
  if (url.pathname.startsWith("/api/") || event.request.method !== "GET") {
    return  // let browser handle normally
  }

  // Static assets: cache-first, network-fallback, refill cache in background.
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const fetchPromise = fetch(event.request)
        .then((res) => {
          if (res.ok) {
            const clone = res.clone()
            caches.open(CACHE).then((c) => c.put(event.request, clone))
          }
          return res
        })
        .catch(() => cached) // offline — return whatever's cached
      return cached || fetchPromise
    }),
  )
})
