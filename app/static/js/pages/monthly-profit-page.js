// v5.0 Phase 4 — Monthly Profit page (Reports app)
// COGS bridge: Opening + Purchases - Closing = COGS, then GP, Op Exp, Net Profit waterfall.
import { route } from '../router.js';
import { api } from '../api.js';
import { $, esc, fmtRs, fmtPct, toast, skeletonCards, errorBox } from '../utils.js';

const SVG = {
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
};

function bridgeBar(label, value, maxAbs, type) {
  const pct = maxAbs > 0 ? Math.min(100, Math.abs(value) / maxAbs * 100) : 0;
  const color = type === 'positive' ? 'var(--success, #16a34a)'
              : type === 'negative' ? 'var(--danger, #dc2626)'
              : 'var(--info, #3b82f6)';
  const sign = type === 'negative' ? '−' : '';
  return `<div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">
    <div style="width:160px;text-align:right;font-weight:600;font-size:14px">${esc(label)}</div>
    <div style="flex:1;height:32px;background:var(--bg-2, #f3f4f6);border-radius:6px;overflow:hidden;position:relative">
      <div style="height:100%;width:${pct}%;background:${color};transition:width .6s ease;border-radius:6px;display:flex;align-items:center;justify-content:flex-end;padding-right:8px;color:white;font-weight:600;font-size:12px">
        ${pct > 30 ? `${sign}${fmtRs(Math.abs(value))}` : ''}
      </div>
    </div>
    <div style="width:110px;font-weight:600;font-size:13px;color:${type === 'negative' ? 'var(--danger-text, #dc2626)' : type === 'result' ? 'var(--info, #3b82f6)' : 'var(--success-text, #16a34a)'}">${sign}${fmtRs(Math.abs(value))}</div>
  </div>`;
}

route('/reports/monthly-profit', async (el) => {
  const thisMonth = new Date().toISOString().slice(0, 7);
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.chart}</div>
      <div>
        <h2 class="pos-page-header-title">Monthly Profit</h2>
        <p class="pos-page-header-sub">COGS = Opening + Purchases − Closing. Gross Profit and Operating Profit shown separately.</p>
      </div>
      <div class="pos-page-header-actions">
        <div class="pos-date-input">
          <span class="pos-date-input-icon">${SVG.calendar}</span>
          <input class="input input-sm" id="mp-month" type="month" value="${thisMonth}">
        </div>
      </div>
    </div>
    <div id="mp-out">${skeletonCards(2)}</div>`;

  $('#mp-month').onchange = loadReport;
  await loadReport();

  async function loadReport() {
    const month = $('#mp-month').value;
    try {
      const r = await api(`/api/profit/monthly?month=${month}`);
      if (r.sales === 0 && r.cogs === 0 && (r.extra_sales_income || 0) === 0) {
        $('#mp-out').innerHTML = `
          <div class="card text-center" style="padding:48px">
            <p style="font-weight:600;margin-bottom:8px">No data for ${esc(month)}</p>
            <p class="text-dim text-sm">Record sales and purchases this month to see the profit breakdown.</p>
          </div>`;
        return;
      }
      // v8.18.14: extra (non-POS) sales income — shown as its own step in the
      // waterfall so it stays differentiable from POS sales
      const extraIncome = r.extra_sales_income || 0;
      const bridgeValues = [r.opening_inventory, r.purchases, r.closing_inventory, r.cogs, r.sales, r.gross_profit, r.operating_expenses, r.operating_profit, extraIncome];
      const maxAbs = Math.max(...bridgeValues.map(Math.abs), 1);
      const cogsMatch = Math.abs(r.cogs - r.cogs_from_sales) < 1.0;

      $('#mp-out').innerHTML = `
        <div class="grid grid-4" style="gap:12px;margin-bottom:16px">
          <div class="card" style="padding:16px">
            <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Sales</div>
            <div style="font-size:22px;font-weight:700;margin-top:4px">${fmtRs(r.sales)}</div>
          </div>
          <div class="card" style="padding:16px">
            <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Gross Profit</div>
            <div style="font-size:22px;font-weight:700;margin-top:4px;color:var(--success-text, #16a34a)">${fmtRs(r.gross_profit)}</div>
            <div class="text-dim text-sm">${fmtPct(r.monthly_margin)} margin</div>
          </div>
          <div class="card" style="padding:16px">
            <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Operating Profit</div>
            <div style="font-size:22px;font-weight:700;margin-top:4px;color:${r.operating_profit >= 0 ? 'var(--success-text, #16a34a)' : 'var(--danger-text, #dc2626)'}">${fmtRs(r.operating_profit)}</div>
          </div>
          <div class="card" style="padding:16px">
            <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">COGS Cross-Check</div>
            <div style="font-size:18px;font-weight:700;margin-top:4px;color:${cogsMatch ? 'var(--success-text, #16a34a)' : 'var(--danger-text, #dc2626)'}">
              ${cogsMatch ? '✓ Match' : '✗ Mismatch'}
            </div>
            <div class="text-dim text-sm">Δ ${fmtRs(r.cogs_difference)}</div>
          </div>
        </div>

        <div class="card" style="padding:24px;margin-bottom:16px">
          <h3 style="margin-bottom:4px">COGS Bridge</h3>
          <p class="text-dim text-sm" style="margin-bottom:20px">Opening Inventory + Purchases − Closing Inventory = COGS</p>
          ${bridgeBar('Opening Inventory', r.opening_inventory, maxAbs, 'positive')}
          ${bridgeBar('+ Purchases', r.purchases, maxAbs, 'positive')}
          ${bridgeBar('− Closing Inventory', r.closing_inventory, maxAbs, 'negative')}
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">
            <div style="width:160px;text-align:right"></div>
            <div style="flex:1;color:var(--text-dim);font-size:12px">= COGS (bridge method)</div>
            <div style="width:110px"></div>
          </div>
          ${bridgeBar('= COGS', r.cogs, maxAbs, 'result')}
          <div style="margin-top:12px;padding:10px;background:var(--bg-2, #f3f4f6);border-radius:8px;font-size:13px">
            <strong>Cross-check:</strong> COGS from sale_items.cost_price = ${fmtRs(r.cogs_from_sales)}
            (difference ${fmtRs(r.cogs_difference)} — ${cogsMatch ? 'within rounding ✓' : 'mismatch — investigate'})
          </div>
        </div>

        <div class="card" style="padding:24px">
          <h3 style="margin-bottom:4px">Profit Waterfall</h3>
          <p class="text-dim text-sm" style="margin-bottom:20px">Sales → −COGS → =Gross Profit${extraIncome > 0 ? ' → +Extra Sales (non-POS)' : ''} → −Operating Expenses → =Operating Profit</p>
          ${bridgeBar('Sales (POS)', r.sales, maxAbs, 'positive')}
          ${bridgeBar('− COGS', r.cogs, maxAbs, 'negative')}
          ${bridgeBar('= Gross Profit', r.gross_profit, maxAbs, 'result')}
          ${extraIncome > 0 ? bridgeBar('+ Extra Sales (non-POS — cartons, raddi)', extraIncome, maxAbs, 'positive') : ''}
          ${bridgeBar('− Operating Expenses', r.operating_expenses, maxAbs, 'negative')}
          ${bridgeBar('= Operating Profit', r.operating_profit, maxAbs, 'result')}
          ${extraIncome > 0 ? `
            <div style="margin-top:12px;padding:10px;background:var(--bg-2, #f3f4f6);border-left:3px solid var(--success, #16a34a);border-radius:8px;font-size:13px">
              <strong>Extra Sales: ${fmtRs(extraIncome)}</strong> — income from non-stock items sold outside the POS (cartons, raddi/scrap...). No COGS, not included in the Sales figure above. <a href="#/bills/extra-sales" style="text-decoration:underline">Manage Extra Sales →</a>
            </div>` : ''}
          ${r.owner_draws > 0 ? `
            <div style="margin-top:12px;padding:10px;background:var(--bg-2, #f3f4f6);border-radius:8px;font-size:13px">
              <strong>Owner Draws: ${fmtRs(r.owner_draws)}</strong> — excluded from operating expenses (equity reduction).
            </div>` : ''}
        </div>`;
    } catch (e) {
      $('#mp-out').innerHTML = errorBox(e.message);
    }
  }
});
