// SnowUI App Shell — per-app topbar + sidebar + content area
// Renders the shell around app content, with app-specific nav config.

import { $, $$, esc, icon, toggleTheme } from '../utils.js';

// SVG icon set for shell
const SHELL_ICONS = {
  back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
  home: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>',
};

/**
 * Render the app shell.
 * @param {Object} config - Shell configuration
 * @param {string} config.appId - e.g. 'pos', 'billing'
 * @param {string} config.appName - e.g. 'Point of Sale'
 * @param {string} config.appIcon - SVG string for app icon
 * @param {string} config.appColor - hex color for icon chip
 * @param {string} config.chipClass - CSS class for icon chip (e.g. 'chip-primary')
 * @param {Array} config.nav - Array of {icon, label, route, badge, key}
 * @param {string} currentPath - Current route path for active nav
 * @param {string} breadcrumb - e.g. 'New Sale' or 'Sales History'
 * @returns {string} HTML string for the shell
 */
export function renderShell(config, currentPath, breadcrumb = '') {
  // v8.4: Find the single best-matching nav item (longest prefix wins)
  // so only ONE item is active at a time. Previously /stock/adjustments
  // would highlight both /stock and /stock/adjustments.
  const _findBestMatch = (nav, path) => {
    // Exact match first
    for (const item of nav) {
      if (item.route === path) return item.route;
    }
    // Longest prefix match (exclude appRoute)
    let best = null, bestLen = 0;
    for (const item of nav) {
      if (item.route === config.appRoute) continue;
      if (item.route.length <= 1) continue;
      if (path.startsWith(item.route + '/') && item.route.length > bestLen) {
        best = item.route;
        bestLen = item.route.length;
      }
    }
    return best;
  };
  const bestMatch = _findBestMatch(config.nav || [], currentPath);

  const navItems = (config.nav || []).map(item => {
    // Active only if: exact match OR this is the best prefix match
    const isActive = currentPath === item.route || item.route === bestMatch;
    const badgeHtml = item.badge
      ? `<span class="nav-badge">${esc(item.badge)}</span>`
      : (item.badgeSlot ? `<span class="nav-badge" id="${esc(item.badgeSlot)}" style="display:none">0</span>` : '');
    return `
      <a href="#${item.route}" class="${isActive ? 'active' : ''}" title="${esc(item.label)}">
        <span class="nav-icon">${item.icon}</span>
        <span class="nav-label">${esc(item.label)}</span>
        ${badgeHtml}
        ${item.key ? `<kbd class="nav-kbd">${esc(item.key)}</kbd>` : ''}
      </a>`;
  }).join('');

  return `
    <div class="shell-root">
      <!-- Sidebar -->
      <aside class="shell-sidebar">
        <div class="shell-sidebar-brand">
          <div class="shell-sidebar-brand-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M3 19V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
              <path d="M3 7h18"/>
              <path d="M8 11h8M8 15h6"/>
            </svg>
          </div>
          <span class="shell-sidebar-brand-name">BillBook</span>
        </div>
        <div class="shell-sidebar-section">
          <div class="shell-sidebar-section-label">${esc(config.appName)}</div>
        </div>
        <nav class="shell-sidebar-nav">
          ${navItems}
        </nav>
        <div class="shell-sidebar-footer">
          <a href="#/launcher" class="shell-sidebar-back">
            ${SHELL_ICONS.back}
            <span>All Apps</span>
          </a>
        </div>
      </aside>

      <!-- Main -->
      <div class="shell-main">
        <!-- Topbar -->
        <div class="shell-topbar">
          <div class="shell-topbar-left">
            <a href="#/launcher" class="shell-topbar-back" title="Back to apps">
              ${SHELL_ICONS.back}
            </a>
            <div class="shell-topbar-app-icon ${config.chipClass || 'chip-primary'}">
              ${config.appIcon || ''}
            </div>
            <div class="shell-topbar-title">
              <h1>${esc(config.appName)}</h1>
              ${breadcrumb ? `<div class="breadcrumb">${esc(breadcrumb)}</div>` : ''}
            </div>
          </div>
          <div class="shell-topbar-center">
            <div class="shell-topbar-search">
              ${SHELL_ICONS.search}
              <input type="text" id="shell-search" placeholder="Search..." autocomplete="off">
            </div>
          </div>
          <div class="shell-topbar-right">
            <div id="shell-shift-indicator"></div>
            <div id="shell-offline-indicator"></div>
            <button class="launcher-icon-btn" id="shell-theme-toggle" title="Toggle theme">
              ${SHELL_ICONS.sun}
            </button>
          </div>
        </div>

        <!-- Content -->
        <div class="shell-content" id="page"></div>
      </div>

      <!-- Mobile bottom nav (SnowUI Free Operation: navigation accessible on all devices) -->
      <nav class="shell-bottom-nav" id="shell-bottom-nav"></nav>
    </div>`;
}

/**
 * Bind shell events (theme toggle, search, shift indicator).
 * Called after innerHTML is set.
 */
export function bindShellEvents(config) {
  // Theme toggle
  const themeBtn = $('#shell-theme-toggle');
  if (themeBtn) {
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
    themeBtn.innerHTML = currentTheme === 'dark' ? SHELL_ICONS.sun : SHELL_ICONS.moon;
    themeBtn.onclick = () => {
      const newTheme = toggleTheme();
      themeBtn.innerHTML = newTheme === 'dark' ? SHELL_ICONS.sun : SHELL_ICONS.moon;
      // Re-render charts so colors adapt (dispatch event chart pages listen for)
      window.dispatchEvent(new CustomEvent('theme-changed', { detail: { theme: newTheme } }));
    };
  }

  // Search → trigger command palette
  const search = $('#shell-search');
  if (search) {
    search.onfocus = () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, ctrlKey: true }));
      search.blur();
    };
  }

  // Mobile bottom nav: populate with current app's nav (first 5 items)
  const bottomNav = $('#shell-bottom-nav');
  if (bottomNav && config && config.nav) {
    const hash = window.location.hash.slice(1) || '/';
    const path = hash.split('?')[0];
    const top5 = config.nav.slice(0, 5);
    bottomNav.innerHTML = top5.map(item => {
      const isActive = path === item.route || (path.startsWith(item.route + '/') && item.route !== config.appRoute);
      return `<a href="#${item.route}" class="shell-bottom-nav-item ${isActive ? 'active' : ''}" title="${esc(item.label)}">
        <span class="shell-bottom-nav-icon">${item.icon}</span>
        <span class="shell-bottom-nav-label">${esc(item.label)}</span>
      </a>`;
    }).join('');
  }

  // Shift indicator
  loadShiftIndicator();

  // Offline indicator
  updateOfflineIndicator();
  window.addEventListener('online', updateOfflineIndicator);
  window.addEventListener('offline', updateOfflineIndicator);
}

async function loadShiftIndicator() {
  const el = $('#shell-shift-indicator');
  if (!el) return;
  try {
    const { api } = await import('../api.js');
    const r = await api('/api/shifts/current');
    if (r.shift) {
      el.innerHTML = `<div class="shell-shift-pill"><span class="shell-shift-pill-dot"></span>Shift open</div>`;
    }
  } catch (e) {}
}

function updateOfflineIndicator() {
  const el = $('#shell-offline-indicator');
  if (!el) return;
  if (!navigator.onLine) {
    el.innerHTML = `<div class="shell-offline-badge">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      Offline
    </div>`;
  } else {
    el.innerHTML = '';
  }
}

// ─── App configurations (used by router to know which shell to render) ───
export const APP_CONFIGS = {
  pos: {
    appId: 'pos', appName: 'Point of Sale', appRoute: '/pos',
    chipClass: 'chip-primary',
    appIcon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l1-5h16l1 5"/><path d="M4 9v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V9"/><line x1="9" y1="13" x2="15" y2="13"/></svg>',
    nav: [
      { label: 'New Sale', route: '/pos', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>', key: '1' },
      { label: 'Sales History', route: '/pos/sales', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>', key: '2' },
      { label: 'Quotations', route: '/pos/quotes', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>', key: '3' },
      { label: 'Returns', route: '/pos/returns', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>', key: '4' },
      { label: 'Shifts', route: '/pos/shifts', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>', key: '5' },
      { label: 'Cash Drawer', route: '/pos/cash-drawer', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="6" width="20" height="12" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/></svg>', key: '6' },
      { label: 'Z-Report', route: '/pos/z-report', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>', key: '7' },
      { label: 'Barcodes', route: '/barcodes', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="4" y1="4" x2="4" y2="20"/><line x1="8" y1="4" x2="8" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/><line x1="16" y1="4" x2="16" y2="20"/><line x1="20" y1="4" x2="20" y2="20"/></svg>', key: '8' },
    ],
  },
  billing: {
    appId: 'billing', appName: 'Billing', appRoute: '/bills',
    chipClass: 'chip-success',
    appIcon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
    nav: [
      { label: 'All Bills', route: '/bills', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/></svg>', key: '1' },
      { label: 'Upload', route: '/bills/new', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>', key: '2' },
      { label: 'Review Queue', route: '/bills/review', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>', key: '3' },
      { label: 'Suppliers', route: '/suppliers', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l1-5h16l1 5"/><path d="M4 9v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V9"/></svg>', key: '4' },
      { label: 'Payments', route: '/bills/payments', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/></svg>', key: '5' },
      { label: 'Expenses', route: '/bills/expenses', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>', key: 'E' },
      { label: 'Cash Buckets', route: '/bills/cash-buckets', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>', key: 'B' },
    ],
  },
  inventory: {
    appId: 'inventory', appName: 'Inventory', appRoute: '/items',
    chipClass: 'chip-warning',
    appIcon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>',
    nav: [
      { label: 'Stock Overview', route: '/items', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>', key: '1' },
      { label: 'Stock Levels', route: '/stock', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 7l-8-4-8 4 8 4 8-4z"/><path d="M4 7v10l8 4 8-4V7"/><line x1="12" y1="11" x2="12" y2="21"/></svg>', key: '2' },
      { label: 'Adjustments', route: '/stock/adjustments', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>', key: '3' },
      { label: 'Purchase Orders', route: '/purchase-orders', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/></svg>', key: '4' },
      { label: 'Reorder', route: '/reorder', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><polyline points="21 3 21 8 16 8"/></svg>', key: '5' },
      { label: 'Dead Stock', route: '/dead-stock', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>', key: '6' },
      { label: 'Transfer Out', route: '/transfers/out', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>', key: '7' },
      { label: 'Transfer In', route: '/transfers/in', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>', key: '8' },
      { label: 'Central Buys', route: '/central-purchases', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-4"/></svg>', key: '9' },
      { label: 'POS Import', route: '/pos-import', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>', key: 'I' },
      { label: 'Custom Items', route: '/inventory/custom-items', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>', key: 'C' },
    ],
  },
  reports: {
    appId: 'reports', appName: 'Reports', appRoute: '/reports',
    chipClass: 'chip-secondary',
    appIcon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
    nav: [
      { label: 'Store Profit', route: '/reports/store-profit', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>', key: '1' },
      { label: 'Actual Earnings', route: '/reports/earnings', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>', key: 'X' },
      { label: 'Overview', route: '/reports/overview', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>', key: 'Q' },
      { label: 'Billwise', route: '/reports/billwise', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>', key: '2' },
      { label: 'P&L Statement', route: '/reports/pnl', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>', key: '3' },
      { label: 'Cash Flow', route: '/reports/cash-flow', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>', key: '4' },
      { label: 'Balance Sheet', route: '/reports/balance-sheet', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M7 14l4-4 4 4 5-5"/></svg>', key: '5' },
      { label: 'Top Items', route: '/reports/top-items', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>', key: '6' },
      { label: 'Profit Analysis', route: '/reports/profit-analysis', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>', key: 'P' },
      { label: 'Sold Stock', route: '/reports/sold-stock', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="7.5 4.21 12 6.81 16.5 4.21"/></svg>', key: 'S' },
      { label: 'Peak Hours', route: '/reports/peak-hours', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>', key: '7' },
      { label: 'Sales Targets', route: '/reports/targets', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>', key: '8' },
      { label: 'Monthly Close', route: '/reports/monthly-close', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><polyline points="9 16 11 18 15 14"/></svg>', key: '9' },
      { label: 'Export Center', route: '/reports/export', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>', key: '0' },
      { label: 'Suspicious', route: '/reports/suspicious', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>', key: 'A' },
      { label: 'AI Auditor', route: '/reports/audit', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>', key: 'T' },
      { label: 'Margins', route: '/reports/margins', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>', key: 'M' },
      { label: 'Monthly Profit', route: '/reports/monthly-profit', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><polyline points="7 16 10 19 14 14"/></svg>', key: 'P' },
      { label: 'YTD Profit', route: '/reports/ytd', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>', key: 'Y' },
      { label: 'Daily Stock', route: '/reports/daily-stock', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>', key: 'D' },
    ],
  },
  insights: {
    appId: 'insights', appName: 'AI Insights', appRoute: '/insights/agent',
    chipClass: 'chip-pink',
    appIcon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>',
    nav: [
      { label: 'Dashboard', route: '/', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>', key: '1' },
      { label: 'AI Assistant', route: '/insights/agent', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"/></svg>', key: '2' },
      { label: 'ABC Analysis', route: '/insights/abc', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>', key: '3' },
      { label: 'Trends', route: '/insights/trends', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>', key: '4' },
      { label: 'Forecast', route: '/insights/forecast', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>', key: '5' },
      { label: 'Market Intel', route: '/insights/market-intel', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>', key: '6' },
      { label: 'Approval Queue', route: '/insights/approval-queue', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8" y2="16"/><line x1="16" y1="16" x2="16" y2="16"/></svg>', key: '7', badgeSlot: 'approval-badge' },
      { label: 'AI Usage', route: '/insights/ai-usage', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>', key: '8' },
      { label: 'HQ Branches', route: '/insights/hq-branches', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>', key: 'h' },
      { label: 'Owner Hub', route: '/insights/owner-hub', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-4"/></svg>', key: 'o' },
      { label: 'Price Push', route: '/insights/price-push', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>', key: 'p' },
    ],
  },
  settings: {
    appId: 'settings', appName: 'Settings', appRoute: '/settings',
    chipClass: '',
    appIcon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    nav: [
      { label: 'General', route: '/settings', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/></svg>', key: '1' },
      { label: 'Employees', route: '/settings/employees', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>', key: '2' },
      { label: 'Tax & SMS', route: '/settings/tax-sms', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>', key: '3' },
      { label: 'Backups', route: '/settings/backups', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>', key: '4' },
      { label: 'Security', route: '/settings/security', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>', key: '5' },
      { label: 'Appearance', route: '/settings/appearance', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>', key: '6' },
      { label: 'AI Automations', route: '/settings/ai-automations', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"/></svg>', key: 'a' },
      { label: 'Branch', route: '/settings/branch', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>', key: 'b' },
      { label: 'Help & Guide', route: '/help', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>', key: '?' },
    ],
  },
  customers: {
    appId: 'customers', appName: 'Customers', appRoute: '/customers',
    chipClass: 'chip-info',
    appIcon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    nav: [
      { label: 'All Customers', route: '/customers', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>', key: '1' },
      { label: 'Credit Outstanding', route: '/customers/credit', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>', key: '2' },
      { label: 'Loyalty Tiers', route: '/customers/loyalty', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>', key: '3' },
      { label: 'Import', route: '/customers/import', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>', key: '4' },
    ],
  },
};

/**
 * Determine which app a path belongs to.
 */
export function getAppForPath(path) {
  // POS non-kiosk pages (returns, shifts, cash-drawer, z-report, barcodes)
  if (path.startsWith('/pos/returns') || path.startsWith('/pos/shifts') ||
      path.startsWith('/pos/cash-drawer') || path.startsWith('/pos/z-report') ||
      path === '/barcodes') {
    return 'pos';
  }
  if (path.startsWith('/pos')) return null; // Kiosk routes — no shell
  // Inventory app: items, stock, adjustments, POs, reorder, dead-stock, transfers, central
  if (path.startsWith('/items') || path.startsWith('/stock') ||
      path.startsWith('/purchase-orders') || path.startsWith('/reorder') ||
      path.startsWith('/dead-stock') || path.startsWith('/transfers') ||
      path.startsWith('/central-purchases') || path.startsWith('/pos-import') ||
      path.startsWith('/inventory')) {
    return 'inventory';
  }
  // Customers app
  if (path.startsWith('/customers')) return 'customers';
  if (path.startsWith('/bills') || path.startsWith('/suppliers')) return 'billing';
  if (path.startsWith('/reports') || path.startsWith('/more') ||
      path === '/import' || path === '/pos-import') return 'reports';
  if (path.startsWith('/insights')) return 'insights';
  if (path.startsWith('/settings')) return 'settings';
  if (path === '/' || path === '/dashboard') return 'insights';
  return null;
}

/**
 * Render the command palette (Ctrl+K) overlay.
 */
export function renderCommandPalette() {
  const root = $('#modal-root') || document.body;
  const existing = document.querySelector('.cmdk-overlay');
  if (existing) existing.remove();

  const overlay = document.createElement('div');
  overlay.className = 'cmdk-overlay';
  overlay.innerHTML = `
    <div class="cmdk-modal" onclick="event.stopPropagation()">
      <div class="cmdk-input-wrap">
        ${SHELL_ICONS.search}
        <input class="cmdk-input" id="cmdk-input" placeholder="Search apps, pages, bills..." autocomplete="off" autofocus>
        <kbd class="cmdk-input-kbd">ESC</kbd>
      </div>
      <div class="cmdk-results" id="cmdk-results">
        <div class="cmdk-empty">Start typing to search...</div>
      </div>
    </div>`;

  overlay.addEventListener('click', () => overlay.remove());
  root.appendChild(overlay);

  // Focus input
  setTimeout(() => {
    const input = $('#cmdk-input');
    if (input) input.focus();
  }, 50);

  // ESC closes
  const escHandler = (e) => {
    if (e.key === 'Escape') {
      overlay.remove();
      document.removeEventListener('keydown', escHandler);
    }
  };
  document.addEventListener('keydown', escHandler);

  // Build search index
  const searchIndex = buildSearchIndex();

  // Input handler
  const input = $('#cmdk-input');
  if (input) {
    let selectedIdx = 0;
    input.oninput = () => {
      const q = input.value.toLowerCase().trim();
      if (!q) {
        $('#cmdk-results').innerHTML = '<div class="cmdk-empty">Start typing to search...</div>';
        return;
      }
      const matches = searchIndex.filter(item =>
        item.title.toLowerCase().includes(q) || (item.sub || '').toLowerCase().includes(q)
      ).slice(0, 12);

      if (!matches.length) {
        $('#cmdk-results').innerHTML = '<div class="cmdk-empty">No results found</div>';
        return;
      }

      selectedIdx = 0;
      $('#cmdk-results').innerHTML = renderResults(matches, selectedIdx);

      $$('.cmdk-item').forEach((el, i) => {
        el.onclick = async () => {
          const match = matches[i];
          if (match.isAction) {
            overlay.remove();
            await executeAction(match.route);
          } else {
            window.location.hash = '#' + match.route;
            overlay.remove();
          }
        };
      });
    };

    input.onkeydown = (e) => {
      const items = $$('.cmdk-item');
      if (!items.length) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        selectedIdx = Math.min(selectedIdx + 1, items.length - 1);
        updateSelected(items, selectedIdx);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        selectedIdx = Math.max(selectedIdx - 1, 0);
        updateSelected(items, selectedIdx);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        items[selectedIdx]?.click();
      }
    };
  }
}

async function executeAction(actionKey) {
  const { apiPost } = await import('../api.js');
  const { toast, toggleTheme } = await import('../utils.js');
  switch (actionKey) {
    case '/pos':
      window.location.hash = '#/pos';
      break;
    case '__action_backup':
      try {
        toast('Creating backup...', 'info');
        const r = await apiPost('/api/backup', {});
        toast(`Backup created (${r.size_mb} MB)`, 'success');
      } catch (e) { toast('Backup failed: ' + e.message, 'error'); }
      break;
    case '__action_expense':
      window.location.hash = '#/bills/expenses';
      break;
    case '__action_theme':
      toggleTheme();
      break;
    case '__action_export':
      location.href = '/api/export/bills.xlsx';
      break;
    case '__action_shift':
      try {
        const r = await apiPost('/api/shifts/start?employee_id=1&opening_cash=5000', {});
        toast('Shift started', 'success');
      } catch (e) { toast('Shift start failed: ' + e.message, 'error'); }
      break;
  }
}

function buildSearchIndex() {
  const index = [];
  // Apps
  for (const [id, config] of Object.entries(APP_CONFIGS)) {
    index.push({ title: config.appName, sub: 'Application', route: config.appRoute, icon: config.appIcon, chipClass: config.chipClass });
    for (const nav of config.nav) {
      index.push({ title: nav.label, sub: config.appName, route: nav.route, icon: nav.icon, chipClass: config.chipClass });
    }
  }
  // v3.1.1: Action items (not navigation — they execute handlers)
  index.push({ title: 'New Sale', sub: 'Action', route: '/pos', icon: SHELL_ICONS.plus, chipClass: 'chip-primary', isAction: true });
  index.push({ title: 'Backup Now', sub: 'Action', route: '__action_backup', icon: SHELL_ICONS.home, chipClass: 'chip-success', isAction: true });
  index.push({ title: 'Add Expense', sub: 'Action', route: '__action_expense', icon: SHELL_ICONS.home, chipClass: 'chip-warning', isAction: true });
  index.push({ title: 'Toggle Theme', sub: 'Action', route: '__action_theme', icon: SHELL_ICONS.sun, chipClass: 'chip-secondary', isAction: true });
  index.push({ title: 'Export Bills', sub: 'Action', route: '__action_export', icon: SHELL_ICONS.home, chipClass: 'chip-info', isAction: true });
  index.push({ title: 'Start Shift', sub: 'Action', route: '__action_shift', icon: SHELL_ICONS.home, chipClass: 'chip-success', isAction: true });
  return index;
}

function renderResults(matches, selectedIdx) {
  return matches.map((m, i) => `
    <div class="cmdk-item ${i === selectedIdx ? 'selected' : ''}">
      <div class="cmdk-item-icon ${m.chipClass || 'chip-primary'}">${m.icon || ''}</div>
      <div class="cmdk-item-text">
        <div class="cmdk-item-title">${esc(m.title)}</div>
        ${m.sub ? `<div class="cmdk-item-sub">${esc(m.sub)}</div>` : ''}
      </div>
    </div>`).join('');
}

function updateSelected(items, idx) {
  items.forEach((el, i) => el.classList.toggle('selected', i === idx));
  items[idx]?.scrollIntoView({ block: 'nearest' });
}
