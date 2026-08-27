// States System v3.1 — empty/loading/error/success states per U.5
// Import: import { emptyState, errorState, skeletonCards, skeletonRows } from '../core/states.js';

const SVG_ICONS = {
  inbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="20 6 9 17 4 12"/></svg>',
  package: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>',
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>',
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
};

// Default empty-state copy by context
const EMPTY_COPY = {
  bills: { title: 'No bills yet', hint: 'Upload your first supplier bill and AI will extract it.', action: 'Upload Bill' },
  sales: { title: 'No sales found', hint: 'Sales will appear here once you start using the POS.', action: '' },
  customers: { title: 'No customers yet', hint: 'Add customers to track loyalty and credit.', action: 'Add Customer' },
  suppliers: { title: 'No suppliers yet', hint: 'Add your first supplier to start tracking bills.', action: 'Add Supplier' },
  reports: { title: 'No data in this range', hint: 'Try a wider date range or check back later.', action: 'Reset filters' },
  insights: { title: 'No insights yet', hint: 'Confirm bills and make sales to generate insights.', action: '' },
  inventory: { title: 'No stock items', hint: 'Confirm bills with line items to see stock levels.', action: '' },
  search: { title: 'No matches found', hint: 'Check spelling or try fewer words.', action: '' },
  generic: { title: 'Nothing here yet', hint: 'Data will appear once available.', action: '' },
};

export function emptyState(context = 'generic', customTitle = '', customHint = '', actionLabel = '', actionFn = '') {
  const copy = EMPTY_COPY[context] || EMPTY_COPY.generic;
  const title = customTitle || copy.title;
  const hint = customHint || copy.hint;
  const action = actionLabel || copy.action;
  const icon = SVG_ICONS[context] || SVG_ICONS.inbox;
  return `<div class="empty-state">
    <div class="empty-state-icon">${icon}</div>
    <h3>${title}</h3>
    <p>${hint}</p>
    ${action ? `<button class="btn" onclick="${actionFn}">${action}</button>` : ''}
  </div>`;
}

export function errorState(message, retryFn = '') {
  return `<div class="empty-state">
    <div class="empty-state-icon" style="background:var(--danger-soft);color:var(--danger-text)">${SVG_ICONS.alert}</div>
    <h3>Something went wrong</h3>
    <p>${message}</p>
    ${retryFn ? `<button class="btn" onclick="${retryFn}">Retry</button>` : ''}
  </div>`;
}

export function offlineState(queueCount = 0) {
  return `<div class="empty-state">
    <div class="empty-state-icon" style="background:var(--warning-soft);color:var(--warning-text)">${SVG_ICONS.alert}</div>
    <h3>You're offline</h3>
    <p>Data may be stale. ${queueCount > 0 ? `${queueCount} item(s) queued for sync.` : 'No pending items.'}</p>
  </div>`;
}

export function skeletonCards(count = 3) {
  return `<div class="grid grid-${count}">${Array.from({length: count}).map(() =>
    `<div class="card"><div class="skeleton" style="height:18px;width:120px;margin-bottom:16px"></div>
     <div class="skeleton" style="height:14px;width:100%;margin-bottom:8px"></div>
     <div class="skeleton" style="height:14px;width:80%;margin-bottom:8px"></div>
     <div class="skeleton" style="height:14px;width:90%"></div></div>`
  ).join('')}</div>`;
}

export function skeletonRows(count = 5, cols = 6) {
  return `<table><tbody>${Array.from({length: count}).map(() =>
    `<tr>${Array.from({length: cols}).map(() =>
      `<td><div class="skeleton" style="height:14px;width:${60 + Math.random() * 40}%"></div></td>`
    ).join('')}</tr>`
  ).join('')}</tbody></table>`;
}

export function skeletonKpis(count = 4) {
  return `<div class="grid grid-${count}">${Array.from({length: count}).map(() =>
    `<div class="stat-card"><div class="skeleton" style="height:11px;width:60px;margin-bottom:8px"></div>
     <div class="skeleton" style="height:26px;width:80px;margin-bottom:6px"></div>
     <div class="skeleton" style="height:11px;width:90px"></div></div>`
  ).join('')}</div>`;
}

// Count-up animation for KPI values (U.6)
export function countUp(element, target, duration = 600) {
  if (!element || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    if (element) element.textContent = formatNumber(target);
    return;
  }
  const start = 0;
  const startTime = performance.now();
  element.classList.add('count-up-animating');
  function update(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
    const current = start + (target - start) * eased;
    element.textContent = formatNumber(current);
    if (progress < 1) {
      requestAnimationFrame(update);
    } else {
      element.textContent = formatNumber(target);
      element.classList.remove('count-up-animating');
    }
  }
  requestAnimationFrame(update);
}

function formatNumber(n) {
  if (n == null || isNaN(n)) return '—';
  return Number(n).toLocaleString('en-PK', { maximumFractionDigits: 0 });
}

// Stagger entrance for card grids (U.6)
export function applyStagger(selector, delay = 30) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  document.querySelectorAll(selector).forEach((el, i) => {
    el.classList.add('stagger-in');
    el.style.animationDelay = `${i * delay}ms`;
  });
}
