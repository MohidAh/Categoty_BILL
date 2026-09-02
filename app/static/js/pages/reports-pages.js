// Reports app — Overview, Billwise, P&L, Cash Flow, Balance Sheet,
// Top Items, Peak Hours, Targets, Monthly Close, Export Center
// All render inside the Reports app SnowUI shell (chip-secondary color theme).
import { route, navigate, reload } from '../router.js';
import { errorState } from '../core/states.js';
import { api } from '../api.js';
import { $, $$, esc, fmt, fmtRs, fmtDate, fmtPct, icon, toast, showLoading, hideLoading,
         openModal, closeModal, skeletonCards, skeletonRows, errorBox, emptyState, chartTheme } from '../utils.js';
import { initListState } from '../list-state.js';

// Shared SVG icon set
const SVG = {
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  trendUp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
  target: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>',
  scale: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M7 14l4-4 4 4 5-5"/></svg>',
  bills: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
  print: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
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
// /reports — redirect to Store Profit Dashboard (v5.0 Phase 8 default landing)
// ═══════════════════════════════════════════════════
route('/reports', async (el) => {
  // v5.0: default landing is now the Store Profit Dashboard
  window.location.hash = '#/reports/store-profit';
  el.innerHTML = '<div class="card text-center text-dim" style="padding:24px">Redirecting to Store Profit Dashboard…</div>';
});


// ═══════════════════════════════════════════════════
// OVERVIEW — date range + 4 tab views (Overview/Billwise/Category/Suppliers)
// ═══════════════════════════════════════════════════
route('/reports/overview', async (el, path, q) => {
  // v8.18.5: date range + tab persist across navigation
  const st = initListState('reportsOverview', q, { start: '', end: '', tab: 'overview' });
  st.syncUrlIfRestored();
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const initStart = st.val('start') || monthAgo;
  const initEnd = st.val('end') || today;
  st.replace({ start: initStart, end: initEnd });

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.chart}</div>
      <div>
        <h2 class="pos-page-header-title">Reports Overview</h2>
        <p class="pos-page-header-sub">Analyze spend, profit, and supplier performance.</p>
      </div>
      <div class="pos-page-header-actions"></div>
    </div>

    <div class="card mb-4">
      <div class="report-filters">
        <div><label>From Date</label><input class="input" id="r-start" type="date" value="${initStart}"></div>
        <div><label>To Date</label><input class="input" id="r-end" type="date" value="${initEnd}"></div>
        <button class="btn" id="r-generate-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span>
          Generate
        </button>
      </div>
      <div class="flex gap-2 mt-4" id="r-tabs">
        <button class="btn btn-secondary btn-sm tab-btn ${st.val('tab') === 'overview' ? 'active' : ''}" data-tab="overview">Overview</button>
        <button class="btn btn-secondary btn-sm tab-btn ${st.val('tab') === 'billwise' ? 'active' : ''}" data-tab="billwise">Bill-wise</button>
        <button class="btn btn-secondary btn-sm tab-btn ${st.val('tab') === 'category' ? 'active' : ''}" data-tab="category">Category-wise</button>
        <button class="btn btn-secondary btn-sm tab-btn ${st.val('tab') === 'suppliers' ? 'active' : ''}" data-tab="suppliers">Suppliers</button>
      </div>
    </div>

    <div id="r-out">${skeletonCards(3)}</div>`;

  let currentTab = st.val('tab') || 'overview';
  let cachedData = {};

  $$('.tab-btn').forEach(btn => {
    btn.onclick = () => {
      currentTab = btn.dataset.tab;
      st.replace({ tab: currentTab });
      $$('.tab-btn').forEach(b => b.classList.toggle('active', b === btn));
      renderTab();
    };
  });
  $('#r-generate-btn').onclick = () => { st.replace({ start: $('#r-start').value, end: $('#r-end').value }); runReports(); };
  await runReports();

  async function runReports() {
    const start = $('#r-start').value, end = $('#r-end').value;
    $('#r-out').innerHTML = skeletonCards(3);
    try {
      const [monthly, profit, suppliers, billwise, category] = await Promise.all([
        api(`/api/reports/monthly?start=${start}&end=${end}`),
        api(`/api/reports/profit?start=${start}&end=${end}`),
        api('/api/reports/suppliers'),
        api(`/api/reports/billwise?start=${start}&end=${end}`),
        api(`/api/reports/category?start=${start}&end=${end}`),
      ]);
      cachedData = { monthly, profit, suppliers, billwise, category };
      renderTab();
    } catch (e) {
      $('#r-out').innerHTML = errorBox(e.message, "location.reload()");
    }
  }

  function renderTab() {
    const d = cachedData;
    if (currentTab === 'overview') renderOverview(d);
    else if (currentTab === 'billwise') renderBillwise(d.billwise);
    else if (currentTab === 'category') renderCategory(d.category);
    else if (currentTab === 'suppliers') renderSuppliers(d.suppliers);
  }

  function renderOverview(d) {
    const m = d.monthly, p = d.profit;
    $('#r-out').innerHTML = `
      <div class="grid grid-4 mb-4">
        ${statCard('Total Spend', fmtRs(m.kpis.total_spend), 'chip-success', SVG.wallet)}
        ${statCard('Total Bills', m.kpis.total_bills, 'chip-primary', SVG.bills)}
        ${statCard('Avg / Bill', fmtRs(m.kpis.avg_per_bill), 'chip-info', SVG.chart)}
        ${statCard('Avg / Day', fmtRs(m.kpis.avg_per_day), 'chip-secondary', SVG.trendUp)}
      </div>
      <div class="card mb-4">
        <div class="card-title"><h3>Daily Spend Trend</h3></div>
        ${m.series.length ? `<div class="chart-container"><canvas id="trend-chart"></canvas></div>` : '<p class="text-dim">No data in this date range.</p>'}
      </div>
      <div class="card">
        <div class="card-title" style="display:flex;justify-content:space-between;align-items:center">
          <h3>Profit by Category</h3>
          <span class="text-dim text-sm">Historical cost-at-time-of-sale · differs from Store Profit (forward-looking)</span>
        </div>
        ${p.categories.length ? `
        <div class="table-wrap"><table>
          <thead><tr><th>Code</th><th>Category</th><th class="table-num">Pieces</th><th class="table-num">Cost</th><th class="table-num">Revenue</th><th class="table-num">Profit</th><th>Margin</th><th class="table-num">Bills</th></tr></thead>
          <tbody>${p.categories.map(c => `<tr>
            <td><span class="badge badge-accent">${esc(c.code)}</span></td>
            <td class="font-semibold">${esc(c.name)}</td>
            <td class="table-num">${fmt(c.pieces)}</td>
            <td class="table-num">${fmtRs(c.cost)}</td>
            <td class="table-num">${fmtRs(c.revenue)}</td>
            <td class="table-num ${c.margin >= 0.3 ? 'text-success' : c.margin >= 0.2 ? 'text-warning' : 'text-danger'}">${fmtRs(c.profit)}</td>
            <td class="${c.margin >= 0.3 ? 'text-success' : c.margin >= 0.2 ? 'text-warning' : 'text-danger'}">${esc(c.margin_pct)}</td>
            <td class="table-num">${c.bills}</td>
          </tr>`).join('')}</tbody>
        </table></div>` : '<p class="text-dim">No confirmed bills in this range.</p>'}
      </div>`;
    if (m.series.length) {
      new Chart($('#trend-chart'), {
        type: 'line',
        data: { labels: m.series.map(s => s.date), datasets: [{ label: 'Daily Spend', data: m.series.map(s => s.spend), borderColor: chartTheme().primary, backgroundColor: chartTheme().primarySoft, fill: true, tension: .3, borderWidth: 2 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { ticks: { color: chartTheme().tickColor }, grid: { color: chartTheme().gridColor } }, x: { ticks: { color: chartTheme().tickColor, maxRotation: 45 }, grid: { display: false } } } }
      });
    }
  }

  function renderBillwise(d) {
    if (!d?.bills?.length) {
      $('#r-out').innerHTML = '<div class="card"><p class="text-dim">No confirmed bills in this date range.</p></div>';
      return;
    }
    let html = `<div class="card mb-4"><div class="card-title"><h3>Bill-wise Report (${d.total_bills} bills)</h3></div>`;
    for (const b of d.bills) {
      html += `
        <div class="billwise-section">
          <div class="billwise-header billwise-row-clickable" data-bill="${b.bill_id}">
            <strong>Bill #${b.bill_id}</strong> &mdash; ${esc(b.supplier_name || '—')} &mdash; ${fmtDate(b.bill_date)} &mdash; ${fmtRs(b.total)} &mdash; <span class="badge ${b.payment_status === 'credit' ? 'badge-danger' : 'badge-success'}">${esc(b.payment_status)}</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th style="width:30px">Sr</th><th>Item</th><th>Code</th><th class="table-num">Price</th><th class="table-num">Qty</th><th class="table-num">Line Total</th><th class="table-num">Cost</th><th class="table-num">Profit</th><th>Margin</th></tr></thead>
              <tbody>${b.items.map(it => `<tr>
                <td class="text-dim">${it.sr_no}</td>
                <td>${esc(it.raw)}</td>
                <td class="text-sm text-dim">${esc(it.item_code || '—')}</td>
                <td class="table-num">${fmtRs(it.price)}</td>
                <td class="table-num">${fmt(it.qty)}</td>
                <td class="table-num">${fmtRs(it.line_total)}</td>
                <td class="table-num">${fmtRs(it.cost)}</td>
                <td class="table-num ${it.margin >= 0.3 ? 'text-success' : it.margin >= 0.2 ? 'text-warning' : 'text-danger'}">${fmtRs(it.profit)}</td>
                <td class="${it.margin >= 0.3 ? 'text-success' : it.margin >= 0.2 ? 'text-warning' : 'text-danger'}">${esc(it.margin_pct)}</td>
              </tr>`).join('')}</tbody>
            </table>
          </div>
        </div>`;
    }
    $('#r-out').innerHTML = html + '</div>';
    $$('.billwise-row-clickable').forEach(row => {
      row.style.cursor = 'pointer';
      row.onclick = () => navigate('/bills/' + row.dataset.bill);
    });
  }

  function renderCategory(d) {
    if (!d?.categories?.length) {
      $('#r-out').innerHTML = '<div class="card"><p class="text-dim">No data in this date range.</p></div>';
      return;
    }
    $('#r-out').innerHTML = `
      <div class="grid grid-4 mb-4">
        ${statCard('Total Products', d.grand.total_products, 'chip-primary', SVG.bills)}
        ${statCard('Total Cost', fmtRs(d.grand.total_cost), 'chip-success', SVG.wallet)}
        ${statCard('Total Revenue', fmtRs(d.grand.total_revenue), 'chip-info', SVG.trendUp)}
        ${statCard('Profit Margin', esc(d.grand.profit_margin_pct), d.grand.margin >= 0.2 ? 'chip-success' : 'chip-danger', SVG.chart)}
      </div>
      <div class="card">
        <div class="card-title"><h3>Category-wise Breakdown</h3></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Code</th><th>Category</th><th class="table-num">Products</th><th class="table-num">Pieces</th><th class="table-num">Cost</th><th class="table-num">Revenue</th><th class="table-num">Profit</th><th>Margin</th><th class="table-num">Bills</th></tr></thead>
          <tbody>${d.categories.map(c => `<tr>
            <td><span class="badge badge-accent" style="${c.color ? `background:${c.color}22;color:${c.color}` : ''}">${esc(c.code)}</span></td>
            <td class="font-semibold">${esc(c.name)}</td>
            <td class="table-num">${c.total_products}</td>
            <td class="table-num">${fmt(c.total_pieces)}</td>
            <td class="table-num">${fmtRs(c.total_cost)}</td>
            <td class="table-num">${fmtRs(c.total_revenue)}</td>
            <td class="table-num ${c.margin >= 0.3 ? 'text-success' : c.margin >= 0.2 ? 'text-warning' : 'text-danger'}">${fmtRs(c.total_profit)}</td>
            <td class="${c.margin >= 0.3 ? 'text-success' : c.margin >= 0.2 ? 'text-warning' : 'text-danger'} font-semibold">${esc(c.profit_margin_pct)}</td>
            <td class="table-num">${c.bill_count}</td>
          </tr>`).join('')}</tbody>
          <tfoot><tr style="font-weight:700;border-top:2px solid var(--border)">
            <td colspan="2">Grand Total</td>
            <td class="table-num">${d.grand.total_products}</td>
            <td class="table-num">${fmt(d.grand.total_pieces)}</td>
            <td class="table-num">${fmtRs(d.grand.total_cost)}</td>
            <td class="table-num">${fmtRs(d.grand.total_revenue)}</td>
            <td class="table-num ${d.grand.margin >= 0.2 ? 'text-success' : 'text-danger'}">${fmtRs(d.grand.total_profit)}</td>
            <td class="${d.grand.margin >= 0.2 ? 'text-success' : 'text-danger'} font-bold">${esc(d.grand.profit_margin_pct)}</td>
            <td></td>
          </tr></tfoot>
        </table></div>
      </div>`;
  }

  function renderSuppliers(d) {
    if (!d?.suppliers?.length) {
      $('#r-out').innerHTML = '<div class="card"><p class="text-dim">No supplier data yet.</p></div>';
      return;
    }
    $('#r-out').innerHTML = `
      <div class="card">
        <div class="card-title"><h3>Supplier Ranking</h3></div>
        <div class="table-wrap"><table class="table-clickable">
          <thead><tr><th>#</th><th>Supplier</th><th class="table-num">Bills</th><th class="table-num">Total Spent</th><th class="table-num">Outstanding</th><th>Last Purchase</th></tr></thead>
          <tbody>${d.suppliers.map((s, i) => `<tr class="supplier-rank-row" data-id="${s.id}">
            <td class="text-dim">${i + 1}</td>
            <td class="font-semibold">${esc(s.name)}</td>
            <td class="table-num">${s.bill_count}</td>
            <td class="table-num">${fmtRs(s.total_spent)}</td>
            <td class="table-num ${s.outstanding > 0 ? 'text-danger' : ''}">${fmtRs(s.outstanding)}</td>
            <td class="text-sm">${fmtDate(s.last_purchase)}</td>
          </tr>`).join('')}</tbody>
        </table></div>
      </div>`;
    $$('.supplier-rank-row').forEach(row => {
      row.onclick = () => navigate('/suppliers/' + row.dataset.id);
    });
  }
});

// ═══════════════════════════════════════════════════
// ═══════════════════════════════════════════════════
// BILLWISE — master-detail layout (v8.5.3)
// Top: compact list of all bills. Click one → details show below.
// No status dropdown — all bills shown by default.
// Excel export produces per-category sheets (250/500/750/1000 + Summary).
// ═══════════════════════════════════════════════════
route('/reports/billwise', async (el, path, q) => {
  // v8.18.5: date range persists across navigation
  const st = initListState('reportsBillwise', q, { start: '', end: '' });
  st.syncUrlIfRestored();
  const today = new Date().toISOString().slice(0, 10);
  const yearAgo = new Date(Date.now() - 365 * 86400000).toISOString().slice(0, 10);
  const initStart = st.val('start') || yearAgo;
  const initEnd = st.val('end') || today;
  st.replace({ start: initStart, end: initEnd });
  let allBills = [];
  let selectedBillId = null;
  // v8.7: cache for lazy-loaded bill details (bill_id → bill detail dict)
  // so re-clicking a bill doesn't re-fetch.
  const detailCache = new Map();

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.bills}</div>
      <div>
        <h2 class="pos-page-header-title">Billwise Report</h2>
        <p class="pos-page-header-sub">Click a bill below to see its items, cost, and profit. Export selected bills as Excel.</p>
      </div>
      <div class="pos-page-header-actions">
        <input class="input input-sm" id="bw-start" type="date" value="${initStart}">
        <input class="input input-sm" id="bw-end" type="date" value="${initEnd}">
        <button class="btn btn-sm" id="bw-generate-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span>
          Generate
        </button>
      </div>
    </div>
    <div id="bw-out">${skeletonCards(2)}</div>`;

  $('#bw-generate-btn').onclick = () => { st.replace({ start: $('#bw-start').value, end: $('#bw-end').value }); loadReport(); };
  $('#bw-start').onchange = () => { st.replace({ start: $('#bw-start').value }); loadReport(); };
  $('#bw-end').onchange = () => { st.replace({ end: $('#bw-end').value }); loadReport(); };
  await loadReport();

  async function loadReport() {
    const start = $('#bw-start').value, end = $('#bw-end').value;
    try {
      // v8.7: default endpoint returns bill headers + precomputed aggregates (NO items).
      // Much lighter payload — was potentially several MB with all items embedded.
      const d = await api(`/api/reports/billwise?start=${start}&end=${end}&status=all`);
      allBills = d.bills || [];
      // Clear the detail cache when the date range changes
      detailCache.clear();
      if (!allBills.length) {
        $('#bw-out').innerHTML = emptyState('No bills found', `No bills in this date range (${start} to ${end}). Try widening the date range.`, '', '');
        return;
      }

      // v8.7: summary stats use the precomputed aggregates (no need to sum items[]).
      const totalCost = allBills.reduce((s, b) => s + (b.total_cost || 0), 0);
      const totalRevenue = allBills.reduce((s, b) => s + (b.total_revenue || 0), 0);
      const totalProfit = allBills.reduce((s, b) => s + (b.total_profit || 0), 0);

      // Auto-select the first bill so the details panel isn't empty
      selectedBillId = allBills[0].bill_id;

      const html = `
        <div class="grid grid-4 mb-4">
          ${statCard('Total Bills', allBills.length, 'chip-primary', SVG.bills)}
          ${statCard('Total Cost', fmtRs(totalCost), 'chip-success', SVG.wallet)}
          ${statCard('Total Revenue', fmtRs(totalRevenue), 'chip-info', SVG.trendUp)}
          ${statCard('Total Profit', fmtRs(totalProfit), 'chip-success', SVG.trendUp, `Margin ${totalRevenue > 0 ? ((totalProfit / totalRevenue) * 100).toFixed(1) : 0}%`)}
        </div>

        <!-- Toolbar: select-all + export -->
        <div class="card mb-3" style="padding:12px 16px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
          <div class="flex gap-2 items-center">
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px">
              <input type="checkbox" id="bw-select-all" style="width:16px;height:16px;cursor:pointer">
              <strong>Select All</strong>
            </label>
            <span class="text-dim text-sm" id="bw-selected-count">0 bills selected</span>
          </div>
          <div class="flex gap-2">
            <input class="input input-sm" id="bw-search" placeholder="Search supplier or bill no..." style="width:220px">
            <button class="btn btn-primary btn-sm" id="bw-export-xlsx" disabled>
              <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
              Export Excel
            </button>
          </div>
        </div>

        <!-- Master: compact list of all bills (clickable rows) -->
        <div class="card mb-4" id="bw-master-card">
          <div class="card-title"><h3>All Bills (${allBills.length})</h3></div>
          <div class="table-wrap">
            <table class="table" id="bw-master-table">
              <thead>
                <tr>
                  <th style="width:32px"><input type="checkbox" id="bw-select-all-header" style="width:16px;height:16px;cursor:pointer"></th>
                  <th>Bill #</th>
                  <th>Supplier</th>
                  <th>Date</th>
                  <th class="table-num">Total</th>
                  <th>Items</th>
                  <th>Cats</th>
                  <th class="table-num">Cost</th>
                  <th class="table-num">Profit</th>
                  <th>Status</th>
                  <th>Payment</th>
                </tr>
              </thead>
              <tbody>
                ${allBills.map(b => {
                  const isSelected = b.bill_id === selectedBillId;
                  const statusBadge = b.status === 'confirmed'
                    ? '<span class="badge badge-success">Confirmed</span>'
                    : '<span class="badge badge-warning">Review</span>';
                  return `<tr class="bw-bill-row ${isSelected ? 'bw-bill-row-selected' : ''}" data-bill-id="${b.bill_id}" style="cursor:pointer">
                    <td onclick="event.stopPropagation()"><input type="checkbox" class="bw-bill-checkbox" data-bill-id="${b.bill_id}" style="width:16px;height:16px;cursor:pointer"></td>
                    <td><strong>#${b.bill_id}</strong></td>
                    <td>${esc(b.supplier_name || '—')}</td>
                    <td class="text-sm text-dim">${fmtDate(b.bill_date)}</td>
                    <td class="table-num">${fmtRs(b.total)}</td>
                    <td class="text-sm">${b.item_count}</td>
                    <td class="text-sm text-dim">${b.category_count || '—'}</td>
                    <td class="table-num text-sm">${fmtRs(b.total_cost)}</td>
                    <td class="table-num text-sm ${b.total_profit >= 0 ? 'text-success' : 'text-danger'}">${fmtRs(b.total_profit)}</td>
                    <td>${statusBadge}</td>
                    <td><span class="badge ${b.payment_status === 'credit' ? 'badge-danger' : 'badge-success'}">${esc(b.payment_status)}</span></td>
                  </tr>`;
                }).join('')}
              </tbody>
            </table>
          </div>
        </div>

        <!-- Detail: selected bill's items (lazy-loaded) -->
        <div id="bw-detail-panel">${skeletonRows(5, 9)}</div>
      `;
      $('#bw-out').innerHTML = html;

      // Render the detail panel for the selected bill (lazy-load via GET /api/bills/{id})
      renderDetail(selectedBillId);

      // Wire row click → select bill
      $$('.bw-bill-row').forEach(row => {
        row.onclick = () => {
          const id = parseInt(row.dataset.billId);
          selectedBillId = id;
          // Update selected row styling
          $$('.bw-bill-row').forEach(r => r.classList.remove('bw-bill-row-selected'));
          row.classList.add('bw-bill-row-selected');
          renderDetail(id);
          // Scroll detail into view (smooth, only if it's below the fold)
          const detail = document.getElementById('bw-detail-panel');
          if (detail) {
            const rect = detail.getBoundingClientRect();
            if (rect.top < 100 || rect.top > window.innerHeight - 200) {
              detail.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
          }
        };
      });

      // Checkbox logic
      const selectAllHeader = $('#bw-select-all-header');
      const selectAllCard = $('#bw-select-all');  // The "Select All" in toolbar
      const exportXlsx = $('#bw-export-xlsx');
      const selectedCount = $('#bw-selected-count');

      function updateSelection() {
        const checked = document.querySelectorAll('.bw-bill-checkbox:checked');
        const count = checked.length;
        selectedCount.textContent = `${count} bill${count !== 1 ? 's' : ''} selected`;
        exportXlsx.disabled = count === 0;
        const allCbs = document.querySelectorAll('.bw-bill-checkbox');
        const allChecked = count === allCbs.length && count > 0;
        if (selectAllHeader) selectAllHeader.checked = allChecked;
        if (selectAllCard) selectAllCard.checked = allChecked;
        if (selectAllHeader) selectAllHeader.indeterminate = count > 0 && count < allCbs.length;
        if (selectAllCard) selectAllCard.indeterminate = count > 0 && count < allCbs.length;
      }

      // Both "select all" controls (header checkbox + toolbar checkbox) toggle all rows
      [selectAllHeader, selectAllCard].forEach(sa => {
        if (!sa) return;
        sa.onchange = () => {
          document.querySelectorAll('.bw-bill-checkbox').forEach(cb => {
            cb.checked = sa.checked;
          });
          // Keep both select-all controls in sync
          [selectAllHeader, selectAllCard].forEach(other => {
            if (other && other !== sa) other.checked = sa.checked;
          });
          updateSelection();
        };
      });

      document.querySelectorAll('.bw-bill-checkbox').forEach(cb => {
        cb.onchange = updateSelection;
      });

      // Search filter (filters the master list)
      $('#bw-search').oninput = (e) => {
        const q = e.target.value.toLowerCase();
        $$('.bw-bill-row').forEach(row => {
          const id = parseInt(row.dataset.billId);
          const b = allBills.find(x => x.bill_id === id);
          if (!b) return;
          const matchSupplier = (b.supplier_name || '').toLowerCase().includes(q);
          const matchBillNo = (b.bill_no || '').toLowerCase().includes(q);
          const matchId = String(b.bill_id).includes(q);
          row.style.display = (matchSupplier || matchBillNo || matchId || !q) ? '' : 'none';
        });
      };

      // Excel export
      exportXlsx.onclick = () => exportSelected();

      function exportSelected() {
        const checked = document.querySelectorAll('.bw-bill-checkbox:checked');
        const billIds = Array.from(checked).map(cb => parseInt(cb.dataset.billId));
        if (!billIds.length) return;
        const url = `/api/reports/billwise/export?start=${$('#bw-start').value}&end=${$('#bw-end').value}&bill_ids=${billIds.join(',')}&status=all`;
        toast(`Preparing Excel for ${billIds.length} bills...`, 'info');
        location.href = url;
      }

      // v8.7: Render the detail panel for a single bill — LAZY-LOADED via
      // GET /api/bills/{bill_id}. The bill list endpoint no longer returns
      // items[] (default), so we fetch the detail on demand + cache it.
      async function renderDetail(billId) {
        const detailEl = document.getElementById('bw-detail-panel');
        if (!detailEl) return;
        const bMaster = allBills.find(x => x.bill_id === billId);
        if (!bMaster) return;

        // Show skeleton while fetching
        detailEl.innerHTML = `<div class="card"><div class="card-title"><h3>Loading Bill #${billId}...</h3></div>${skeletonRows(5, 9)}</div>`;

        // Fetch full bill detail (cached)
        let b;
        if (detailCache.has(billId)) {
          b = detailCache.get(billId);
        } else {
          try {
            b = await api(`/api/bills/${billId}`);
            detailCache.set(billId, b);
          } catch (e) {
            detailEl.innerHTML = errorBox(`Failed to load bill #${billId}: ${e.message}`);
            return;
          }
        }

        // Compute per-bill + per-item profit (same logic as the old billwise_report)
        const items = (b.items || []).map((it, idx) => {
          // dozen → pcs conversion (matches pieces() in validate.py)
          const p = it.unit === 'dozen' ? it.qty * 12 : it.qty;
          const sell = it.cat_sell_price || it.ai_sell_price || 0;
          const cost = (it.price || 0) * p;
          const revenue = sell * p;
          const profit = revenue - cost;
          const margin = revenue > 0 ? profit / revenue : 0;
          return {
            sr_no: idx + 1,
            raw: it.raw,
            item_code: it.item_code,
            price: it.price,
            qty: it.qty,
            unit: it.unit,
            pieces: p,
            line_total: it.line_total,
            cat_name: it.cat_name,
            sell_price: sell,
            cost: Math.round(cost * 100) / 100,
            revenue: Math.round(revenue * 100) / 100,
            profit: Math.round(profit * 100) / 100,
            margin: Math.round(margin * 100) / 100,
            margin_pct: `${(margin * 100).toFixed(1)}%`,
          };
        });

        const billCost = items.reduce((s, i) => s + i.cost, 0);
        const billRevenue = items.reduce((s, i) => s + i.revenue, 0);
        const billProfit = billRevenue - billCost;
        const billMargin = billRevenue > 0 ? (billProfit / billRevenue * 100).toFixed(1) : 0;
        const statusBadge = b.status === 'confirmed'
          ? '<span class="badge badge-success">Confirmed</span>'
          : '<span class="badge badge-warning">Review</span>';

        detailEl.innerHTML = `
          <div class="card">
            <div class="card-title" style="flex-wrap:wrap;gap:8px">
              <div style="display:flex;align-items:center;gap:10px">
                <h3>Bill #${b.id} &mdash; ${esc(b.supplier_name || '—')}</h3>
                <span class="text-dim text-sm">· ${fmtDate(b.bill_date)} · ${fmtRs(b.written_total || b.computed_total)}</span>
              </div>
              <div style="display:flex;gap:6px;align-items:center">
                ${statusBadge}
                <span class="badge ${b.payment_status === 'credit' ? 'badge-danger' : 'badge-success'}">${esc(b.payment_status)}</span>
                <button class="btn btn-secondary btn-sm" onclick="window.location.hash='/bills/${b.id}'" title="Open bill in editor">
                  ${SVG.edit || ''} Edit Bill
                </button>
              </div>
            </div>

            <!-- Per-bill summary -->
            <div class="grid grid-4 mb-3">
              ${statCard('Cost', fmtRs(billCost), 'chip-success', SVG.wallet)}
              ${statCard('Revenue', fmtRs(billRevenue), 'chip-info', SVG.trendUp)}
              ${statCard('Profit', fmtRs(billProfit), 'chip-success', SVG.trendUp)}
              ${statCard('Margin', `${billMargin}%`, 'chip-primary', SVG.trendUp)}
            </div>

            <!-- Item detail table -->
            <div class="table-wrap"><table class="table">
              <thead><tr>
                <th>Sr</th>
                <th>Item</th>
                <th>Code</th>
                <th>Category</th>
                <th class="table-num">Price</th>
                <th class="table-num">Qty</th>
                <th class="table-num">Line Total</th>
                <th class="table-num">Cost</th>
                <th class="table-num">Profit</th>
                <th>Margin</th>
              </tr></thead>
              <tbody>
                ${items.map(it => `<tr>
                  <td class="text-dim">${it.sr_no}</td>
                  <td>${esc(it.raw)}</td>
                  <td class="text-sm text-dim">${esc(it.item_code || '—')}</td>
                  <td class="text-sm">${esc(it.cat_name || '—')}</td>
                  <td class="table-num">${fmtRs(it.price)}</td>
                  <td class="table-num">${fmt(it.qty)}</td>
                  <td class="table-num">${fmtRs(it.line_total)}</td>
                  <td class="table-num">${fmtRs(it.cost)}</td>
                  <td class="table-num ${it.margin >= 0.3 ? 'text-success' : it.margin >= 0.2 ? 'text-warning' : 'text-danger'}">${fmtRs(it.profit)}</td>
                  <td class="${it.margin >= 0.3 ? 'text-success' : it.margin >= 0.2 ? 'text-warning' : 'text-danger'}">${esc(it.margin_pct)}</td>
                </tr>`).join('')}
              </tbody>
              <tfoot>
                <tr style="font-weight:600;background:var(--bg-elevated)">
                  <td colspan="5">Total</td>
                  <td class="table-num">${fmt(items.reduce((s, i) => s + i.pieces, 0))}</td>
                  <td class="table-num">${fmtRs(items.reduce((s, i) => s + i.line_total, 0))}</td>
                  <td class="table-num">${fmtRs(billCost)}</td>
                  <td class="table-num">${fmtRs(billProfit)}</td>
                  <td>${billMargin}%</td>
                </tr>
              </tfoot>
            </table></div>
          </div>
        `;
      }

      updateSelection();
    } catch (e) {
      $('#bw-out').innerHTML = errorBox(e.message);
    }
  }
});

// P&L, Cash Flow, Balance Sheet moved to reports-financial.js (Phase 9 — file size limit)

// ═══════════════════════════════════════════════════
// TOP ITEMS
// ═══════════════════════════════════════════════════
route('/reports/top-items', async (el, path, q) => {
  // v8.18.5: date range persists across navigation
  const st = initListState('reportsTopItems', q, { start: '', end: '' });
  st.syncUrlIfRestored();
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const initStart = st.val('start') || monthAgo;
  const initEnd = st.val('end') || today;
  st.replace({ start: initStart, end: initEnd });

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.trendUp}</div>
      <div>
        <h2 class="pos-page-header-title">Top-Selling Items</h2>
        <p class="pos-page-header-sub">Best performers by quantity and revenue.</p>
      </div>
      <div class="pos-page-header-actions">
        <input class="input input-sm" id="ti-start" type="date" value="${initStart}">
        <input class="input input-sm" id="ti-end" type="date" value="${initEnd}">
        <button class="btn btn-sm" id="ti-generate-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span>
          Generate
        </button>
      </div>
    </div>
    <div id="ti-out">${skeletonCards(2)}</div>`;

  $('#ti-generate-btn').onclick = () => { st.replace({ start: $('#ti-start').value, end: $('#ti-end').value }); loadReport(); };
  $('#ti-start').onchange = () => { st.replace({ start: $('#ti-start').value }); loadReport(); };
  $('#ti-end').onchange = () => { st.replace({ end: $('#ti-end').value }); loadReport(); };
  await loadReport();

  async function loadReport() {
    const start = $('#ti-start').value, end = $('#ti-end').value;
    try {
      const r = await api(`/api/reports/top-items?start=${start}&end=${end}&limit=20`);
      if (!r.items?.length) {
        $('#ti-out').innerHTML = emptyState('No sales in this period', 'Try a wider date range.', '', '');
        return;
      }
      const totalRevenue = r.items.reduce((s, i) => s + i.total_revenue, 0);
      const totalQty = r.items.reduce((s, i) => s + i.total_qty, 0);
      $('#ti-out').innerHTML = `
        <div class="grid grid-3 mb-4">
          ${statCard('Items Sold', r.items.length, 'chip-primary', SVG.bills)}
          ${statCard('Total Quantity', fmt(totalQty), 'chip-info', SVG.trendUp)}
          ${statCard('Total Revenue', fmtRs(totalRevenue), 'chip-success', SVG.wallet)}
        </div>
        <div class="card">
          <div class="card-title"><h3>Top 20 Items (${start} → ${end})</h3></div>
          <div class="table-wrap"><table>
            <thead><tr><th>Rank</th><th>Item</th><th>Code</th><th class="table-num">Qty Sold</th><th class="table-num">Revenue</th><th class="table-num">Sales</th><th class="table-num">Avg Price</th></tr></thead>
            <tbody>${r.items.map((i, idx) => `<tr>
              <td><span class="badge ${idx < 3 ? 'badge-accent' : ''}">${idx + 1}</span></td>
              <td class="font-semibold">${esc(i.item_name)}</td>
              <td><span class="badge badge-accent">${esc(i.category_code || '—')}</span></td>
              <td class="table-num font-semibold">${fmt(i.total_qty)}</td>
              <td class="table-num text-success font-semibold">${fmtRs(i.total_revenue)}</td>
              <td class="table-num">${i.sale_count}</td>
              <td class="table-num text-dim">${fmtRs(i.avg_price)}</td>
            </tr>`).join('')}</tbody>
          </table></div>
        </div>`;
    } catch (e) {
      $('#ti-out').innerHTML = errorBox(e.message);
    }
  }
});

// ═══════════════════════════════════════════════════
// PEAK HOURS — heatmap of sales by hour
// ═══════════════════════════════════════════════════
route('/reports/peak-hours', async (el, path, q) => {
  // v8.18.5: date range persists across navigation
  const st = initListState('reportsPeakHours', q, { start: '', end: '' });
  st.syncUrlIfRestored();
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const initStart = st.val('start') || monthAgo;
  const initEnd = st.val('end') || today;
  st.replace({ start: initStart, end: initEnd });

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.clock}</div>
      <div>
        <h2 class="pos-page-header-title">Peak Hours</h2>
        <p class="pos-page-header-sub">Sales distribution by hour of day &mdash; schedule staff accordingly.</p>
      </div>
      <div class="pos-page-header-actions">
        <input class="input input-sm" id="ph-start" type="date" value="${initStart}">
        <input class="input input-sm" id="ph-end" type="date" value="${initEnd}">
        <button class="btn btn-sm" id="ph-generate-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span>
          Generate
        </button>
      </div>
    </div>
    <div id="ph-out">${skeletonCards(2)}</div>`;

  $('#ph-generate-btn').onclick = () => { st.replace({ start: $('#ph-start').value, end: $('#ph-end').value }); loadReport(); };
  $('#ph-start').onchange = () => { st.replace({ start: $('#ph-start').value }); loadReport(); };
  $('#ph-end').onchange = () => { st.replace({ end: $('#ph-end').value }); loadReport(); };
  await loadReport();

  async function loadReport() {
    const start = $('#ph-start').value, end = $('#ph-end').value;
    try {
      const r = await api(`/api/reports/peak-hours?start=${start}&end=${end}`);
      if (!r.total_sales || r.total_sales === 0) {
        $('#ph-out').innerHTML = emptyState('No sales in this period', 'Try a wider date range.', '', '');
        return;
      }
      const maxCount = Math.max(...r.by_hour.map(h => h.sale_count), 1);
      $('#ph-out').innerHTML = `
        <div class="grid grid-3 mb-4">
          ${statCard('Total Sales', r.total_sales, 'chip-primary', SVG.bills)}
          ${statCard('Total Revenue', fmtRs(r.total_revenue), 'chip-success', SVG.wallet)}
          ${statCard('Peak Hour', `${String(r.peak_hour).padStart(2, '0')}:00`, 'chip-warning', SVG.clock, `${r.peak_count} sales`)}
        </div>
        <div class="card">
          <h3>Hourly Distribution</h3>
          <p class="text-sm text-dim mt-2">Darker green = more sales. Use this to schedule staff shifts.</p>
          <div class="peak-hours-grid mt-4">
            ${r.by_hour.map(h => {
              const intensity = h.sale_count / maxCount;
              const bg = h.sale_count === 0 ? 'var(--bg-input)' : `rgba(16,185,129,${0.15 + intensity * 0.85})`;
              return `<div class="peak-hour-cell" style="background:${bg}" title="${String(h.hour).padStart(2, '0')}:00 — ${h.sale_count} sales, Rs ${fmt(h.revenue)}">
                <div class="peak-hour-label">${String(h.hour).padStart(2, '0')}</div>
                <div class="peak-hour-count">${h.sale_count}</div>
              </div>`;
            }).join('')}
          </div>
        </div>`;
    } catch (e) {
      $('#ph-out').innerHTML = errorBox(e.message);
    }
  }
});

// Sales Targets moved to reports-financial.js (Phase 9 — file size limit)


// ═══════════════════════════════════════════════════
// MONTHLY CLOSE — snapshot + export (PDF/Excel via the universal
// v8.16.1 auto-injected export buttons, consistent with every other report
// page; the page's own duplicate "Download PDF" button was removed in
// v8.18.12)
// ═══════════════════════════════════════════════════
route('/reports/monthly-close', async (el) => {
  // v8.18.9 FIX ("no data showing"): this page always showed zeros —
  // it read fields the backend never returned (sales_count,
  // total_revenue, total_profit, bills_count, details). The API now
  // returns a real month snapshot (sales + bills + expenses + profit)
  // and this page reads those actual fields.
  // Also fixed: the default month was computed in UTC (toISOString) —
  // in Pakistan (UTC+5) that picked the WRONG month on the 1st (00:00
  // –05:00) and last day (after 19:00). Now uses local time.
  const now = new Date();
  const thisMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.calendar}</div>
      <div>
        <h2 class="pos-page-header-title">Monthly Close</h2>
        <p class="pos-page-header-sub">Snapshot all data for a month and generate a PDF for accounting closure.</p>
      </div>
      <div class="pos-page-header-actions">
        <div class="pos-date-input">
          <span class="pos-date-input-icon">${SVG.calendar}</span>
          <input class="input input-sm" id="mc-month" type="month" value="${thisMonth}">
        </div>
      </div>
    </div>
    <div id="mc-out">${skeletonCards(2)}</div>`;

  $('#mc-month').onchange = loadReport;
  await loadReport();

  function detailRows(details) {
    // Backend convention: numbers = money (Rs), strings = plain labels.
    return Object.entries(details).map(([k, v]) =>
      `<div class="stat-row"><span>${esc(k.replace(/_/g, ' '))}</span><span>${
        typeof v === 'number' ? fmtRs(v) : esc(String(v))}</span></div>`).join('');
  }

  async function loadReport() {
    const monthVal = $('#mc-month').value;
    if (!monthVal) {
      $('#mc-out').innerHTML = errorBox('Pick a month to load the close snapshot.');
      return;
    }
    const [year, month] = monthVal.split('-').map(Number);
    try {
      const r = await api(`/api/reports/monthly-close?year=${year}&month=${month}`);

      // Fallbacks keep the page alive against old field names.
      const salesCount = r.sales_count ?? 0;
      const revenue = r.total_revenue ?? 0;
      const netProfit = r.net_profit ?? r.total_profit ?? 0;
      const billsCount = r.bills_count ?? r.total_bills ?? 0;
      const grossProfit = r.gross_profit ?? 0;
      const opExp = r.operating_expenses ?? 0;
      // v8.18.14: extra (non-POS) sales — own line so it's differentiable
      const extraIncome = r.extra_sales_income ?? 0;
      const extraCount = r.extra_sales_count ?? 0;

      const hasData = (salesCount + billsCount + (r.refunded_sales_count || 0)) > 0
        || revenue > 0 || (r.total_spent || 0) > 0 || opExp > 0 || extraIncome > 0;

      if (!hasData) {
        $('#mc-out').innerHTML = emptyState(
          `Nothing recorded for ${esc(monthVal)}`,
          'No sales, purchase bills, or expenses exist for this month. Pick a different month above.');
        return;
      }

      const salesByCat = r.sales_by_category || [];
      const audit = (r.audit && !r.audit.error)
        ? `<div class="stat-row"><span>Month-End Audit</span><span>${fmt(r.audit.findings_count)} findings (${
            fmt(r.audit.critical_count)} critical / ${fmt(r.audit.warning_count)} warnings)</span></div>`
        : '';

      $('#mc-out').innerHTML = `
        <div class="grid grid-4 mb-4">
          ${statCard('POS Sales', fmt(salesCount), 'chip-primary', SVG.bills)}
          ${statCard('Revenue', fmtRs(revenue), 'chip-success', SVG.wallet,
                     extraIncome > 0 ? `+ Extra Sales: ${fmtRs(extraIncome)} (${fmt(extraCount)})` : `Credit sales: ${fmtRs(r.sales_credit_total || 0)}`)}
          ${statCard('Net Profit', fmtRs(netProfit), netProfit >= 0 ? 'chip-success' : 'chip-danger', SVG.trendUp,
                     extraIncome > 0 ? `Gross: ${fmtRs(grossProfit)} + Extra: ${fmtRs(extraIncome)} − Op Ex: ${fmtRs(opExp)}` : `Gross: ${fmtRs(grossProfit)} − Op Ex: ${fmtRs(opExp)}`)}
          ${statCard('Bills Processed', fmt(billsCount), 'chip-warning', SVG.file,
                     `Purchases: ${fmtRs(r.total_spent || 0)}`)}
        </div>
        <div class="grid grid-2 mb-4">
          <div class="card">
            <h3>Sales &amp; Profit — ${esc(monthVal)}</h3>
            <div class="stat-list mt-3">
              ${detailRows({
                'POS Sales (invoices)': String(salesCount),
                'Sales Revenue (net)': revenue,
                'Discounts Given': r.discounts_given || 0,
                'Sales on Credit (udhaar)': r.sales_credit_total || 0,
                'Refunded Sales': `${fmt(r.refunded_sales_count || 0)} / ${fmtRs(r.refunded_total || 0)}`,
                'Extra Sales (non-POS — cartons, raddi)': extraIncome > 0 ? `${fmt(extraCount)} / ${fmtRs(extraIncome)}` : '—',
                'Cost of Goods Sold': r.cost_of_goods ?? 0,
                'Gross Profit (POS)': grossProfit,
                'Operating Expenses': opExp,
                'Net Profit (gross + extra − op. expenses)': netProfit,
                'Owner Draws (not expense)': r.owner_draws || 0,
              })}
              ${audit}
            </div>
          </div>
          <div class="card">
            <h3>Purchases (Bills) — ${esc(monthVal)}</h3>
            <div class="stat-list mt-3">
              ${detailRows({
                'Purchase Bills': String(billsCount),
                'Purchases Total': r.total_spent || 0,
                'Paid to Suppliers': r.total_paid || 0,
                'Credit from Suppliers': r.total_credit || 0,
                'Suppliers This Month': String(r.supplier_count || 0),
              })}
            </div>
            ${(r.suppliers || []).length ? `
              <div style="margin-top:12px">
                <p class="text-dim text-sm" style="margin:0 0 6px">Suppliers:</p>
                <div style="display:flex;flex-wrap:wrap;gap:6px">
                  ${(r.suppliers).map(s => `<span class="chip chip-secondary" style="font-size:12px">${esc(s)}</span>`).join('')}
                </div>
              </div>` : ''}
          </div>
        </div>
        ${salesByCat.length ? `
        <div class="card">
          <h3>Sales by Category — ${esc(monthVal)}</h3>
          <div class="table-wrap"><table class="table">
            <thead><tr>
              <th>Category</th><th class="table-num">Lines</th><th class="table-num">Qty Sold</th>
              <th class="table-num">Revenue</th><th class="table-num">COGS</th>
              <th class="table-num">Gross Profit</th>
            </tr></thead>
            <tbody>
              ${salesByCat.map(c => {
                const gp = (c.revenue || 0) - (c.cost || 0);
                return `<tr>
                  <td><strong>${esc(c.category || 'Uncategorized')}</strong></td>
                  <td class="table-num">${fmt(c.line_count || 0)}</td>
                  <td class="table-num">${fmt(c.qty_sold || 0)}</td>
                  <td class="table-num">${fmtRs(c.revenue || 0)}</td>
                  <td class="table-num">${fmtRs(c.cost || 0)}</td>
                  <td class="table-num ${gp >= 0 ? 'text-success' : 'text-danger'}">${fmtRs(gp)}</td>
                </tr>`;
              }).join('')}
            </tbody>
          </table></div>
        </div>` : ''}`;
    } catch (e) {
      $('#mc-out').innerHTML = errorBox(e.message);
    }
  }
});

// ═══════════════════════════════════════════════════
// EXPORT CENTER — bills/insights/custom CSV downloads
// ═══════════════════════════════════════════════════
route('/reports/export', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.download}</div>
      <div>
        <h2 class="pos-page-header-title">Export Center</h2>
        <p class="pos-page-header-sub">Download bills, sales, and insights data in Excel or CSV format.</p>
      </div>
    </div>

    <div class="grid grid-3" style="gap:16px">
      <div class="card" style="padding:24px;display:flex;flex-direction:column;gap:12px">
        <div style="width:48px;height:48px;background:var(--success-soft);color:var(--success-text);border-radius:12px;display:flex;align-items:center;justify-content:center">
          <span style="display:inline-flex;width:24px;height:24px">${SVG.file}</span>
        </div>
        <div>
          <h3 style="margin:0">Bills Excel</h3>
          <p class="text-dim text-sm" style="margin:4px 0 0">All supplier bills with line items, prices, and payment status.</p>
        </div>
        <button class="btn btn-primary" data-export="bills-xlsx" style="margin-top:auto">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
          Download .xlsx
        </button>
      </div>
      <div class="card" style="padding:24px;display:flex;flex-direction:column;gap:12px">
        <div style="width:48px;height:48px;background:var(--primary-soft);color:var(--primary-text);border-radius:12px;display:flex;align-items:center;justify-content:center">
          <span style="display:inline-flex;width:24px;height:24px">${SVG.file}</span>
        </div>
        <div>
          <h3 style="margin:0">Insights Excel</h3>
          <p class="text-dim text-sm" style="margin:4px 0 0">Customers, suppliers, sales, and inventory in one workbook.</p>
        </div>
        <button class="btn btn-primary" data-export="insights-xlsx" style="margin-top:auto">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
          Download .xlsx
        </button>
      </div>
      <div class="card" style="padding:24px;display:flex;flex-direction:column;gap:12px">
        <div style="width:48px;height:48px;background:var(--warning-soft);color:var(--warning-text);border-radius:12px;display:flex;align-items:center;justify-content:center">
          <span style="display:inline-flex;width:24px;height:24px">${SVG.file}</span>
        </div>
        <div>
          <h3 style="margin:0">Bills CSV</h3>
          <p class="text-dim text-sm" style="margin:4px 0 0">Lightweight CSV for accounting software imports.</p>
        </div>
        <button class="btn btn-primary" data-export="bills-csv" style="margin-top:auto">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
          Download .csv
        </button>
      </div>
    </div>

    <div class="card mt-4" style="padding:20px">
      <h3 style="margin:0 0 12px">Export Tips</h3>
      <div class="grid grid-3" style="gap:16px">
        <div style="display:flex;gap:10px;align-items:flex-start">
          <span style="display:inline-flex;width:16px;height:16px;color:var(--success-text);flex-shrink:0;margin-top:2px">${SVG.check}</span>
          <div>
            <strong class="text-sm">Excel Format</strong>
            <p class="text-dim text-sm" style="margin:2px 0 0">Use <code>.xlsx</code> for human review — supports formatting, multiple sheets.</p>
          </div>
        </div>
        <div style="display:flex;gap:10px;align-items:flex-start">
          <span style="display:inline-flex;width:16px;height:16px;color:var(--success-text);flex-shrink:0;margin-top:2px">${SVG.check}</span>
          <div>
            <strong class="text-sm">CSV Format</strong>
            <p class="text-dim text-sm" style="margin:2px 0 0">Use <code>.csv</code> for accounting software — smaller file, universal.</p>
          </div>
        </div>
        <div style="display:flex;gap:10px;align-items:flex-start">
          <span style="display:inline-flex;width:16px;height:16px;color:var(--warning-text);flex-shrink:0;margin-top:2px">${SVG.alert}</span>
          <div>
            <strong class="text-sm">All Data Included</strong>
            <p class="text-dim text-sm" style="margin:2px 0 0">Exports include all records — filter in Excel by date for specific periods.</p>
          </div>
        </div>
      </div>
    </div>`;

  $$('[data-export]').forEach(btn => {
    btn.onclick = () => {
      const type = btn.dataset.export;
      const url = type === 'bills-xlsx' ? '/api/export/bills.xlsx'
                : type === 'insights-xlsx' ? '/api/export/insights.xlsx'
                : '/api/export.csv';
      toast('Preparing download...', 'info');
      location.href = url;
    };
  });
});


// ════════════════════════════════════════════════════════════════════════════════
// v8.7 — NEW REPORT: Profit Analysis (by category or by month)
// ════════════════════════════════════════════════════════════════════════════════
route('/reports/profit-analysis', async (el) => {
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-success">${SVG.trendUp}</div>
      <div>
        <h2 class="pos-page-header-title">Profit Analysis</h2>
        <p class="pos-page-header-sub">Date-range profit breakdown by category or month. Excludes refunded sales.</p>
      </div>
      <div class="pos-page-header-actions">
        <input class="input input-sm" id="pa-start" type="date" value="${monthAgo}">
        <input class="input input-sm" id="pa-end" type="date" value="${today}">
        <select class="input input-sm" id="pa-group">
          <option value="category" selected>By Category</option>
          <option value="month">By Month</option>
        </select>
        <button class="btn btn-sm" id="pa-generate-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span>
          Generate
        </button>
      </div>
    </div>
    <div id="pa-out">${skeletonCards(2)}</div>`;

  $('#pa-generate-btn').onclick = loadReport;
  $('#pa-start').onchange = loadReport;
  $('#pa-end').onchange = loadReport;
  $('#pa-group').onchange = loadReport;
  await loadReport();

  async function loadReport() {
    const start = $('#pa-start').value, end = $('#pa-end').value;
    const groupBy = $('#pa-group').value;
    try {
      const r = await api(`/api/reports/profit-analysis?start=${start}&end=${end}&group_by=${groupBy}`);
      if (r.error) {
        $('#pa-out').innerHTML = errorBox(r.error);
        return;
      }
      const t = r.totals || {};
      const rows = groupBy === 'month' ? (r.months || []) : (r.categories || []);
      // v8.18.14: extra (non-POS) sales income — separate from POS revenue
      const extraIncome = t.extra_sales_income || r.extra_sales_income || 0;

      if (!rows.length) {
        $('#pa-out').innerHTML = emptyState('No sales in this period', 'Try a wider date range.', '', '');
        return;
      }

      $('#pa-out').innerHTML = `
        <div class="grid grid-4 mb-4">
          ${statCard('Revenue', fmtRs(t.revenue), 'chip-info', SVG.trendUp)}
          ${statCard('COGS', fmtRs(t.cogs), 'chip-warning', SVG.wallet)}
          ${statCard('Gross Profit', fmtRs(t.gross_profit), 'chip-success', SVG.trendUp)}
          ${statCard('Margin', `${t.margin_pct}%`, 'chip-primary', SVG.trendUp,
                     groupBy === 'month' ? (extraIncome > 0 ? `+ Extra Sales: ${fmtRs(extraIncome)}` : `Op Expenses: ${fmtRs(t.operating_expenses)}`) : (extraIncome > 0 ? `+ Extra Sales: ${fmtRs(extraIncome)}` : `Qty Sold: ${fmt(t.qty_sold)}`))}
        </div>

        ${extraIncome > 0 ? `
        <div class="card" style="padding:12px;margin-bottom:12px;background:var(--success-soft, #f0fdf4);border-left:3px solid var(--success, #16a34a)">
          <strong style="color:var(--success-text, #16a34a)">Extra Sales (non-POS): ${fmtRs(extraIncome)}</strong>
          <span class="text-dim" style="font-size:12px"> — income from non-stock items sold outside the POS (cartons, raddi/scrap...). No COGS, kept separate from POS category/month revenue above; ${groupBy === 'month' ? 'included in Operating Profit' : 'NOT included in the category totals below'}. <a href="#/bills/extra-sales" style="text-decoration:underline">Manage Extra Sales →</a></span>
        </div>` : ''}

        <div class="card">
          ${groupBy !== 'month' ? `
            <div class="card" style="padding:12px;background:var(--bg-2,#F1F5F9);margin-bottom:12px;border-left:3px solid var(--info,#3b82f6);font-size:12px">
              <strong style="color:var(--info-text,#2563EB)">Why two margin columns?</strong>
              <ul style="margin:4px 0 0 18px;padding:0;line-height:1.6">
                <li><strong>Hist. Margin %</strong> <span style="color:var(--info-text,#2563EB);font-weight:600">(changes with date range)</span> —
                  actual margin realized on past sales = (Revenue − COGS) / Revenue.
                  COGS uses the cost recorded at the time of each sale within the selected date range.</li>
                <li><strong>Curr. Margin %</strong> <span style="color:var(--text-dim);font-weight:600">(stays the same — independent of date range)</span> —
                  what margin you'd make on the NEXT sale = (Sell Price − Current Avg Cost) / Sell Price.
                  Always uses today's current avg cost, regardless of date range. Matches Store Profit dashboard.</li>
                <li><strong>Cost Δ</strong> = Curr. Avg Cost − Avg Hist. Cost.
                  <span style="color:var(--danger-text,#dc2626)">Positive (red)</span> means cost has gone UP since the sale.
                  <span style="color:var(--success-text,#16a34a)">Negative (green)</span> means cost went DOWN.</li>
              </ul>
            </div>
          ` : ''}
          <div class="card-title" style="display:flex;justify-content:space-between;align-items:center">
            <h3>Profit ${groupBy === 'month' ? 'by Month' : 'by Category'} (${start} → ${end})</h3>
            <div style="display:flex;gap:6px">
              <button class="btn btn-secondary btn-sm" id="pa-export-pdf" title="Download as PDF">
                <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
                PDF
              </button>
              <button class="btn btn-secondary btn-sm" id="pa-export-excel" title="Download as Excel">
                <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
                Excel
              </button>
              <button class="btn btn-secondary btn-sm" id="pa-export" title="Download as CSV">
                <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
                CSV
              </button>
            </div>
          </div>
          <div class="table-wrap"><table class="table">
            ${groupBy === 'month' ? `
              <thead><tr>
                <th>Month</th><th class="table-num">Qty Sold</th>
                <th class="table-num">Revenue</th><th class="table-num">COGS</th>
                <th class="table-num">Gross Profit</th><th class="table-num">Margin %</th>
                <th class="table-num" title="Extra (non-POS) sales income — cartons, raddi... No COGS, separate from POS revenue." style="color:var(--success-text, #16a34a)">Extra Sales (non-POS)</th>
                <th class="table-num">Op Expenses</th><th class="table-num">Op Profit</th>
              </tr></thead>
              <tbody>
                ${rows.map(m => `<tr>
                  <td><strong>${m.month}</strong></td>
                  <td class="table-num">${fmt(m.qty_sold)}</td>
                  <td class="table-num">${fmtRs(m.revenue)}</td>
                  <td class="table-num">${fmtRs(m.cogs)}</td>
                  <td class="table-num ${m.gross_profit >= 0 ? 'text-success' : 'text-danger'}">${fmtRs(m.gross_profit)}</td>
                  <td class="table-num ${m.margin_pct >= 30 ? 'text-success' : m.margin_pct >= 20 ? 'text-warning' : 'text-danger'}">${m.margin_pct}%</td>
                  <td class="table-num text-success">${(m.extra_sales_income || 0) > 0 ? `+ ${fmtRs(m.extra_sales_income)}` : '—'}</td>
                  <td class="table-num">${fmtRs(m.operating_expenses)}</td>
                  <td class="table-num ${m.operating_profit >= 0 ? 'text-success' : 'text-danger'}">${fmtRs(m.operating_profit)}</td>
                </tr>`).join('')}
              </tbody>
              <tfoot><tr style="font-weight:600;background:var(--bg-elevated)">
                <td>TOTAL</td>
                <td class="table-num">${fmt(t.qty_sold)}</td>
                <td class="table-num">${fmtRs(t.revenue)}</td>
                <td class="table-num">${fmtRs(t.cogs)}</td>
                <td class="table-num">${fmtRs(t.gross_profit)}</td>
                <td class="table-num">${t.margin_pct}%</td>
                <td class="table-num text-success">${extraIncome > 0 ? `+ ${fmtRs(extraIncome)}` : '—'}</td>
                <td class="table-num">${fmtRs(t.operating_expenses)}</td>
                <td class="table-num">${fmtRs(t.operating_profit)}</td>
              </tr></tfoot>
            ` : `
              <thead><tr>
                <th>Code</th><th>Category</th>
                <th class="table-num">Qty Sold</th>
                <th class="table-num">Revenue</th>
                <th class="table-num">COGS</th>
                <th class="table-num">Gross Profit</th>
                <th class="table-num" title="Historical margin = (Revenue - COGS) / Revenue * 100, using the cost recorded at time of sale. CHANGES with date range.">
                  <span style="color:var(--info-text,#2563EB)">Hist. Margin % ↻</span>
                </th>
                <th class="table-num" title="Current margin = (Sell Price - Current Avg Cost) / Sell Price * 100. STAYS THE SAME regardless of date range. Matches Store Profit dashboard.">
                  <span style="color:var(--text-dim)">Curr. Margin % =</span>
                </th>
                <th class="table-num" title="Avg cost per unit actually used for past sales in this date range = COGS / Qty Sold. CHANGES with date range.">
                  <span style="color:var(--info-text,#2563EB)">Avg Hist. Cost ↻</span>
                </th>
                <th class="table-num" title="Current running weighted-avg cost = cost of all confirmed purchase bills / qty purchased, less qty sold. STAYS THE SAME — matches Store Profit.">
                  <span style="color:var(--text-dim)">Curr. Avg Cost =</span>
                </th>
                <th class="table-num" title="Cost change since the period = Current Avg Cost - Avg Historical Cost. Positive means cost went UP since the sale; negative means cost went DOWN.">
                  Cost Δ
                </th>
                <th class="table-num" title="Profit per unit (historical) = Gross Profit / Qty Sold. CHANGES with date range.">
                  <span style="color:var(--info-text,#2563EB)">Profit/Unit ↻</span>
                </th>
                <th class="table-num" title="Current profit per unit = Sell Price - Current Avg Cost. STAYS THE SAME — forward-looking.">
                  <span style="color:var(--text-dim)">Curr. Profit/Unit =</span>
                </th>
                <th class="table-num" title="Markup % = (Sell - Cost) / Cost * 100. Different from margin % which divides by Sell. CHANGES with date range.">
                  <span style="color:var(--info-text,#2563EB)">Markup % ↻</span>
                </th>
                <th class="table-num" title="Current markup % = (Sell Price - Current Avg Cost) / Current Avg Cost * 100. STAYS THE SAME.">
                  <span style="color:var(--text-dim)">Curr. Markup % =</span>
                </th>
                <th class="table-num">Sales</th>
              </tr></thead>
              <tbody>
                ${rows.map(c => {
                  const costChangeColor = c.cost_change > 0.01 ? 'text-danger' :
                                          c.cost_change < -0.01 ? 'text-success' : 'text-dim';
                  const costChangeStr = c.cost_change > 0 ? `+Rs ${c.cost_change.toFixed(2)}` :
                                        c.cost_change < 0 ? `-Rs ${Math.abs(c.cost_change).toFixed(2)}` :
                                        'Rs 0.00';
                  const marginDiff = (c.current_margin_pct - c.margin_pct).toFixed(2);
                  const marginDiffStr = marginDiff > 0 ? `(+${marginDiff}%)` :
                                       marginDiff < 0 ? `(${marginDiff}%)` : '';
                  return `<tr>
                    <td><span class="badge badge-accent">${esc(c.code)}</span></td>
                    <td class="font-semibold">${esc(c.name)}</td>
                    <td class="table-num">${fmt(c.qty_sold)}</td>
                    <td class="table-num">${fmtRs(c.revenue)}</td>
                    <td class="table-num">${fmtRs(c.cogs)}</td>
                    <td class="table-num ${c.gross_profit >= 0 ? 'text-success' : 'text-danger'}">${fmtRs(c.gross_profit)}</td>
                    <td class="table-num ${c.margin_pct >= 30 ? 'text-success' : c.margin_pct >= 20 ? 'text-warning' : 'text-danger'}" title="Historical margin (margin you actually realized)">
                      ${c.margin_pct}%
                    </td>
                    <td class="table-num ${c.current_margin_pct >= 30 ? 'text-success' : c.current_margin_pct >= 20 ? 'text-warning' : 'text-danger'}" title="Current margin (matches Store Profit dashboard)">
                      ${c.current_margin_pct}% <span class="text-xs text-dim">${marginDiffStr}</span>
                    </td>
                    <td class="table-num text-dim" title="Avg historical cost per unit">${fmtRs(c.avg_historical_cost)}</td>
                    <td class="table-num" title="Current running avg cost (from Store Profit)">${fmtRs(c.current_avg_cost)}</td>
                    <td class="table-num ${costChangeColor}" title="Cost change since the period">${costChangeStr}</td>
                    <td class="table-num text-success" title="Profit per unit (historical)">${fmtRs(c.profit_per_unit)}</td>
                    <td class="table-num text-success" title="Current profit per unit">${fmtRs(c.current_profit_per_unit)}</td>
                    <td class="table-num text-dim" title="Markup % = (Sell-Cost)/Cost*100">${c.markup_pct}%</td>
                    <td class="table-num text-dim" title="Current markup %">${c.current_markup_pct}%</td>
                    <td class="table-num">${c.sale_count}</td>
                  </tr>`;
                }).join('')}
              </tbody>
              <tfoot><tr style="font-weight:600;background:var(--bg-elevated)">
                <td colspan="2">TOTAL</td>
                <td class="table-num">${fmt(t.qty_sold)}</td>
                <td class="table-num">${fmtRs(t.revenue)}</td>
                <td class="table-num">${fmtRs(t.cogs)}</td>
                <td class="table-num">${fmtRs(t.gross_profit)}</td>
                <td class="table-num">${t.margin_pct}%</td>
                <td class="table-num text-dim" colspan="8" title="Per-category metrics are shown above; this row only shows totals.">—</td>
                <td class="table-num"></td>
              </tr></tfoot>
            `}
          </table></div>
        </div>`;

      $('#pa-export-pdf').onclick = () => {
        const s = $('#pa-start').value, e = $('#pa-end').value, g = $('#pa-group').value;
        window.open(`/api/reports/profit-analysis/export?format=pdf&start=${s}&end=${e}&group_by=${g}`, '_blank');
      };
      $('#pa-export-excel').onclick = () => {
        const s = $('#pa-start').value, e = $('#pa-end').value, g = $('#pa-group').value;
        window.open(`/api/reports/profit-analysis/export?format=excel&start=${s}&end=${e}&group_by=${g}`, '_blank');
      };
      $('#pa-export').onclick = () => {
        const s = $('#pa-start').value, e = $('#pa-end').value, g = $('#pa-group').value;
        location.href = `/api/reports/profit-analysis/export?start=${s}&end=${e}&group_by=${g}`;
      };
    } catch (e) {
      $('#pa-out').innerHTML = errorBox(e.message);
    }
  }
});


// ════════════════════════════════════════════════════════════════════════════════
// v8.7 — NEW REPORT: Sold Stock (by category DEFAULT, by item secondary)
// ════════════════════════════════════════════════════════════════════════════════
route('/reports/sold-stock', async (el) => {
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-info">${SVG.bills}</div>
      <div>
        <h2 class="pos-page-header-title">Sold Stock Report</h2>
        <p class="pos-page-header-sub">What sold, how much, and at what margin. <strong>By Category</strong> is the default view (reliable).</p>
      </div>
      <div class="pos-page-header-actions">
        <input class="input input-sm" id="ss-start" type="date" value="${monthAgo}">
        <input class="input input-sm" id="ss-end" type="date" value="${today}">
        <select class="input input-sm" id="ss-group">
          <option value="category" selected>By Category</option>
          <option value="item">By Item</option>
        </select>
        <button class="btn btn-sm" id="ss-generate-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span>
          Generate
        </button>
      </div>
    </div>
    <div id="ss-out">${skeletonCards(2)}</div>`;

  $('#ss-generate-btn').onclick = loadReport;
  $('#ss-start').onchange = loadReport;
  $('#ss-end').onchange = loadReport;
  $('#ss-group').onchange = loadReport;
  await loadReport();

  async function loadReport() {
    const start = $('#ss-start').value, end = $('#ss-end').value;
    const groupBy = $('#ss-group').value;
    try {
      const r = await api(`/api/reports/sold-stock?start=${start}&end=${end}&group_by=${groupBy}`);
      if (r.error) {
        $('#ss-out').innerHTML = errorBox(r.error);
        return;
      }
      const t = r.totals || {};
      const rows = groupBy === 'item' ? (r.items || []) : (r.categories || []);

      if (!rows.length) {
        $('#ss-out').innerHTML = emptyState('No sales in this period', 'Try a wider date range.', '', '');
        return;
      }

      $('#ss-out').innerHTML = `
        <div class="grid grid-4 mb-4">
          ${statCard('Qty Sold', fmt(t.qty_sold), 'chip-primary', SVG.trendUp)}
          ${statCard('Revenue', fmtRs(t.revenue), 'chip-info', SVG.trendUp)}
          ${statCard('COGS', fmtRs(t.cogs), 'chip-warning', SVG.wallet)}
          ${statCard('Gross Profit', fmtRs(t.gross_profit), 'chip-success', SVG.trendUp,
                     `Margin ${t.margin_pct}%`)}
        </div>

        ${groupBy === 'item' ? `
          <div class="card mb-3" style="padding:12px 16px;background:var(--bg-elevated)">
            <strong>Note:</strong> Item names are AI-extracted free text — expect some fragmentation
            (e.g. "Toy Car Red" vs "Red Toy Car"). Case-insensitive grouping is applied.
            Switch to <em>By Category</em> for a cleaner view.
          </div>
        ` : ''}

        <div class="card">
          <div class="card-title" style="display:flex;justify-content:space-between;align-items:center">
            <h3>Sold Stock ${groupBy === 'item' ? 'by Item' : 'by Category'} (${start} → ${end})</h3>
            <div style="display:flex;gap:6px">
              <button class="btn btn-secondary btn-sm" id="ss-export-pdf" title="Download as PDF">
                <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
                PDF
              </button>
              <button class="btn btn-secondary btn-sm" id="ss-export-excel" title="Download as Excel">
                <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
                Excel
              </button>
              <button class="btn btn-secondary btn-sm" id="ss-export" title="Download as CSV">
                <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
                CSV
              </button>
            </div>
          </div>
          <div class="table-wrap"><table class="table">
            ${groupBy === 'item' ? `
              <thead><tr>
                <th>Item</th><th>Category</th>
                <th class="table-num">Qty Sold</th>
                <th class="table-num">Revenue</th><th class="table-num">COGS</th>
                <th class="table-num">Gross Profit</th><th class="table-num">Margin %</th>
                <th class="table-num">Avg Price</th><th class="table-num">Avg Cost</th>
                <th class="table-num">Sales</th><th>Last Sold</th>
              </tr></thead>
              <tbody>
                ${rows.map(it => `<tr>
                  <td class="font-semibold">${esc(it.item_name)}</td>
                  <td><span class="badge badge-accent">${esc(it.cat_code)}</span> ${esc(it.cat_name)}</td>
                  <td class="table-num">${fmt(it.qty_sold)}</td>
                  <td class="table-num">${fmtRs(it.revenue)}</td>
                  <td class="table-num">${fmtRs(it.cogs)}</td>
                  <td class="table-num ${it.gross_profit >= 0 ? 'text-success' : 'text-danger'}">${fmtRs(it.gross_profit)}</td>
                  <td class="table-num ${it.margin_pct >= 30 ? 'text-success' : it.margin_pct >= 20 ? 'text-warning' : 'text-danger'}">${it.margin_pct}%</td>
                  <td class="table-num text-dim">${fmtRs(it.avg_selling_price)}</td>
                  <td class="table-num text-dim">${fmtRs(it.avg_cost_price)}</td>
                  <td class="table-num">${it.sale_count}</td>
                  <td class="text-sm text-dim">${fmtDate(it.last_sold)}</td>
                </tr>`).join('')}
              </tbody>
              <tfoot><tr style="font-weight:600;background:var(--bg-elevated)">
                <td colspan="2">TOTAL (${t.distinct_items || 0} items)</td>
                <td class="table-num">${fmt(t.qty_sold)}</td>
                <td class="table-num">${fmtRs(t.revenue)}</td>
                <td class="table-num">${fmtRs(t.cogs)}</td>
                <td class="table-num">${fmtRs(t.gross_profit)}</td>
                <td class="table-num">${t.margin_pct}%</td>
                <td class="table-num"></td>
                <td class="table-num"></td>
                <td class="table-num"></td>
                <td></td>
              </tr></tfoot>
            ` : `
              <thead><tr>
                <th>Code</th><th>Category</th>
                <th class="table-num">Qty Sold</th>
                <th class="table-num">Revenue</th><th class="table-num">COGS</th>
                <th class="table-num">Gross Profit</th><th class="table-num">Margin %</th>
                <th class="table-num">Avg Price</th><th class="table-num">Sales</th>
                <th class="table-num">Distinct Items</th>
              </tr></thead>
              <tbody>
                ${rows.map(c => `<tr>
                  <td><span class="badge badge-accent">${esc(c.code)}</span></td>
                  <td class="font-semibold">${esc(c.name)}</td>
                  <td class="table-num">${fmt(c.qty_sold)}</td>
                  <td class="table-num">${fmtRs(c.revenue)}</td>
                  <td class="table-num">${fmtRs(c.cogs)}</td>
                  <td class="table-num ${c.gross_profit >= 0 ? 'text-success' : 'text-danger'}">${fmtRs(c.gross_profit)}</td>
                  <td class="table-num ${c.margin_pct >= 30 ? 'text-success' : c.margin_pct >= 20 ? 'text-warning' : 'text-danger'}">${c.margin_pct}%</td>
                  <td class="table-num text-dim">${fmtRs(c.avg_selling_price)}</td>
                  <td class="table-num">${c.sale_count}</td>
                  <td class="table-num text-dim">${c.distinct_items}</td>
                </tr>`).join('')}
              </tbody>
              <tfoot><tr style="font-weight:600;background:var(--bg-elevated)">
                <td colspan="2">TOTAL (${t.distinct_categories || 0} categories)</td>
                <td class="table-num">${fmt(t.qty_sold)}</td>
                <td class="table-num">${fmtRs(t.revenue)}</td>
                <td class="table-num">${fmtRs(t.cogs)}</td>
                <td class="table-num">${fmtRs(t.gross_profit)}</td>
                <td class="table-num">${t.margin_pct}%</td>
                <td class="table-num"></td>
                <td class="table-num"></td>
                <td class="table-num"></td>
              </tr></tfoot>
            `}
          </table></div>
        </div>`;

      $('#ss-export-pdf').onclick = () => {
        const s = $('#ss-start').value, e = $('#ss-end').value, g = $('#ss-group').value;
        window.open(`/api/reports/sold-stock/export?format=pdf&start=${s}&end=${e}&group_by=${g}`, '_blank');
      };
      $('#ss-export-excel').onclick = () => {
        const s = $('#ss-start').value, e = $('#ss-end').value, g = $('#ss-group').value;
        window.open(`/api/reports/sold-stock/export?format=excel&start=${s}&end=${e}&group_by=${g}`, '_blank');
      };
      $('#ss-export').onclick = () => {
        const s = $('#ss-start').value, e = $('#ss-end').value, g = $('#ss-group').value;
        location.href = `/api/reports/sold-stock/export?start=${s}&end=${e}&group_by=${g}`;
      };
    } catch (e) {
      $('#ss-out').innerHTML = errorBox(e.message);
    }
  }
});


// ═══════════════════════════════════════════════════════════════════════════════
// v8.16.1: Universal export helper — adds PDF + Excel buttons to any report page
// ═══════════════════════════════════════════════════════════════════════════════

export function reportExportButtons(reportName, params = {}) {
  const q = new URLSearchParams(params).toString();
  const base = `/api/reports/${reportName}/export`;
  const queryStr = q ? `&${q}` : '';
  return `
    <div class="flex gap-2">
      <button class="btn btn-secondary btn-sm" onclick="window.open('${base}?format=pdf${queryStr}', '_blank')">
        <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
        PDF
      </button>
      <button class="btn btn-secondary btn-sm" onclick="window.open('${base}?format=excel${queryStr}', '_blank')">
        <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
        Excel
      </button>
    </div>`;
}

// v8.16.2: Add export buttons to ALL report pages on load
// Comprehensive list of ALL report routes + their export names
const REPORT_EXPORT_MAP = {
  'margins': 'margins',
  'monthly-profit': 'monthly',
  'ytd': 'ytd',
  'earnings': 'earnings',
  'actual-earnings': 'earnings',
  'profit-analysis': null,  // v8.16.14: has its own native PDF/Excel/CSV buttons that read date inputs
  'sold-stock': null,       // v8.16.14: same — has its own export button with correct date params
  'cash-buckets': 'cash-buckets',
  'daily-stock': 'daily-stock',
  'pnl': 'pnl',
  'cash-flow': 'cash-flow',
  'balance-sheet': 'balance-sheet',
  'top-items': 'top-items',
  'peak-hours': 'peak-hours',
  'ar-aging': 'ar-aging',
  'ap-aging': 'ap-aging',
  'inventory-turnover': 'inventory-turnover',
  'gmroi': 'gmroi',
  'sell-through': 'sell-through',
  'shrinkage': 'shrinkage',
  'sales-by-customer': 'sales-by-customer',
  'sales-by-employee': 'sales-by-employee',
  'atv-basket': 'atv-basket',
  'retention': 'retention',
  'supplier-performance': 'supplier-performance',
  'yoy-compare': 'yoy-compare',
  'supplier-comparison': 'supplier-comparison',
  'category-cost-trends': 'category-cost-trends',
  'stock-writeoffs': 'stock-writeoffs',
  'expenses': 'expenses',
  'store-profit': 'store-profit',
  'overview': 'overview',
  'billwise': 'billwise',
  'audit': 'audit',
  'suspicious': 'suspicious',
  'targets': 'targets',
  'monthly-close': 'monthly-close',
  'export': null,  // Already has export buttons
};

function _collectExportParams() {
  // v8.16.15: Read date/month/group_by params from the page's INPUT FIELDS,
  // not from the URL hash (which is always empty because pages use JS inputs
  // that don't update the URL). This fixes the systemic bug where auto-injected
  // PDF/Excel buttons exported with empty/wrong date ranges.
  const params = [];

  // Strategy: scan the page for common date input patterns.
  // Pages use IDs like: pa-start, pa-end, ss-start, ss-end, r-start, r-end,
  // bw-start, bw-end, ti-start, ti-end, ph-start, ph-end,
  // pnl-month, cf-month, mp-month, ae-month, bs-date, cb-date, mc-month, etc.

  // 1. Find date-range inputs (start + end)
  const startInput = document.querySelector('[id$="-start"]') || document.querySelector('[id$="-from"]');
  const endInput = document.querySelector('[id$="-end"]') || document.querySelector('[id$="-to"]');
  if (startInput && startInput.value) params.push(`start=${startInput.value}`);
  if (endInput && endInput.value) params.push(`end=${endInput.value}`);

  // 2. Find month inputs (type="month")
  const monthInputs = document.querySelectorAll('input[type="month"]');
  for (const inp of monthInputs) {
    if (inp.value) {
      params.push(`month=${inp.value}`);
      break;  // only use the first month input
    }
  }
  // Also check for month inputs by ID pattern (some pages use type="text" with YYYY-MM)
  if (!params.some(p => p.startsWith('month='))) {
    const monthInput = document.querySelector('[id$="-month"]');
    if (monthInput && monthInput.value) params.push(`month=${monthInput.value}`);
  }

  // 3. Find date inputs (single date, not range)
  if (!params.some(p => p.startsWith('date='))) {
    const dateInput = document.querySelector('[id$="-date"]');
    if (dateInput && dateInput.value && dateInput.type === 'date') {
      params.push(`date=${dateInput.value}`);
    }
  }

  // 4. Find group_by selector
  const groupSelect = document.querySelector('[id$="-group"], [id$="-groupby"]');
  if (groupSelect && groupSelect.value) params.push(`group_by=${groupSelect.value}`);

  // 5. Fallback: also check URL hash (in case some pages DO use it)
  const hash = window.location.hash.slice(1);
  const hashQuery = hash.split('?')[1] || '';
  if (hashQuery) {
    const urlParams = new URLSearchParams(hashQuery);
    for (const key of ['start', 'end', 'month', 'date', 'group_by']) {
      if (urlParams.get(key) && !params.some(p => p.startsWith(`${key}=`))) {
        params.push(`${key}=${urlParams.get(key)}`);
      }
    }
  }

  return params.length ? `&${params.join('&')}` : '';
}

function addExportButtonsToReportPages() {
  const headers = document.querySelectorAll('.pos-page-header-actions');
  headers.forEach(header => {
    if (header.querySelector('[data-export], .export-btn, .existing-export')) return;

    const hash = window.location.hash.slice(1);
    const routePath = hash.split('?')[0].split('/').pop();

    const exportName = REPORT_EXPORT_MAP[routePath];
    if (!exportName) return;

    // v8.16.15: Read params from page input fields, not URL hash
    const paramStr = _collectExportParams();

    const exportDiv = document.createElement('div');
    exportDiv.className = 'flex gap-2 export-btn';
    // v8.16.15: Use a function reference instead of inline onclick with paramStr
    // so the params are re-read at click time (in case the user changes dates after injection)
    exportDiv.innerHTML = `
      <button class="btn btn-secondary btn-sm" data-export-format="pdf" data-export-name="${exportName}" title="Download as PDF">
        <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
        PDF
      </button>
      <button class="btn btn-secondary btn-sm" data-export-format="excel" data-export-name="${exportName}" title="Download as Excel">
        <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
        Excel
      </button>`;
    // Wire up click handlers that re-read params at click time
    exportDiv.querySelectorAll('[data-export-format]').forEach(btn => {
      btn.onclick = () => {
        const fmt = btn.dataset.exportFormat;
        const name = btn.dataset.exportName;
        const params = _collectExportParams();
        window.open(`/api/reports/${name}/export?format=${fmt}${params}`, '_blank');
      };
    });
    header.appendChild(exportDiv);
  });
}

// Run after each page render + after any DOM changes
setTimeout(addExportButtonsToReportPages, 200);
// Also re-run periodically for dynamically loaded content
setInterval(addExportButtonsToReportPages, 2000);
