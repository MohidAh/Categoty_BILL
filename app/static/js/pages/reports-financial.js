// Reports app — Financial sub-pages: P&L, Cash Flow, Balance Sheet, Sales Targets
// Split from reports-pages.js (Phase 9) to keep each file under 700 lines.
import { route } from '../router.js';
import { api, apiPost } from '../api.js';
import { $, esc, fmt, fmtRs, toast, skeletonCards, errorBox } from '../utils.js';

// Shared SVG icon set (local copy — same as reports-pages.js)
const SVG = {
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
  trendUp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>',
  scale: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M7 14l4-4 4 4 5-5"/></svg>',
  target: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>',
  save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>',
};

function statCard(label, value, chipClass, svgIcon, sub = '') {
  return `<div class="stat-card">
    <div class="stat-card-icon ${chipClass}">${svgIcon}</div>
    <div class="stat-card-label">${esc(label)}</div>
    <div class="stat-card-value">${value}</div>
    ${sub ? `<div class="stat-card-delta">${sub}</div>` : ''}
  </div>`;
}

// ═══════════════════════════════════════════════════
// P&L STATEMENT
// ═══════════════════════════════════════════════════
route('/reports/pnl', async (el) => {
  const thisMonth = new Date().toISOString().slice(0, 7);
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.chart}</div>
      <div>
        <h2 class="pos-page-header-title">Profit & Loss Statement</h2>
        <p class="pos-page-header-sub">Monthly income statement: revenue, COGS, expenses, and net profit.</p>
      </div>
      <div class="pos-page-header-actions">
        <div class="pos-date-input">
          <span class="pos-date-input-icon">${SVG.calendar}</span>
          <input class="input input-sm" id="pnl-month" type="month" value="${thisMonth}">
        </div>
      </div>
    </div>
    <div id="pnl-out">${skeletonCards(2)}</div>`;

  $('#pnl-month').onchange = loadReport;
  await loadReport();

  async function loadReport() {
    const month = $('#pnl-month').value;
    try {
      const r = await api(`/api/reports/pnl?month=${month}`);
      // v8.18.11 fix: this page read r.revenue / r.cogs / r.expenses_total —
      // keys that NEVER existed (the API returns net_revenue / cost_of_goods /
      // expenses) and treated `expenses` (a number) as an array, so the whole
      // statement rendered Rs 0 everywhere. Rewritten to the real contract;
      // per-category rows come from expenses_by_category, owner draws are
      // shown separately (equity reduction, not an expense).
      const revenue = r.net_revenue || 0;
      const cogs = r.cost_of_goods || 0;
      const expensesTotal = r.expenses || 0;
      const ownerDraws = r.owner_draws || 0;
      // v8.18.13: extra (non-stock) sales — other income
      const otherIncome = r.other_income || 0;
      const grossProfit = r.gross_profit || 0;
      const netProfit = r.net_profit || 0;
      const grossMarginPct = revenue > 0 ? (grossProfit / revenue) * 100 : 0;
      const netMarginPct = revenue > 0 ? (netProfit / revenue) * 100 : 0;
      const expRows = r.expenses_by_category || [];

      $('#pnl-out').innerHTML = `
        <div class="grid grid-4 mb-4">
          ${statCard('Revenue', fmtRs(revenue), 'chip-success', SVG.trendUp)}
          ${statCard('COGS', fmtRs(cogs), 'chip-warning', SVG.wallet)}
          ${statCard('Gross Profit', fmtRs(grossProfit), 'chip-info', SVG.chart, `${grossMarginPct.toFixed(1)}% margin`)}
          ${statCard('Net Profit', fmtRs(netProfit), netProfit >= 0 ? 'chip-success' : 'chip-danger', SVG.trendUp, `${netMarginPct.toFixed(1)}% margin`)}
        </div>

        <div class="grid grid-2">
          <div class="card">
            <h3>Income & COGS</h3>
            <div class="stat-list mt-3">
              <div class="stat-row"><span>Sales Revenue</span><span class="text-success">${fmtRs(revenue)}</span></div>
              <div class="stat-row"><span>Discounts Given</span><span class="text-warning">${fmtRs(r.discounts)}</span></div>
              ${otherIncome > 0 ? `<div class="stat-row"><span>Other Income <span class="text-xs text-dim">(Extra Sales — cartons, raddi...)</span></span><span class="text-success">+ ${fmtRs(otherIncome)}</span></div>` : ''}
              <div class="stat-row" style="border-top:2px solid var(--border)"><span class="font-bold">Cost of Goods Sold</span><span class="font-bold">${fmtRs(cogs)}</span></div>
              <div class="stat-row"><span class="font-bold">Gross Profit</span><span class="font-bold text-success">${fmtRs(grossProfit)}</span></div>
              <div class="stat-row"><span class="text-dim">Purchases (bills)</span><span class="text-dim">${fmtRs(r.purchases || 0)}</span></div>
            </div>
          </div>
          <div class="card">
            <h3>Operating Expenses</h3>
            <div class="stat-list mt-3">
              ${expRows.length ? expRows.map(e => `<div class="stat-row"><span>${esc(e.category)}</span><span>${fmtRs(e.total)}</span></div>`).join('') : '<p class="text-dim text-sm">No expenses recorded this month.</p>'}
              ${ownerDraws > 0 ? `<div class="stat-row"><span class="text-dim">Owner Draws <span class="text-xs">(equity, not expense)</span></span><span class="text-dim">${fmtRs(ownerDraws)}</span></div>` : ''}
              <div class="stat-row" style="border-top:2px solid var(--border)"><span class="font-bold">Total Expenses</span><span class="font-bold text-danger">${fmtRs(expensesTotal)}</span></div>
            </div>
          </div>
        </div>

        <div class="card mt-4 text-center">
          <div class="kpi-label">Net Profit (${esc(month)})</div>
          <div style="font-size:36px;font-weight:800;color:${netProfit >= 0 ? 'var(--success-text)' : 'var(--danger-text)'}">${fmtRs(netProfit)}</div>
          <div class="text-sm text-dim mt-1">${netMarginPct.toFixed(1)}% net margin</div>
        </div>`;
    } catch (e) {
      $('#pnl-out').innerHTML = errorBox(e.message);
    }
  }
});

// ═══════════════════════════════════════════════════
// CASH FLOW
// ═══════════════════════════════════════════════════
route('/reports/cash-flow', async (el) => {
  const thisMonth = new Date().toISOString().slice(0, 7);
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.wallet}</div>
      <div>
        <h2 class="pos-page-header-title">Cash Flow Statement</h2>
        <p class="pos-page-header-sub">Monthly cash inflows and outflows.</p>
      </div>
      <div class="pos-page-header-actions">
        <div class="pos-date-input">
          <span class="pos-date-input-icon">${SVG.calendar}</span>
          <input class="input input-sm" id="cf-month" type="month" value="${thisMonth}">
        </div>
      </div>
    </div>
    <div id="cf-out">${skeletonCards(2)}</div>`;

  $('#cf-month').onchange = loadReport;
  await loadReport();

  async function loadReport() {
    const month = $('#cf-month').value;
    try {
      const r = await api(`/api/reports/cash-flow?month=${month}`);
      $('#cf-out').innerHTML = `
        <div class="grid grid-2">
          <div class="card">
            <h3>Cash Inflows (${esc(r.month)})</h3>
            <div class="stat-list mt-3">
              <div class="stat-row"><span>Cash Sales</span><span class="text-success">${fmtRs(r.inflows.cash_sales)}</span></div>
              <div class="stat-row"><span>Split (cash portion)</span><span class="text-success">${fmtRs(r.inflows.split_cash)}</span></div>
              <div class="stat-row"><span>Card Sales</span><span>${fmtRs(r.inflows.card_sales)}</span></div>
              <div class="stat-row"><span>Online Sales</span><span>${fmtRs(r.inflows.online_sales)}</span></div>
              ${(r.inflows.extra_sales_cash || 0) > 0 ? `<div class="stat-row"><span>Extra Sales (cash) <span class="text-xs text-dim">(cartons, raddi...)</span></span><span class="text-success">${fmtRs(r.inflows.extra_sales_cash)}</span></div>` : ''}
              ${(r.inflows.extra_sales_other || 0) > 0 ? `<div class="stat-row"><span>Extra Sales (bank/card)</span><span>${fmtRs(r.inflows.extra_sales_other)}</span></div>` : ''}
              <div class="stat-row"><span>Credit Payments Received</span><span class="text-success">${fmtRs(r.inflows.customer_payments)}</span></div>
              <div class="stat-row" style="border-top:2px solid var(--border)"><span class="font-bold">Total Cash In</span><span class="font-bold text-success">${fmtRs(r.inflows.total_in)}</span></div>
            </div>
          </div>
          <div class="card">
            <h3>Cash Outflows</h3>
            <div class="stat-list mt-3">
              <div class="stat-row"><span>Cash Expenses</span><span class="text-danger">${fmtRs(r.outflows.cash_expenses)}</span></div>
              <div class="stat-row"><span>Cash Drawer Out</span><span class="text-danger">${fmtRs(r.outflows.cash_drawer_out)}</span></div>
              <div class="stat-row"><span>Purchases (paid bills)</span><span class="text-danger">${fmtRs(r.outflows.purchases)}</span></div>
              <div class="stat-row" style="border-top:2px solid var(--border)"><span class="font-bold">Total Cash Out</span><span class="font-bold text-danger">${fmtRs(r.outflows.total_out)}</span></div>
            </div>
          </div>
        </div>
        <div class="card mt-4 text-center">
          <div class="kpi-label">Net Cash Position</div>
          <div style="font-size:36px;font-weight:800;color:${r.net_cash >= 0 ? 'var(--success-text)' : 'var(--danger-text)'}">${fmtRs(r.net_cash)}</div>
        </div>`;
    } catch (e) {
      $('#cf-out').innerHTML = errorBox(e.message);
    }
  }
});

// ═══════════════════════════════════════════════════
// BALANCE SHEET
// ═══════════════════════════════════════════════════
route('/reports/balance-sheet', async (el) => {
  const today = new Date().toISOString().slice(0, 10);
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.scale}</div>
      <div>
        <h2 class="pos-page-header-title">Balance Sheet</h2>
        <p class="pos-page-header-sub">Snapshot of assets, liabilities, and owner's equity.</p>
      </div>
      <div class="pos-page-header-actions">
        <div class="pos-date-input">
          <span class="pos-date-input-icon">${SVG.calendar}</span>
          <input class="input input-sm" id="bs-date" type="date" value="${today}">
        </div>
      </div>
    </div>
    <div id="bs-out">${skeletonCards(2)}</div>`;

  $('#bs-date').onchange = loadReport;
  await loadReport();

  async function loadReport() {
    const asOf = $('#bs-date').value;
    try {
      const r = await api(`/api/reports/balance-sheet?as_of=${asOf}`);
      $('#bs-out').innerHTML = `
        <div class="grid grid-2">
          <div class="card">
            <h3>Assets (as of ${esc(r.as_of)})</h3>
            <div class="stat-list mt-3">
              <div class="stat-row"><span>Cash on Hand</span><span>${fmtRs(r.assets.cash_on_hand)}</span></div>
              <div class="stat-row"><span>Inventory (at cost)</span><span>${fmtRs(r.assets.inventory_value)}</span></div>
              <div class="stat-row"><span>Inventory (potential revenue)</span><span class="text-dim">${fmtRs(r.assets.inventory_potential_revenue)}</span></div>
              <div class="stat-row"><span>Receivables (outstanding credit)</span><span>${fmtRs(r.assets.receivables)}</span></div>
              <div class="stat-row" style="border-top:2px solid var(--border)"><span class="font-bold">Total Assets</span><span class="font-bold text-success">${fmtRs(r.assets.total)}</span></div>
            </div>
          </div>
          <div class="card">
            <h3>Liabilities</h3>
            <div class="stat-list mt-3">
              <div class="stat-row"><span>Payables (urdhaar to suppliers)</span><span class="text-danger">${fmtRs(r.liabilities.payables)}</span></div>
              <div class="stat-row" style="border-top:2px solid var(--border)"><span class="font-bold">Total Liabilities</span><span class="font-bold text-danger">${fmtRs(r.liabilities.total)}</span></div>
            </div>
          </div>
        </div>
        <div class="card mt-4 text-center">
          <div class="kpi-label">Owner's Equity (Assets &minus; Liabilities)</div>
          <div style="font-size:36px;font-weight:800;color:${r.equity >= 0 ? 'var(--success-text)' : 'var(--danger-text)'}">${fmtRs(r.equity)}</div>
        </div>`;
    } catch (e) {
      $('#bs-out').innerHTML = errorBox(e.message);
    }
  }
});

// ═══════════════════════════════════════════════════
// SALES TARGETS — set & track daily/monthly targets
// ═══════════════════════════════════════════════════
route('/reports/targets', async (el) => {
  const today = new Date().toISOString().slice(0, 10);
  const thisMonth = new Date().toISOString().slice(0, 7);

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.target}</div>
      <div>
        <h2 class="pos-page-header-title">Sales Targets</h2>
        <p class="pos-page-header-sub">Set daily and monthly targets &mdash; track progress in real time.</p>
      </div>
      <div class="pos-page-header-actions"></div>
    </div>
    <div class="grid grid-2">
      <div class="card">
        <h3>Daily Target</h3>
        <div id="daily-target" class="mt-3"><p class="text-dim text-sm">Loading...</p></div>
        <hr class="my-3">
        <h4>Set New Daily Target</h4>
        <div class="grid grid-2 mt-2">
          <input class="input" id="dt-date" type="date" value="${today}">
          <input class="input" id="dt-amount" type="number" placeholder="Rs" value="10000">
        </div>
        <button class="btn mt-2" id="dt-save-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.save}</span>
          Save Daily Target
        </button>
      </div>
      <div class="card">
        <h3>Monthly Target</h3>
        <div id="monthly-target" class="mt-3"><p class="text-dim text-sm">Loading...</p></div>
        <hr class="my-3">
        <h4>Set New Monthly Target</h4>
        <div class="grid grid-2 mt-2">
          <input class="input" id="mt-date" type="month" value="${thisMonth}">
          <input class="input" id="mt-amount" type="number" placeholder="Rs" value="300000">
        </div>
        <button class="btn mt-2" id="mt-save-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.save}</span>
          Save Monthly Target
        </button>
      </div>
    </div>`;

  $('#dt-save-btn').onclick = async () => {
    try {
      await apiPost('/api/sales-targets', {
        period: 'daily',
        target_date: $('#dt-date').value,
        target_amount: parseFloat($('#dt-amount').value),
      });
      toast('Daily target saved', 'success');
      loadTargetProgress();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  };
  $('#mt-save-btn').onclick = async () => {
    try {
      await apiPost('/api/sales-targets', {
        period: 'monthly',
        target_date: $('#mt-date').value,
        target_amount: parseFloat($('#mt-amount').value),
      });
      toast('Monthly target saved', 'success');
      loadTargetProgress();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  };

  await loadTargetProgress();

  async function loadTargetProgress() {
    try {
      const today = $('#dt-date').value;
      const thisMonth = $('#mt-date').value;
      const [d, m] = await Promise.all([
        api(`/api/sales-targets/progress?period=daily&target_date=${today}`),
        api(`/api/sales-targets/progress?period=monthly&target_date=${thisMonth}`),
      ]);
      $('#daily-target').innerHTML = renderTargetCard(d, 'Today');
      $('#monthly-target').innerHTML = renderTargetCard(m, 'This Month');
    } catch (e) { toast('Error loading targets', 'error'); }
  }

  function renderTargetCard(t, label) {
    if (!t.target) return `<p class="text-dim text-sm">No ${label} target set.</p>`;
    const pct = Math.min(100, t.progress_pct);
    const color = pct >= 100 ? 'var(--success-text)' : pct >= 50 ? 'var(--accent-text)' : 'var(--warning-text)';
    return `
      <div class="target-progress">
        <div class="flex justify-between">
          <span class="text-sm">Actual: <b class="text-success">${fmtRs(t.actual)}</b></span>
          <span class="text-sm text-dim">Target: <b>${fmtRs(t.target)}</b></span>
        </div>
        <div class="progress-bar mt-2">
          <div class="progress-bar-fill" style="width:${pct}%;background:${color}"></div>
        </div>
        <div class="flex justify-between mt-1">
          <span class="text-xs text-dim">${t.progress_pct}% achieved</span>
          <span class="text-xs ${t.remaining > 0 ? 'text-warning' : 'text-success'}">${t.remaining > 0 ? `Rs ${fmt(t.remaining)} to go` : 'Target hit!'}</span>
        </div>
      </div>`;
  }
});
