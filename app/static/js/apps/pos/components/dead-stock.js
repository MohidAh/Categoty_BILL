// Dead Stock page — extracted from inventory-pages.js (size fix)
import { route } from '../../../router.js';
import { api } from '../../../api.js';
import { $, esc, fmt, fmtRs, fmtDate, skeletonCards, errorBox, emptyState } from '../../../utils.js';

// Shared SVG icon set (must be defined locally — was missing, causing "SVG is not defined" P0 error)
const SVG = {
  box: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  trendDown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/></svg>',
};

// Helper: render a stat card with SVG icon chip
function statCard(label, value, chipClass, svgIcon, sub = '') {
  return `<div class="stat-card">
    <div class="stat-card-icon ${chipClass}">${svgIcon}</div>
    <div class="stat-card-label">${esc(label)}</div>
    <div class="stat-card-value">${value}</div>
    ${sub ? `<div class="stat-card-delta">${sub}</div>` : ''}
  </div>`;
}

route('/dead-stock', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-warning">${SVG.trendDown}</div>
      <div>
        <h2 class="pos-page-header-title">Dead Stock</h2>
        <p class="pos-page-header-sub">Slow-moving inventory that ties up capital. Consider discounts to clear it.</p>
      </div>
    </div>
    <div id="ds-stats" class="mb-4"></div>
    <div class="card">
      <div class="card-title"><h3>Clearance Suggestions</h3></div>
      <div id="ds-list">${skeletonCards(3)}</div>
    </div>`;

  await loadDeadStock();

  async function loadDeadStock() {
    try {
      const r = await api('/api/trends/dead-stock');
      const list = r.alerts || [];

      if (!list.length) {
        $('#ds-stats').innerHTML = '';
        $('#ds-list').innerHTML = `
          <div class="empty-state">
            <div class="empty-state-icon" style="background:var(--success-soft);color:var(--success-text)">${SVG.check}</div>
            <h3>No dead stock detected</h3>
            <p>None of your categories show stalled sales. Keep selling and the system will flag any slow movers automatically.</p>
          </div>`;
        return;
      }

      // v8.18.11 fix: this page previously read 8 fields the API never
      // returned (stock_value, stock, color, code, category_name, last_sold,
      // days_idle, suggestion) — every stat showed 0 and every column was
      // blank/'—' (same bug class as the monthly-close page). The API
      // (/api/trends/dead-stock -> trends.generate_dead_stock_alerts)
      // returns: item_name, last_purchased, days_since, total_qty,
      // tied_capital, avg_cost, supplier, suggested_discount, action.
      // The home dashboard already read this real contract correctly.
      const totalValue = list.reduce((s, a) => s + (a.tied_capital || 0), 0);
      const totalUnits = list.reduce((s, a) => s + (a.total_qty || 0), 0);
      $('#ds-stats').innerHTML = `
        <div class="grid grid-3">
          ${statCard('Dead Items', list.length, 'chip-danger', SVG.alert)}
          ${statCard('Tied-up Units', fmt(totalUnits), 'chip-warning', SVG.box)}
          ${statCard('Capital Locked', fmtRs(totalValue), 'chip-danger', SVG.trendDown)}
        </div>`;

      $('#ds-list').innerHTML = `
        <div class="table-wrap">
          <table>
            <thead><tr>
              <th>Item</th><th>Supplier</th>
              <th class="table-num">Qty</th><th class="table-num">Avg Cost</th>
              <th class="table-num">Last Purchased</th><th class="table-num">Days Since</th>
              <th class="table-num">Capital Locked</th>
              <th>Suggestion</th>
            </tr></thead>
            <tbody>${list.map(a => `<tr>
              <td class="font-semibold">${esc(a.item_name || 'Item')}</td>
              <td class="text-sm">${esc(a.supplier || '—')}</td>
              <td class="table-num font-semibold">${fmt(a.total_qty || 0)}</td>
              <td class="table-num">${fmtRs(a.avg_cost || 0)}</td>
              <td class="text-sm">${a.last_purchased ? fmtDate(a.last_purchased) : '—'}</td>
              <td class="table-num text-warning font-semibold">${fmt(a.days_since || 0)}</td>
              <td class="table-num">${fmtRs(a.tied_capital || 0)}</td>
              <td class="text-sm">${esc(a.suggested_discount || '')}${a.action ? ` — ${esc(a.action)}` : ''}</td>
            </tr>`).join('')}</tbody>
          </table>
        </div>`;
    } catch (e) {
      $('#ds-list').innerHTML = errorBox(e.message);
    }
  }
});
