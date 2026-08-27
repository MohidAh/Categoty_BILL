// Sidebar + shell renderer
import { $, $$, icon, toggleTheme } from '../utils.js';

const NAV_ITEMS = [
  { path: '/', icon: 'dashboard', label: 'Dashboard', key: 'D' },
  { path: '/pos', icon: 'store', label: 'POS', key: 'P' },
  { path: '/bills', icon: 'bills', label: 'Bills', key: 'B' },
  { path: '/items', icon: 'search', label: 'Item Search', key: 'F' },
  { path: '/suppliers', icon: 'suppliers', label: 'Suppliers', key: 'S' },
  { path: '/reports', icon: 'reports', label: 'Reports', key: 'R' },
  { path: '/insights', icon: 'insights', label: 'Insights', key: 'I' },
  { path: '/settings', icon: 'settings', label: 'Settings', key: ',' },
];

export function renderShell(currentPath) {
  const html = `
    <aside class="sidebar">
      <div class="sidebar-brand">
        <div class="sidebar-brand-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 19V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 7h18"/><path d="M8 11h8M8 15h6"/></svg></div>
        <span>BillBook</span>
      </div>
      <div class="sidebar-section-label">Menu</div>
      <nav>
        ${NAV_ITEMS.map(n => `
          <a href="#${n.path}" class="${currentPath === n.path ? 'active' : ''}" title="${n.label} (${n.key})">
            <span class="nav-icon">${icon(n.icon, 16)}</span>
            <span>${n.label}</span>
            <kbd class="nav-key">${n.key}</kbd>
          </a>`).join('')}
      </nav>
      <button class="sidebar-cmdk" id="cmdk-trigger" title="Open command palette (Ctrl+K)">
        ${icon('search', 13)} <span>Search...</span> <kbd>⌘K</kbd>
      </button>
      <div class="sidebar-footer">
        <div class="sidebar-footer-row">
          <span class="sidebar-footer-text">v1.0</span>
          <div class="flex gap-2">
            <button class="btn btn-ghost btn-icon btn-sm" id="shortcuts-trigger" title="Keyboard shortcuts (?)">${icon('settings', 13) === '' ? '?' : '<span style="font-size:13px;font-weight:600">?</span>'}</button>
            <button class="btn btn-ghost btn-icon btn-sm" id="theme-toggle" title="Toggle theme (light/dark)">
              ${icon('sun', 13)}
            </button>
          </div>
        </div>
      </div>
    </aside>
    <main class="main">
      <div id="page"></div>
    </main>`;
  // Bind events on next tick (after innerHTML is set by caller)
  setTimeout(() => {
    const toggle = $('#theme-toggle');
    if (toggle) toggle.onclick = toggleTheme;
    const cmdkTrigger = $('#cmdk-trigger');
    if (cmdkTrigger) {
      cmdkTrigger.onclick = () => {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, ctrlKey: true }));
      };
    }
    const shortcutsTrigger = $('#shortcuts-trigger');
    if (shortcutsTrigger) {
      shortcutsTrigger.onclick = () => {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: '?', shiftKey: true }));
      };
    }
  }, 0);
  return html;
}
