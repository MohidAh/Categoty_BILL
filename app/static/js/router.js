// Simple hash-based router with module registry
import { $, $$ } from './utils.js';
import { renderShell, bindShellEvents, getAppForPath, APP_CONFIGS } from './core/shell.js';

/**
 * Determine if a nav item should be marked active.
 *
 * v8.4 fix: Previously, a path like /stock/adjustments would match BOTH
 * /stock (prefix) and /stock/adjustments (exact), highlighting 2 items.
 * Now we use "longest prefix wins" — only the single longest-matching
 * nav item is marked active.
 *
 * Rules:
 *  1. Exact match always wins.
 *  2. If no exact match, the longest prefix match wins (only ONE item active).
 *  3. appRoute is only active on exact match (never on prefix).
 */
function isNavActive(itemRoute, path, appRoute) {
  if (itemRoute === path) return true;
  if (itemRoute.length <= 1) return false; // skip '/' root
  if (itemRoute === appRoute) return false; // appRoute is only active on exact match
  // Prefix match — but only return true if this is the LONGEST matching prefix
  // (handled by the caller which passes the bestMatch)
  return path.startsWith(itemRoute + '/');
}

/**
 * Find the single best-matching nav item for a path.
 * Returns the route of the item that should be active, or null.
 * "Best" = longest prefix match (exact match always wins).
 */
function findBestNavMatch(navItems, path, appRoute) {
  // 1. Check for exact match first
  for (const item of navItems) {
    if (item.route === path) return item.route;
  }
  // 2. Find the longest prefix match (exclude appRoute — it only matches exactly)
  let bestMatch = null;
  let bestLen = 0;
  for (const item of navItems) {
    if (item.route === appRoute) continue;
    if (item.route.length <= 1) continue;
    if (path.startsWith(item.route + '/') && item.route.length > bestLen) {
      bestMatch = item.route;
      bestLen = item.route.length;
    }
  }
  return bestMatch;
}

const routes = new Map();

// Routes that should render in "kiosk mode" — no sidebar, no shell chrome.
const KIOSK_ROUTES = new Set(['/pos', '/pos/sales', '/pos/quotes']);
const isKioskRoute = (path) => {
  if (KIOSK_ROUTES.has(path)) return true;
  if (path.startsWith('/pos/sale/')) return true;
  return false;
};

// Routes that render fullscreen WITHOUT the sidebar shell
const FULLSCREEN_ROUTES = new Set(['/launcher']);
const isFullscreenRoute = (path) => FULLSCREEN_ROUTES.has(path);

export function route(path, handler) {
  routes.set(path, handler);
}

export function navigate(path) {
  window.location.hash = '#' + path;
}

export function reload() {
  render();
}

export async function render() {
  const hash = window.location.hash.slice(1) || '/';
  const [path] = hash.split('?');
  const queryStr = hash.split('?')[1] || '';
  const query = Object.fromEntries(new URLSearchParams(queryStr));

  const app = $('#app');
  const kiosk = isKioskRoute(path);
  const fullscreen = isFullscreenRoute(path);

  // Find matching handler
  let handler = routes.get(path);
  if (!handler) {
    const prefixes = [...routes.keys()]
      .filter(k => k.endsWith('/') && path.startsWith(k))
      .sort((a, b) => b.length - a.length);
    if (prefixes.length) handler = routes.get(prefixes[0]);
  }

  // Build shell
  if (kiosk) {
    // Kiosk mode: no sidebar, no shell
    document.body.classList.add('kiosk-mode');
    app.innerHTML = `<div id="kiosk-root" class="kiosk-root"><div id="page" class="kiosk-page"></div></div>`;
  } else if (fullscreen) {
    // Fullscreen mode: no sidebar (launcher)
    document.body.classList.remove('kiosk-mode');
    app.innerHTML = `<div id="page"></div>`;
  } else {
    // Normal mode: use new shell if app is known, else old sidebar
    document.body.classList.remove('kiosk-mode');
    const appId = getAppForPath(path);
    if (appId && APP_CONFIGS[appId]) {
      const config = APP_CONFIGS[appId];
      // Determine breadcrumb from path.
      // First pass: exact match wins (no breadcrumb for the nav item itself).
      // Second pass: longest-prefix match wins (for sub-pages like /bills/123 → 'All Bills').
      let breadcrumb = '';
      const exactMatch = config.nav.find(n => path === n.route);
      if (!exactMatch) {
        const prefixMatches = config.nav
          .filter(n => n.route !== config.appRoute && path.startsWith(n.route + '/'))
          .sort((a, b) => b.route.length - a.route.length);
        if (prefixMatches.length) breadcrumb = prefixMatches[0].label;
      }
      app.innerHTML = renderShell(config, path, breadcrumb);
      // Bind shell events on next tick (pass config for mobile bottom nav)
      setTimeout(() => bindShellEvents(config), 0);
    } else {
      // Fallback to old sidebar for unknown routes
      const { renderShell: oldRenderShell } = await import('./components/sidebar.js');
      app.innerHTML = oldRenderShell(path);
    }
  }

  const page = $('#page');
  // Cleanup previous route
  if (typeof page._cleanup === 'function') {
    try { page._cleanup(); } catch (e) { console.warn('cleanup error', e); }
    page._cleanup = null;
  }
  try {
    if (handler) {
      await handler(page, path, query);
    } else {
      const { emptyState, esc } = await import('./utils.js');
      page.innerHTML = emptyState('Page not found', `The path "${path}" doesn't exist.`);
    }
  } catch (e) {
    const { esc } = await import('./utils.js');
    page.innerHTML = `<div class="empty-state"><div class="empty-state-icon">⚠️</div><h3>Error</h3><p>${esc(e.message)}</p></div>`;
    console.error(e);
  }

  // Update active nav (new shell or old sidebar)
  // v8.4: Use "longest prefix wins" so only ONE nav item is active at a time.
  // Previously, /stock/adjustments matched BOTH /stock (prefix) and
  // /stock/adjustments (exact), highlighting 2 items. Now we find the single
  // best match and only highlight that one.
  if (!kiosk && !fullscreen) {
    const appId = getAppForPath(path);
    const config = appId && APP_CONFIGS[appId] ? APP_CONFIGS[appId] : null;
    const appRoute = config ? config.appRoute : null;
    const navItems = config ? config.nav : [];
    const bestMatch = findBestNavMatch(navItems, path, appRoute);
    $$('.shell-sidebar-nav a, .sidebar nav a').forEach(a => {
      const href = a.getAttribute('href').slice(1);
      // Active only if: exact match OR (this is the bestMatch AND it's a prefix match)
      const isExact = href === path;
      const isBestPrefix = href === bestMatch && href !== path;
      a.classList.toggle('active', isExact || isBestPrefix);
    });
  }
}
