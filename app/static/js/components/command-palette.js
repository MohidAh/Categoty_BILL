// Command palette — Cmd+K / Ctrl+K fuzzy search over nav, bills, suppliers
import { navigate } from '../router.js';
import { $, esc, fmtRs, fmtDate, icon } from '../utils.js';
import { api } from '../api.js';

let paletteEl = null;
let state = {
  query: '',
  results: [],
  selected: 0,
  loading: false,
};

const NAV_COMMANDS = [
  { type: 'nav', icon: 'dashboard', label: 'Dashboard', hint: 'Go to dashboard', path: '/', key: 'D' },
  { type: 'nav', icon: 'bills', label: 'Bills', hint: 'Go to bills list', path: '/bills', key: 'B' },
  { type: 'nav', icon: 'search', label: 'Item Search', hint: 'Search across all bill items', path: '/items', key: 'F' },
  { type: 'nav', icon: 'plus', label: 'New Bill', hint: 'Upload a new bill', path: '/bills/new', key: 'N' },
  { type: 'nav', icon: 'suppliers', label: 'Suppliers', hint: 'Go to suppliers', path: '/suppliers', key: 'S' },
  { type: 'nav', icon: 'reports', label: 'Reports', hint: 'View reports', path: '/reports', key: 'R' },
  { type: 'nav', icon: 'insights', label: 'Insights', hint: 'View AI insights', path: '/insights', key: 'I' },
  { type: 'nav', icon: 'settings', label: 'Settings', hint: 'Open settings', path: '/settings', key: ',' },
  // v8.18.13: extra sales + staff salary
  { type: 'nav', icon: 'store', label: 'Extra Sales', hint: 'Record non-POS sales (cartons, raddi/scrap)', path: '/bills/extra-sales', key: 'X' },
  { type: 'nav', icon: 'suppliers', label: 'Staff Salary', hint: 'Payroll, off-days, advances, payout', path: '/bills/salary', key: 'L' },
  { type: 'action', icon: 'download', label: 'Export Bills (Excel)', hint: 'Download .xlsx', action: () => location.href = '/api/export/bills.xlsx' },
  { type: 'action', icon: 'download', label: 'Export Insights (Excel)', hint: 'Download .xlsx', action: () => location.href = '/api/export/insights.xlsx' },
  { type: 'action', icon: 'download', label: 'Export Bills (CSV)', hint: 'Download .csv', action: () => location.href = '/api/export.csv' },
  { type: 'action', icon: 'download', label: 'Monthly Close PDF', hint: 'Download monthly report', action: () => {
    const now = new Date();
    location.href = `/api/reports/monthly-close.pdf?year=${now.getFullYear()}&month=${String(now.getMonth() + 1).padStart(2, '0')}`;
  }},
  { type: 'action', icon: 'backup', label: 'Create Backup', hint: 'Backup database now', action: async () => {
    const r = await api('/api/backup', { method: 'POST' });
    alert(`Backup created (${r.size_mb} MB)`);
  }},
  { type: 'action', icon: 'sun', label: 'Toggle Theme', hint: 'Switch dark/light', action: () => {
    const cur = document.documentElement.getAttribute('data-theme');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('bb-theme', next);
  }},
];

export function initCommandPalette() {
  // Global key listener
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      // Disable command palette in kiosk mode — cashiers shouldn't navigate away
      if (document.body.classList.contains('kiosk-mode')) return;
      e.preventDefault();
      openPalette();
    }
    if (e.key === 'Escape' && paletteEl) {
      closePalette();
    }
  });
}

function openPalette() {
  if (paletteEl) return;
  state = { query: '', results: NAV_COMMANDS.slice(0, 8), selected: 0, loading: false };

  const root = document.createElement('div');
  root.className = 'cmdk-overlay';
  root.innerHTML = `
    <div class="cmdk" onclick="event.stopPropagation()">
      <div class="cmdk-input-wrap">
        <span class="cmdk-icon">${icon('search', 16)}</span>
        <input class="cmdk-input" id="cmdk-query" placeholder="Search bills, suppliers, or run a command..." autofocus>
        <kbd class="cmdk-esc">ESC</kbd>
      </div>
      <div class="cmdk-results" id="cmdk-results"></div>
      <div class="cmdk-footer">
        <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
        <span><kbd>↵</kbd> select</span>
        <span><kbd>esc</kbd> close</span>
      </div>
    </div>`;
  root.addEventListener('click', closePalette);
  document.body.appendChild(root);
  paletteEl = root;

  renderResults();
  const input = $('#cmdk-query');
  input.focus();
  input.addEventListener('input', (e) => onQuery(e.target.value));
  input.addEventListener('keydown', onKeydown);
}

function closePalette() {
  if (paletteEl) {
    paletteEl.remove();
    paletteEl = null;
  }
}

function onQuery(q) {
  state.query = q;
  state.selected = 0;
  if (!q.trim()) {
    state.results = NAV_COMMANDS.slice(0, 8);
    renderResults();
    return;
  }

  // Filter nav commands
  const lower = q.toLowerCase();
  const navMatches = NAV_COMMANDS.filter(c =>
    c.label.toLowerCase().includes(lower) || c.hint.toLowerCase().includes(lower)
  );

  // Search backend (debounced inline)
  state.loading = true;
  renderResults();
  clearTimeout(window.__cmdkTimer);
  window.__cmdkTimer = setTimeout(async () => {
    try {
      const [bills, suppliers] = await Promise.all([
        api(`/api/bills?q=${encodeURIComponent(q)}&page=1&page_size=5`),
        api(`/api/suppliers?q=${encodeURIComponent(q)}`),
      ]);
      const billResults = (bills.bills || []).slice(0, 5).map(b => ({
        type: 'bill',
        icon: 'bills',
        label: `Bill #${b.id} — ${b.supplier_name || 'Unknown'}`,
        hint: `${fmtRs(b.written_total || b.computed_total)} · ${fmtDate(b.bill_date)} · ${b.status}`,
        path: `/bills/${b.id}`,
      }));
      const supResults = (suppliers || []).slice(0, 3).map(s => ({
        type: 'supplier',
        icon: 'suppliers',
        label: s.name,
        hint: s.phone || 'No phone',
        path: `/suppliers/${s.id}`,
      }));
      state.results = [...navMatches, ...billResults, ...supResults];
    } catch (e) {
      state.results = navMatches;
    }
    state.loading = false;
    renderResults();
  }, 200);
}

function renderResults() {
  const el = $('#cmdk-results');
  if (!el) return;
  if (!state.results.length) {
    el.innerHTML = `<div class="cmdk-empty">${state.loading ? 'Searching...' : 'No results found'}</div>`;
    return;
  }
  el.innerHTML = state.results.map((r, i) => `
    <div class="cmdk-item ${i === state.selected ? 'selected' : ''}" data-idx="${i}">
      <span class="cmdk-item-icon">${icon(r.icon, 15)}</span>
      <div class="cmdk-item-text">
        <div class="cmdk-item-label">${esc(r.label)}</div>
        <div class="cmdk-item-hint">${esc(r.hint)}</div>
      </div>
      <span class="cmdk-item-type">${r.type}</span>
    </div>
  `).join('');
  // Click handlers
  el.querySelectorAll('.cmdk-item').forEach(item => {
    item.addEventListener('click', () => {
      state.selected = parseInt(item.dataset.idx);
      selectCurrent();
    });
    item.addEventListener('mouseenter', () => {
      state.selected = parseInt(item.dataset.idx);
      renderResults();
    });
  });
}

function onKeydown(e) {
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    state.selected = Math.min(state.selected + 1, state.results.length - 1);
    renderResults();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    state.selected = Math.max(state.selected - 1, 0);
    renderResults();
  } else if (e.key === 'Enter') {
    e.preventDefault();
    selectCurrent();
  }
}

function selectCurrent() {
  const r = state.results[state.selected];
  if (!r) return;
  closePalette();
  if (r.action) r.action();
  else if (r.path) navigate(r.path);
}
