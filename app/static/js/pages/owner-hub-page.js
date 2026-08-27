// v8.0 Phase 3 — Owner Hub dashboard (on HQ instance)
// Consolidated view of all branches: P&L summed, leaderboard, per-branch stock + cash.
import { route } from '../router.js';
import { api } from '../api.js';
import { $, esc, fmtRs, fmtDate, skeletonCards, errorBox, chartTheme, chartOptions } from '../utils.js';

const SVG = {
  hub: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-4"/></svg>',
  branch: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>',
  cash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/><circle cx="12" cy="15" r="2"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
};

let _chart = null;

route('/insights/owner-hub', async (el) => {
  const today = new Date().toISOString().slice(0, 10);
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-info">${SVG.hub}</div>
      <div>
        <h2 class="pos-page-header-title">Owner Hub</h2>
        <p class="pos-page-header-sub">Consolidated view across all branches. Summaries sync daily + on shift-close.</p>
      </div>
      <div class="pos-page-header-actions">
        <input type="date" class="input input-sm" id="hub-date" value="${today}" style="width:auto">
        <button class="btn btn-secondary btn-sm" id="hub-refresh">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span>
          Refresh
        </button>
      </div>
    </div>
    <div id="hub-out">${skeletonCards(3)}</div>`;

  $('#hub-refresh').onclick = loadDashboard;
  $('#hub-date').onchange = loadDashboard;
  await loadDashboard();

  async function loadDashboard() {
    try {
      const date = $('#hub-date').value || today;
      const r = await api(`/api/hq/owner-hub?date=${encodeURIComponent(date)}`);
      renderDashboard(r);
    } catch (e) {
      $('#hub-out').innerHTML = errorBox(e.message);
    }
  }

  function renderDashboard(data) {
    const c = data.consolidated;
    const gpMargin = c.sales > 0 ? (c.gross_profit / c.sales * 100) : 0;
    const netProfit = c.gross_profit - c.expenses;
    const branches = data.branches || [];
    const leaderboard = data.leaderboard || [];
    const staleBranches = branches.filter(b => b.stale);
    const syncedToday = data.active_branches_synced_today;
    const totalBranches = data.branch_count;

    // Stat cards
    const statCards = `
      <div class="grid grid-4" style="gap:12px;margin-bottom:16px">
        <div class="card" style="padding:16px;text-align:center">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Total Sales</div>
          <div style="font-size:24px;font-weight:700;margin-top:4px">${fmtRs(c.sales)}</div>
          <div class="text-dim text-sm" style="margin-top:2px">${totalBranches} branches · ${syncedToday} synced</div>
        </div>
        <div class="card" style="padding:16px;text-align:center">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Gross Profit</div>
          <div style="font-size:24px;font-weight:700;margin-top:4px;color:var(--success-text,#16A34A)">${fmtRs(c.gross_profit)}</div>
          <div class="text-dim text-sm" style="margin-top:2px">${gpMargin.toFixed(1)}% margin</div>
        </div>
        <div class="card" style="padding:16px;text-align:center">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Net Profit</div>
          <div style="font-size:24px;font-weight:700;margin-top:4px;color:${netProfit >= 0 ? 'var(--success-text,#16A34A)' : 'var(--danger-text,#DC2626)'}">${fmtRs(netProfit)}</div>
          <div class="text-dim text-sm" style="margin-top:2px">GP − Expenses</div>
        </div>
        <div class="card" style="padding:16px;text-align:center">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Cash in Drawer</div>
          <div style="font-size:24px;font-weight:700;margin-top:4px;display:flex;align-items:center;justify-content:center;gap:4px">
            <span style="display:inline-flex;width:16px;height:16px">${SVG.cash}</span>
            ${fmtRs(c.cash_in_drawer)}
          </div>
          <div class="text-dim text-sm" style="margin-top:2px">across all branches</div>
        </div>
      </div>`;

    // Stale banner
    const staleBanner = staleBranches.length > 0 ? `
      <div class="card" style="padding:12px;background:var(--bg-warning-soft,#FEF3C7);border:1px solid var(--warning,#D97706);margin-bottom:16px;display:flex;gap:12px;align-items:center">
        <span style="display:inline-flex;width:20px;height:20px;color:var(--warning-text,#D97706)">${SVG.alert}</span>
        <div style="flex:1">
          <strong style="color:var(--warning-text,#D97706)">${staleBranches.length} branch${staleBranches.length === 1 ? '' : 'es'} stale</strong>
          <span class="text-sm" style="color:var(--warning-text,#D97706)"> — no sync in 24h: ${staleBranches.map(b => esc(b.name)).join(', ')}</span>
        </div>
      </div>` : '';

    // Leaderboard
    let leaderboardHtml = '';
    if (leaderboard.length === 0) {
      leaderboardHtml = `<div class="card text-center text-dim" style="padding:24px">No branches registered yet. Generate a registration code on the <a href="#/insights/hq-branches" style="color:inherit;text-decoration:underline">HQ Branches page</a>.</div>`;
    } else {
      leaderboardHtml = `
        <div class="card" style="padding:16px;margin-bottom:16px">
          <h3 style="margin:0 0 12px;display:flex;align-items:center;gap:6px">
            <span style="display:inline-flex;width:18px;height:18px">${SVG.chart}</span>
            Branch Leaderboard
          </h3>
          <div style="overflow-x:auto">
            <table class="table">
              <thead>
                <tr>
                  <th>#</th><th>Branch</th><th>Region</th>
                  <th style="text-align:right">Sales</th>
                  <th style="text-align:right">Gross Profit</th>
                  <th style="text-align:right">Margin</th>
                  <th>Last Seen</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                ${leaderboard.map((b, i) => {
                  const margin = b.sales > 0 ? (b.gross_profit / b.sales * 100) : 0;
                  return `<tr>
                    <td><strong>${i + 1}</strong></td>
                    <td><strong>${esc(b.name)}</strong></td>
                    <td>${esc(b.region || '—')}</td>
                    <td style="text-align:right">${fmtRs(b.sales)}</td>
                    <td style="text-align:right;color:var(--success-text,#16A34A)">${fmtRs(b.gross_profit)}</td>
                    <td style="text-align:right">${margin.toFixed(1)}%</td>
                    <td>${esc(b.last_seen || 'Never')}</td>
                    <td>${b.stale
                      ? '<span class="chip chip-warning chip-sm">Stale</span>'
                      : '<span class="chip chip-success chip-sm">Live</span>'}</td>
                  </tr>`;
                }).join('')}
              </tbody>
            </table>
          </div>
        </div>`;
    }

    // Per-branch breakdown
    let breakdownHtml = '';
    if (branches.length > 0) {
      breakdownHtml = `
        <div class="card" style="padding:16px;margin-bottom:16px">
          <h3 style="margin:0 0 12px">Per-Branch Breakdown</h3>
          ${branches.map(b => {
            const margin = b.sales > 0 ? (b.gross_profit / b.sales * 100) : 0;
            const stockKeys = Object.keys(b.stock_snapshot || {});
            const stockValue = stockKeys.reduce((s, k) => s + (b.stock_snapshot[k].value || 0), 0);
            return `<div class="card" style="padding:12px;margin-bottom:8px;${b.stale ? 'border-left:3px solid var(--warning,#D97706)' : ''}">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;flex-wrap:wrap">
                <div>
                  <strong style="font-size:15px">${esc(b.name)}</strong>
                  ${b.stale ? '<span class="chip chip-warning chip-sm" style="margin-left:6px">Stale</span>' : ''}
                  <div class="text-dim text-sm">${esc(b.region || 'No region')} · <code style="font-size:11px">${esc(b.branch_id)}</code></div>
                </div>
                <div style="text-align:right">
                  <div style="font-size:18px;font-weight:700">${fmtRs(b.sales)}</div>
                  <div class="text-dim text-sm">sales</div>
                </div>
              </div>
              <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-top:8px;font-size:13px">
                <div><span class="text-dim">COGS:</span> <strong>${fmtRs(b.cogs)}</strong></div>
                <div><span class="text-dim">GP:</span> <strong style="color:var(--success-text,#16A34A)">${fmtRs(b.gross_profit)}</strong></div>
                <div><span class="text-dim">Margin:</span> <strong>${margin.toFixed(1)}%</strong></div>
                <div><span class="text-dim">Expenses:</span> <strong>${fmtRs(b.expenses)}</strong></div>
                <div><span class="text-dim">Cash:</span> <strong>${fmtRs(b.cash_in_drawer)}</strong></div>
                <div><span class="text-dim">Stock Value:</span> <strong>${fmtRs(stockValue)}</strong></div>
              </div>
              ${stockKeys.length > 0 ? `<details style="margin-top:6px;font-size:12px;color:var(--text-dim,#64748B)">
                <summary style="cursor:pointer">Stock snapshot (${stockKeys.length} categories)</summary>
                <div style="margin-top:4px;display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:4px">
                  ${stockKeys.map(k => {
                    const s = b.stock_snapshot[k];
                    return `<div style="padding:4px 8px;background:var(--bg-2,#F1F5F9);border-radius:4px">
                      <strong>Cat #${esc(k)}</strong>: ${s.qty || 0} pcs · ${fmtRs(s.value || 0)}
                    </div>`;
                  }).join('')}
                </div>
              </details>` : ''}
            </div>`;
          }).join('')}
        </div>`;
    }

    $('#hub-out').innerHTML = statCards + staleBanner + leaderboardHtml + breakdownHtml;
  }
});
