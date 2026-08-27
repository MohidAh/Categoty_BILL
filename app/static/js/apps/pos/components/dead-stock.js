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

      const totalValue = list.reduce((s, a) => s + (a.stock_value || 0), 0);
      const totalUnits = list.reduce((s, a) => s + (a.stock || 0), 0);
      $('#ds-stats').innerHTML = `
        <div class="grid grid-3">
          ${statCard('Dead Categories', list.length, 'chip-danger', SVG.alert)}
          ${statCard('Tied-up Units', fmt(totalUnits), 'chip-warning', SVG.box)}
          ${statCard('Capital Locked', fmtRs(totalValue), 'chip-danger', SVG.trendDown)}
        </div>`;

      $('#ds-list').innerHTML = `
        <div class="table-wrap">
          <table>
            <thead><tr>
              <th>Code</th><th>Category</th>
              <th class="table-num">Stock</th><th class="table-num">Last Sold</th>
              <th class="table-num">Days Idle</th><th class="table-num">Stock Value</th>
              <th>Suggestion</th>
            </tr></thead>
            <tbody>${list.map(a => `<tr>
              <td><span class="pos-cat-code" style="background:${esc(a.color || '#888')}">${esc(a.code || '—')}</span></td>
              <td class="font-semibold">${esc(a.category_name || 'Category')}</td>
              <td class="table-num font-semibold">${fmt(a.stock || 0)}</td>
              <td class="text-sm">${a.last_sold ? fmtDate(a.last_sold) : 'Never'}</td>
              <td class="table-num text-warning font-semibold">${fmt(a.days_idle || 0)}</td>
              <td class="table-num">${fmtRs(a.stock_value || 0)}</td>
              <td class="text-sm">${esc(a.suggestion || 'Run a clearance discount')}</td>
            </tr>`).join('')}</tbody>
          </table>
        </div>`;
    } catch (e) {
      $('#ds-list').innerHTML = errorBox(e.message);
    }
  }
});
