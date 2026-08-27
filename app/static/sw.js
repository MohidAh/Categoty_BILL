// BillBook Service Worker v38 — PWA with offline support
// Strategy:
//   - Static assets (/static/*): network-first for JS/CSS (avoid stale code
//     after deploy → API contract mismatch), cache-first for images/fonts
//   - Navigation requests (HTML): network-first, fallback to cached index
//   - API requests: network-only (always fresh; offline writes go to IndexedDB queue)
//   - Google Fonts + Chart.js CDN: stale-while-revalidate
// Triggers:
//   - 'backgroundfetchsuccess' / 'sync' → flush offline sales queue
// H16 fix (v8.13.4): bumped IndexedDB schema to v2 — added 'outbox' object
//   store so SW Background Sync can flush returns/payments/adjustments
//   queued by the page. Previously the page upgraded to v2 but the SW
//   still opened v1, so Background Sync never saw the outbox store.
// M2 fix (v8.13.4): static JS/CSS is now network-first (not cache-first)
//   so deployed changes take effect without forcing a hard reload.
//   skipWaiting is still called but only takes effect when the page
//   receives the controllerchange event.
const CACHE_VERSION = 'v48';  // v8.15.0: new appearance engine + settings page
const STATIC_CACHE = `billbook-static-${CACHE_VERSION}`;
const RUNTIME_CACHE = `billbook-runtime-${CACHE_VERSION}`;
const FONT_CACHE = `billbook-fonts-${CACHE_VERSION}`;
const OFFLINE_DB_VERSION = 2;  // H16: bumped from 1

// Critical static assets to pre-cache on install
const PRECACHE_URLS = [
  '/',
  '/static/index.html',
  '/static/css/design-system.css',
  '/static/css/launcher.css',
  '/static/css/shell.css',
  '/static/styles/base.css',
  '/static/styles/components.css',
  '/static/styles/layout.css',
  '/static/styles/pages.css',
  '/static/js/app.js',
  '/static/js/router.js',
  '/static/js/api.js',
  '/static/js/utils.js',
  '/static/js/core/shell.js',
  '/static/js/core/launcher.js',
  '/static/js/core/theme.js',
  '/static/manifest.json',
];

// ─── Install: pre-cache critical assets ───
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => {
      // Use addAll with individual fetches so one failure doesn't break everything
      return Promise.allSettled(
        PRECACHE_URLS.map(url => cache.add(url).catch(() => {}))
      );
    }).then(() => self.skipWaiting())
  );
});

// ─── Activate: clean up old caches ───
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      // Delete any cache that doesn't match current version
      const validCaches = [STATIC_CACHE, RUNTIME_CACHE, FONT_CACHE];
      return Promise.all(
        keys.filter(k => !validCaches.includes(k)).map(k => caches.delete(k))
      );
    }).then(() => self.clients.claim())
  );
});

// ─── Fetch: route-based caching strategy ───
self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Skip non-GET requests (POST/PUT/DELETE go to network)
  if (req.method !== 'GET') return;

  // Skip chrome-extension requests
  if (url.protocol === 'chrome-extension:') return;

  // Skip cross-origin API calls (don't cache external API responses)
  if (url.hostname !== self.location.hostname && url.hostname !== 'cdn.jsdelivr.net' && url.hostname !== 'fonts.googleapis.com' && url.hostname !== 'fonts.gstatic.com') {
    return;
  }

  // 1. Static assets — M2 fix: JS/CSS network-first (avoid stale API
  //    contract after deploy). Images/fonts/icons: cache-first.
  if (url.pathname.startsWith('/static/') || url.pathname === '/') {
    // JS/CSS go network-first; everything else (images, fonts, icons, json) stays cache-first
    if (url.pathname.endsWith('.js') || url.pathname.endsWith('.css')) {
      event.respondWith(networkFirstStatic(req, STATIC_CACHE));
    } else {
      event.respondWith(cacheFirst(req, STATIC_CACHE));
    }
    return;
  }

  // 2. Google Fonts CSS + Fonts — stale-while-revalidate
  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    event.respondWith(staleWhileRevalidate(req, FONT_CACHE));
    return;
  }

  // 3. Chart.js CDN — stale-while-revalidate
  if (url.hostname === 'cdn.jsdelivr.net') {
    event.respondWith(staleWhileRevalidate(req, RUNTIME_CACHE));
    return;
  }

  // 4. API requests — network-only (don't intercept; app handles offline via IndexedDB)
  if (url.pathname.startsWith('/api/')) {
    return; // Let it go to network; will fail when offline
  }

  // 5. Navigation requests — network-first, fallback to cached index.html
  if (req.mode === 'navigate') {
    event.respondWith(networkFirstNavigation(req));
    return;
  }
});

// ─── Caching strategies ───

async function cacheFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  if (cached) {
    // Update cache in background (so next load is fresh)
    fetch(req).then(res => {
      if (res.ok) cache.put(req, res.clone());
    }).catch(() => {});
    return cached;
  }
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (e) {
    // Offline and not cached — return a basic offline page for navigations
    if (req.mode === 'navigate') {
      return caches.match('/static/index.html');
    }
    return new Response('Offline', { status: 503, statusText: 'Offline' });
  }
}

// M2 fix: network-first for JS/CSS — falls back to cache on network failure
// but always tries the network first so the latest deployed code wins.
async function networkFirstStatic(req, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (e) {
    // Network failed — fall back to cached copy (offline mode)
    const cached = await cache.match(req);
    if (cached) return cached;
    // Not cached either — return a 503 so the page can show an offline state
    return new Response('Offline and not cached', { status: 503, statusText: 'Offline' });
  }
}

async function staleWhileRevalidate(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  const fetchPromise = fetch(req).then(res => {
    if (res.ok) cache.put(req, res.clone());
    return res;
  }).catch(() => cached || new Response('Offline', { status: 503 }));
  return cached || fetchPromise;
}

async function networkFirstNavigation(req) {
  try {
    const res = await fetch(req);
    if (res.ok) {
      const cache = await caches.open(STATIC_CACHE);
      cache.put(req, res.clone());
    }
    return res;
  } catch (e) {
    // Offline — return cached index.html
    const cache = await caches.open(STATIC_CACHE);
    return (await cache.match('/static/index.html')) || (await cache.match('/'));
  }
}

// ─── Background Sync: flush offline sales queue ───
self.addEventListener('sync', (event) => {
  if (event.tag === 'flush-sales-queue') {
    event.waitUntil(flushSalesQueue());
  }
});

self.addEventListener('periodicsync', (event) => {
  if (event.tag === 'flush-sales-queue') {
    event.waitUntil(flushSalesQueue());
  }
});

// Listen for messages from the page (manual flush trigger)
self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') {
    self.skipWaiting();
  } else if (event.data === 'FLUSH_QUEUE') {
    event.waitUntil(flushSalesQueue().then(() => {
      // Notify all clients that queue was flushed
      self.clients.matchAll().then(clients => {
        clients.forEach(c => c.postMessage({ type: 'QUEUE_FLUSHED' }));
      });
    }));
  }
});

// Flush the offline sales queue by posting each queued sale to /api/sales.
// H16 fix (v8.13.4): also drain the 'outbox' store (returns, payments,
// adjustments queued by the page) so Background Sync catches up on ALL
// pending writes — not just sales.
async function flushSalesQueue() {
  const db = await openOfflineDB();
  // 1. Drain the sales_queue store
  const tx = db.transaction(['sales_queue'], 'readonly');
  const store = tx.objectStore('sales_queue');
  const queued = await store.getAll();
  if (queued.length) {
    for (const item of queued) {
      try {
        const res = await fetch('/api/sales', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify(item.payload),
        });
        if (res.ok) {
          const result = await res.json();
          // Remove from queue on success
          const delTx = db.transaction(['sales_queue'], 'readwrite');
          delTx.objectStore('sales_queue').delete(item.id);
          await delTx.complete;
          // Notify clients of successful sync
          const clients = await self.clients.matchAll();
          clients.forEach(c => c.postMessage({
            type: 'SALE_SYNCED',
            tempId: item.id,
            invoice_no: result.invoice_no,
          }));
        } else {
          // Server rejected — leave in queue, mark as failed
          const updTx = db.transaction(['sales_queue'], 'readwrite');
          const updStore = updTx.objectStore('sales_queue');
          const existing = await updStore.get(item.id);
          if (existing) {
            existing.sync_attempts = (existing.sync_attempts || 0) + 1;
            existing.last_error = `HTTP ${res.status}`;
            if (existing.sync_attempts >= 5) {
              updStore.delete(item.id);
            } else {
              updStore.put(existing);
            }
          }
          await updTx.complete;
          // M16 fix: don't break on 4xx — continue draining the rest of
          // the queue. The previous code broke on first failure, which
          // quarantined all later items even if they would succeed.
        }
      } catch (e) {
        // Network error — leave in queue, try again on next sync.
        // Don't break; continue with the next item so a single offline
        // moment doesn't block all subsequent writes.
      }
    }
  }
  // 2. H16: drain the outbox store (returns, payments, adjustments)
  if (db.objectStoreNames.contains('outbox')) {
    const obTx = db.transaction(['outbox'], 'readonly');
    const outbox = await obTx.objectStore('outbox').getAll();
    for (const item of outbox) {
      try {
        const url = item.url || '/api/sales';
        const method = item.method || 'POST';
        const res = await fetch(url, {
          method,
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify(item.payload || {}),
        });
        if (res.ok) {
          const delObTx = db.transaction(['outbox'], 'readwrite');
          delObTx.objectStore('outbox').delete(item.id);
          await delObTx.complete;
        } else {
          // Mark failed; continue draining the rest
          const updObTx = db.transaction(['outbox'], 'readwrite');
          const updStore = updObTx.objectStore('outbox');
          const existing = await updStore.get(item.id);
          if (existing) {
            existing.sync_attempts = (existing.sync_attempts || 0) + 1;
            existing.last_error = `HTTP ${res.status}`;
            if (existing.sync_attempts >= 5) {
              updStore.delete(item.id);
            } else {
              updStore.put(existing);
            }
          }
          await updObTx.complete;
        }
      } catch (e) {
        // Network error — leave in queue, continue
      }
    }
  }
}

function openOfflineDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('billbook-offline', OFFLINE_DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains('sales_queue')) {
        db.createObjectStore('sales_queue', { keyPath: 'id', autoIncrement: true });
      }
      // H16: add outbox store for returns / payments / adjustments
      if (!db.objectStoreNames.contains('outbox')) {
        db.createObjectStore('outbox', { keyPath: 'id', autoIncrement: true });
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
  });
}
