// SnowUI App Launcher — fullscreen post-login home
// Route: #/launcher
// Shows app cards with live badges, keyboard shortcuts (1-7), resume pill, recent apps.

import { $, $$, esc, icon, toast, toggleTheme } from '../utils.js';
import { api, apiPost } from '../api.js';

// ─── SVG icon set for launcher cards ───
const CARD_ICONS = {
  pos: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l1-5h16l1 5"/><path d="M4 9v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V9"/><line x1="9" y1="13" x2="15" y2="13"/></svg>',
  billing: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>',
  inventory: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>',
  customers: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  reports: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  insights: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>',
  settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
};

// ─── App definitions ───
const APPS = [
  { id: 'pos', name: 'Point of Sale', desc: 'Process sales & checkout', icon: CARD_ICONS.pos, color: '#3B82F6', chipClass: 'chip-primary', route: '/pos', key: '1' },
  { id: 'billing', name: 'Billing', desc: 'Supplier bills & AI extraction', icon: CARD_ICONS.billing, color: '#10B981', chipClass: 'chip-success', route: '/bills', key: '2' },
  { id: 'inventory', name: 'Inventory', desc: 'Stock, adjustments & POs', icon: CARD_ICONS.inventory, color: '#F59E0B', chipClass: 'chip-warning', route: '/items', key: '3' },
  { id: 'customers', name: 'Customers', desc: 'Loyalty, credit & history', icon: CARD_ICONS.customers, color: '#06B6D4', chipClass: 'chip-info', route: '/customers', key: '4' },
  { id: 'reports', name: 'Reports', desc: 'P&L, cash flow & analytics', icon: CARD_ICONS.reports, color: '#8B5CF6', chipClass: 'chip-secondary', route: '/reports', key: '5' },
  { id: 'insights', name: 'AI Insights', desc: 'Trends, forecasts & ABC', icon: CARD_ICONS.insights, color: '#EC4899', chipClass: 'chip-pink', route: '/insights', key: '6' },
  { id: 'settings', name: 'Settings', desc: 'Categories, staff & backup', icon: CARD_ICONS.settings, color: '#64748B', chipClass: '', route: '/settings', key: '7' },
];

// Cashier role sees only POS + Customers
const CASHIER_APPS = ['pos', 'customers'];

// ─── Launcher state ───
let userRole = 'manager';
let userName = 'Manager';

export function renderLauncher(el, role = 'manager', name = 'Manager') {
  userRole = role;
  userName = name;

  // Determine visible apps
  const visibleApps = role === 'cashier'
    ? APPS.filter(a => CASHIER_APPS.includes(a.id))
    : APPS;

  // Get recent apps from localStorage
  const recentAppIds = JSON.parse(localStorage.getItem('bb-recent-apps') || '[]');
  const recentApps = recentAppIds.map(id => APPS.find(a => a.id === id)).filter(Boolean).slice(0, 3);

  // Get last app for resume pill
  const lastApp = localStorage.getItem('bb-last-app');
  const lastAppObj = lastApp ? APPS.find(a => a.id === lastApp) : null;

  // Time-based greeting
  const hour = new Date().getHours();
  let greeting = 'Good evening';
  if (hour < 12) greeting = 'Good morning';
  else if (hour < 17) greeting = 'Good afternoon';

  // Get user initial
  const initial = userName.charAt(0).toUpperCase();

  el.innerHTML = `
    <div class="launcher-root">
      <!-- Topbar -->
      <div class="launcher-topbar">
        <div class="launcher-topbar-left">
          <div class="launcher-logo">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M3 19V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
              <path d="M3 7h18"/>
              <path d="M8 11h8M8 15h6"/>
            </svg>
          </div>
          <span class="launcher-brand">BillBook</span>
        </div>
        <div class="launcher-topbar-center">
          <div class="launcher-search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="text" id="launcher-search-input" placeholder="Search apps, bills, suppliers..." autocomplete="off">
          </div>
        </div>
        <div class="launcher-topbar-right">
          <button class="launcher-icon-btn" id="launcher-theme-toggle" title="Toggle theme">
            ${icon('sun', 18)}
          </button>
          <div class="launcher-user-menu">
            <div class="launcher-user-avatar" id="launcher-user-avatar">${initial}</div>
            <div class="launcher-user-dropdown" id="launcher-user-dropdown">
              <div class="launcher-user-dropdown-header">
                <div class="name">${esc(userName)}</div>
                <div class="role">${esc(role)}</div>
              </div>
              <div class="launcher-user-dropdown-item" id="launcher-logout">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
                Logout
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Content -->
      <div class="launcher-content">
        <div class="launcher-greeting">
          <h1>${greeting}, ${esc(userName)}</h1>
          <p>${new Date().toLocaleDateString('en-PK', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}</p>
        </div>

        ${lastAppObj ? `
          <a href="#${lastAppObj.route}" class="launcher-resume" id="launcher-resume">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="5 4 15 12 5 20"/></svg>
            Resume ${esc(lastAppObj.name)}
          </a>
        ` : ''}

        ${recentApps.length ? `
          <div class="launcher-section-label">Recent</div>
          <div class="launcher-recent">
            ${recentApps.map(a => `
              <a href="#${a.route}" class="launcher-recent-item">
                <span style="color:${a.color}">${a.icon}</span>
                ${esc(a.name)}
              </a>`).join('')}
          </div>
        ` : ''}

        <div class="launcher-section-label">Applications</div>
        <div class="launcher-grid" id="launcher-grid">
          ${visibleApps.map(a => `
            <a href="#${a.route}" class="launcher-card" data-app-id="${a.id}" style="--card-color:${a.color}">
              <div class="launcher-card-icon ${a.chipClass}">${a.icon}</div>
              <div class="launcher-card-name">${esc(a.name)}</div>
              <div class="launcher-card-desc">${esc(a.desc)}</div>
              <div class="launcher-card-badge" id="badge-${a.id}" style="display:none"></div>
              <div class="launcher-card-kbd">${a.key}</div>
            </a>`).join('')}
        </div>
      </div>
    </div>`;

  // ─── Bind events ───
  bindLauncherEvents(el, visibleApps);

  // ─── Load badges asynchronously ───
  loadBadges(visibleApps);

  // ─── Track app clicks for recent apps ───
  $$('.launcher-card').forEach(card => {
    card.addEventListener('click', () => {
      const appId = card.dataset.appId;
      recordRecentApp(appId);
      localStorage.setItem('bb-last-app', appId);
    });
  });
}

// ─── Event bindings ───
function bindLauncherEvents(el, visibleApps) {
  // Theme toggle
  const themeBtn = $('#launcher-theme-toggle');
  if (themeBtn) {
    themeBtn.onclick = () => {
      const newTheme = toggleTheme();
      themeBtn.innerHTML = newTheme === 'dark' ? icon('sun', 18) : icon('moon', 18);
    };
    // Set correct icon based on current theme
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
    themeBtn.innerHTML = currentTheme === 'dark' ? icon('sun', 18) : icon('moon', 18);
  }

  // User menu dropdown
  const avatar = $('#launcher-user-avatar');
  const dropdown = $('#launcher-user-dropdown');
  if (avatar && dropdown) {
    avatar.onclick = (e) => {
      e.stopPropagation();
      dropdown.classList.toggle('open');
    };
    document.addEventListener('click', () => dropdown.classList.remove('open'));
  }

  // Logout
  const logoutBtn = $('#launcher-logout');
  if (logoutBtn) {
    logoutBtn.onclick = async () => {
      try {
        await apiPost('/api/logout', {});
      } catch (e) {}
      localStorage.removeItem('bb-last-app');
      localStorage.removeItem('bb-recent-apps');
      window.location.href = '/login';
    };
  }

  // Search input — redirect to old search/command palette
  const searchInput = $('#launcher-search-input');
  if (searchInput) {
    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && searchInput.value.trim()) {
        // Navigate to bills search with the query
        window.location.hash = '#/items?q=' + encodeURIComponent(searchInput.value.trim());
      }
    });
  }

  // Keyboard shortcuts 1-7
  const keyHandler = (e) => {
    const tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const num = parseInt(e.key);
    if (num >= 1 && num <= visibleApps.length) {
      e.preventDefault();
      const app = visibleApps[num - 1];
      recordRecentApp(app.id);
      localStorage.setItem('bb-last-app', app.id);
      window.location.hash = '#' + app.route;
    }
  };
  document.addEventListener('keydown', keyHandler);
  // Cleanup on next render
  el._cleanup = () => document.removeEventListener('keydown', keyHandler);
}

// ─── Load live badges ───
async function loadBadges(visibleApps) {
  const appIds = visibleApps.map(a => a.id);

  // Billing: count of status='review' bills
  if (appIds.includes('billing')) {
    try {
      const r = await api('/api/bills?status=review&limit=1');
      const count = r.length || 0;
      if (count > 0) showBadge('billing', `${count} review`, 'badge-warning');
    } catch (e) {}
  }

  // Inventory: low-stock count
  if (appIds.includes('inventory')) {
    try {
      const r = await api('/api/inventory');
      const lowCount = (r.items || []).filter(i => i.low_stock || i.out_of_stock).length;
      if (lowCount > 0) showBadge('inventory', `${lowCount} low`, 'badge-danger');
    } catch (e) {}
  }

  // POS: active shift
  if (appIds.includes('pos')) {
    try {
      const r = await api('/api/shifts/current');
      if (r.shift) showBadge('pos', 'Shift open', 'badge-success');
    } catch (e) {}
  }

  // Customers: overdue credit count
  if (appIds.includes('customers')) {
    try {
      const r = await api('/api/customers');
      const creditCount = (r.customers || []).filter(c => (c.total_credit || 0) > 0).length;
      if (creditCount > 0) showBadge('customers', `${creditCount} credit`, 'badge-warning');
    } catch (e) {}
  }
}

function showBadge(appId, text, badgeClass) {
  const badge = $(`#badge-${appId}`);
  if (badge) {
    badge.className = `launcher-card-badge ${badgeClass}`;
    badge.innerHTML = `<span class="launcher-card-badge-dot"></span>${esc(text)}`;
    badge.style.display = 'flex';
  }
}

// ─── Recent apps tracking ───
function recordRecentApp(appId) {
  let recent = JSON.parse(localStorage.getItem('bb-recent-apps') || '[]');
  // Remove if already in list
  recent = recent.filter(id => id !== appId);
  // Add to front
  recent.unshift(appId);
  // Keep last 3
  recent = recent.slice(0, 3);
  localStorage.setItem('bb-recent-apps', JSON.stringify(recent));
}
