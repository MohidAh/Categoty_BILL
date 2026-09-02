// v5.0 Phase 6 — Daily Stock Report page (Reports app)
import { route } from '../router.js';
import { api } from '../api.js';
import { $, esc, fmt, fmtRs, fmtDate, toast, skeletonCards, errorBox } from '../utils.js';
import { initListState } from '../list-state.js';

const SVG = {
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  box: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>',
};

route('/reports/daily-stock', async (el, path, q) => {
  // v8.18.5: date persists across navigation
  const st = initListState('dailyStock', q, { date: '' });
  st.syncUrlIfRestored();
  const today = st.val('date') || new Date().toISOString().slice(0, 10);
  st.replace({ date: today });
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.box}</div>
      <div>
        <h2 class="pos-page-header-title">Daily Stock Report</h2>
        <p class="pos-page-header-sub">Per-category daily movement: opening + purchased - sold = closing, with COGS and GP.</p>
      </div>
      <div class="pos-page-header-actions">
        <div class="pos-date-input">
          <span class="pos-date-input-icon">${SVG.calendar}</span>
          <input class="input input-sm" id="ds-date" type="date" value="${today}">
        </div>
        <button class="btn btn-secondary btn-sm" id="ds-export-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
          Export CSV
        </button>
      </div>
    </div>
    <div id="ds-out">${skeletonCards(2)}</div>`;

  $('#ds-date').onchange = () => { st.replace({ date: $('#ds-date').value }); loadReport(); };
  $('#ds-export-btn').onclick = () => {
    const date = $('#ds-date').value;
    // v8.18.12: format=csv — the universal export route previously only
    // spoke pdf/excel, so this button silently downloaded an .xlsx file.
    window.location.href = `/api/reports/daily-stock/export?format=csv&date=${date}`;
  };
  await loadReport();

  async function loadReport() {
    const date = $('#ds-date').value;
    try {
      const r = await api(`/api/reports/daily-stock?date=${date}`);
      const rows = r.rows || [];
      const t = r.totals || {};
      if (rows.length === 0) {
        $('#ds-out').innerHTML = `
          <div class="card text-center" style="padding:48px">
            <p style="font-weight:600;margin-bottom:8px">No stock activity on ${esc(date)}</p>
            <p class="text-dim text-sm">Pick a date with purchases or sales to see the daily report.</p>
          </div>`;
        return;
      }
      $('#ds-out').innerHTML = `
        <div class="card">
          <div style="overflow-x:auto">
          <table class="table">
            <thead><tr>
              <th>Category</th>
              <th style="text-align:right">Opening</th>
              <th style="text-align:right">Purchased</th>
              <th style="text-align:right">Sold</th>
              <th style="text-align:right">Closing</th>
              <th style="text-align:right">Avg Cost</th>
              <th style="text-align:right">Stock Value</th>
              <th style="text-align:right">Sales Value</th>
              <th style="text-align:right">COGS</th>
              <th style="text-align:right">Gross Profit</th>
            </tr></thead>
            <tbody>
              ${rows.map(row => `<tr>
                <td><strong>${esc(row.code)}</strong> ${esc(row.category)}</td>
                <td style="text-align:right">${fmt(row.opening_qty)}</td>
                <td style="text-align:right;text-success">${row.purchased_qty > 0 ? '+' + fmt(row.purchased_qty) : '—'}</td>
                <td style="text-align:right;text-danger">${row.sold_qty > 0 ? '-' + fmt(row.sold_qty) : '—'}</td>
                <td style="text-align:right;font-weight:600">${fmt(row.closing_qty)}</td>
                <td style="text-align:right">${fmtRs(row.average_cost)}</td>
                <td style="text-align:right">${fmtRs(row.stock_value)}</td>
                <td style="text-align:right">${fmtRs(row.sales_value)}</td>
                <td style="text-align:right">${fmtRs(row.cogs)}</td>
                <td style="text-align:right;font-weight:600;color:var(--success-text, #16a34a)">${fmtRs(row.gross_profit)}</td>
              </tr>`).join('')}
            </tbody>
            <tfoot><tr style="font-weight:700;background:var(--bg-2, #f3f4f6)">
              <td>TOTALS</td>
              <td style="text-align:right">${fmt(t.opening_qty)}</td>
              <td style="text-align:right">${fmt(t.purchased_qty)}</td>
              <td style="text-align:right">${fmt(t.sold_qty)}</td>
              <td style="text-align:right">${fmt(t.closing_qty)}</td>
              <td style="text-align:right">—</td>
              <td style="text-align:right">${fmtRs(t.stock_value)}</td>
              <td style="text-align:right">${fmtRs(t.sales_value)}</td>
              <td style="text-align:right">${fmtRs(t.cogs)}</td>
              <td style="text-align:right;color:var(--success-text, #16a34a)">${fmtRs(t.gross_profit)}</td>
            </tr></tfoot>
          </table>
          </div>
        </div>`;
    } catch (e) {
      $('#ds-out').innerHTML = errorBox(e.message);
    }
  }
});
