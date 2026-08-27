// v5.0 Phase 8 — Store Profit Dashboard (the hero page, Reports default landing)
import { route, navigate } from '../router.js';
import { api } from '../api.js';
import { $, esc, fmt, fmtRs, fmtPct, toast, skeletonCards, errorBox } from '../utils.js';

const SVG = {
  trendUp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>',
  box: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>',
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
};

function statCard(label, value, chipClass, svgIcon, sub = '') {
  return `<div class="stat-card">
    <div class="stat-card-icon ${chipClass}">${svgIcon}</div>
    <div class="stat-card-label">${esc(label)}</div>
    <div class="stat-card-value">${value}</div>
    ${sub ? `<div class="stat-card-delta">${sub}</div>` : ''}
  </div>`;
}

route('/reports/store-profit', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.trendUp}</div>
      <div>
        <h2 class="pos-page-header-title">Store Profit Dashboard</h2>
        <p class="pos-page-header-sub">The single source of truth — stock, margins, daily, monthly, YTD, and cash in one view.</p>
      </div>
      <div class="pos-page-header-actions"></div>
    </div>
    <div id="sp-out">${skeletonCards(3)}</div>`;

  try {
    const d = await api('/api/profit/dashboard');

    // The single most prominent number: Actual Overall Gross Margin
    const overallMargin = d.current_margins.actual_overall_margin;
    const reserveColor = d.cash.stock_reserve_color === 'green' ? 'var(--success-text, #16a34a)'
                       : d.cash.stock_reserve_color === 'amber' ? 'var(--warning-text, #d97706)'
                       : 'var(--danger-text, #dc2626)';

    $('#sp-out').innerHTML = `
      <!-- HERO: Actual Overall Gross Margin (the primary KPI) -->
      <div class="card" style="padding:32px;margin-bottom:16px;text-align:center;border:2px solid var(--success, #16a34a);background:var(--success-soft, #f0fdf4)">
        <div style="text-transform:uppercase;letter-spacing:1px;font-weight:700;color:var(--success-text, #16a34a);font-size:13px">
          Actual Overall Gross Margin — Primary KPI
        </div>
        <div style="font-size:48px;font-weight:800;color:var(--success-text, #16a34a);margin:12px 0;line-height:1">
          ${fmtPct(overallMargin)}
        </div>
        <div class="text-sm" style="margin-top:8px">
          Total Sales ${fmtRs(d.current_margins.total_sales)} · Total Gross Profit ${fmtRs(d.current_margins.total_gross_profit)}
        </div>
        <div class="text-dim text-sm" style="margin-top:4px">
          Category Average Margin (informational): ${fmtPct(d.current_margins.category_average_margin)}
          (difference ${d.current_margins.difference_pct > 0 ? '+' : ''}${d.current_margins.difference_pct}%)
        </div>
      </div>

      <!-- 6 KPI groups in a 2-column grid -->
      <div class="grid grid-2" style="gap:16px">

        <!-- 1. Current Stock -->
        <div class="card" style="padding:20px">
          <h3 style="margin-bottom:12px;display:flex;gap:8px;align-items:center">
            <span style="display:inline-flex;width:18px;height:18px;color:var(--warning-text, #d97706)">${SVG.box}</span>
            Current Stock
          </h3>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
            <div>
              <div class="text-dim text-sm">Total Quantity</div>
              <div style="font-size:22px;font-weight:700">${fmt(d.current_stock.total_qty)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">Total Value</div>
              <div style="font-size:22px;font-weight:700">${fmtRs(d.current_stock.total_value)}</div>
            </div>
          </div>
          ${d.current_stock.per_category.length > 0 ? `
            <table style="width:100%;font-size:13px">
              <thead><tr style="text-align:left;color:var(--text-dim)">
                <th>Cat</th><th style="text-align:right">Qty</th>
                <th style="text-align:right">Avg Cost</th><th style="text-align:right">Value</th>
              </tr></thead>
              <tbody>
                ${d.current_stock.per_category.map(c => `<tr>
                  <td><span class="pos-cat-code" style="background:${esc(c.color || '#888')}">${esc(c.code)}</span></td>
                  <td style="text-align:right">${fmt(c.qty)}</td>
                  <td style="text-align:right">${fmtRs(c.avg_cost)}</td>
                  <td style="text-align:right;font-weight:600">${fmtRs(c.value)}</td>
                </tr>`).join('')}
              </tbody>
            </table>` : '<p class="text-dim text-sm">No stock data yet.</p>'}
        </div>

        <!-- 2. Current Margins (per category) -->
        <div class="card" style="padding:20px">
          <h3 style="margin-bottom:4px;display:flex;gap:8px;align-items:center">
            <span style="display:inline-flex;width:18px;height:18px;color:var(--info-text, #3b82f6)">${SVG.chart}</span>
            Current Margins
          </h3>
          <p class="text-dim text-sm" style="margin:0 0 12px">
            Based on <strong>current running avg cost</strong> (forward-looking).
            Differs from <a href="#/reports/profit-analysis" style="color:var(--info-text, #3b82f6)">Profit Analysis</a>,
            which uses <strong>historical cost-at-time-of-sale</strong>.
          </p>
          ${d.current_margins.categories.length > 0 ? `
            <table style="width:100%;font-size:13px">
              <thead><tr style="text-align:left;color:var(--text-dim)">
                <th>Cat</th><th>Sell</th><th>Cost</th><th style="text-align:right">Margin</th>
              </tr></thead>
              <tbody>
                ${d.current_margins.categories.map(c => {
                  const color = c.margin_pct >= 30 ? 'var(--success-text, #16a34a)'
                              : c.margin_pct >= 15 ? 'var(--warning-text, #d97706)'
                              : 'var(--danger-text, #dc2626)';
                  return `<tr>
                    <td><strong>${esc(c.code)}</strong></td>
                    <td>${fmtRs(c.sell_price)}</td>
                    <td>${fmtRs(c.avg_cost)}</td>
                    <td style="text-align:right;font-weight:700;color:${color}">${fmtPct(c.margin_pct)}</td>
                  </tr>`;
                }).join('')}
              </tbody>
            </table>` : '<p class="text-dim text-sm">No categories yet.</p>'}
          <div style="margin-top:8px;padding:8px;background:var(--bg-2, #f3f4f6);border-radius:6px;font-size:12px">
            <span class="text-dim">Category Avg (info):</span> <strong>${fmtPct(d.current_margins.category_average_margin)}</strong>
            · <span class="text-dim">Actual Overall (KPI):</span> <strong style="color:var(--success-text, #16a34a)">${fmtPct(d.current_margins.actual_overall_margin)}</strong>
          </div>
        </div>

        <!-- 3. Daily -->
        <div class="card" style="padding:20px">
          <h3 style="margin-bottom:12px;display:flex;gap:8px;align-items:center">
            <span style="display:inline-flex;width:18px;height:18px;color:var(--text-dim)">${SVG.calendar}</span>
            Today
          </h3>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div>
              <div class="text-dim text-sm">Sales</div>
              <div style="font-size:22px;font-weight:700">${fmtRs(d.daily.sales)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">Gross Profit</div>
              <div style="font-size:22px;font-weight:700;color:var(--success-text, #16a34a)">${fmtRs(d.daily.gross_profit)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">COGS</div>
              <div style="font-size:16px;font-weight:600">${fmtRs(d.daily.cogs)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">Margin</div>
              <div style="font-size:16px;font-weight:600">${fmtPct(d.daily.margin)}</div>
            </div>
          </div>
        </div>

        <!-- 4. Monthly -->
        <div class="card" style="padding:20px">
          <h3 style="margin-bottom:12px;display:flex;gap:8px;align-items:center">
            <span style="display:inline-flex;width:18px;height:18px;color:var(--text-dim)">${SVG.calendar}</span>
            This Month
          </h3>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div>
              <div class="text-dim text-sm">Sales</div>
              <div style="font-size:22px;font-weight:700">${fmtRs(d.monthly.sales)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">Gross Profit</div>
              <div style="font-size:22px;font-weight:700;color:var(--success-text, #16a34a)">${fmtRs(d.monthly.gross_profit)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">Monthly Margin</div>
              <div style="font-size:16px;font-weight:600">${fmtPct(d.monthly.monthly_margin)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">Operating Profit</div>
              <div style="font-size:16px;font-weight:600;color:${d.monthly.operating_profit >= 0 ? 'var(--success-text, #16a34a)' : 'var(--danger-text, #dc2626)'}">${fmtRs(d.monthly.operating_profit)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">COGS</div>
              <div style="font-size:14px">${fmtRs(d.monthly.cogs)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">Operating Expenses</div>
              <div style="font-size:14px">${fmtRs(d.monthly.operating_expenses)}</div>
            </div>
          </div>
        </div>

        <!-- 5. YTD -->
        <div class="card" style="padding:20px">
          <h3 style="margin-bottom:12px;display:flex;gap:8px;align-items:center">
            <span style="display:inline-flex;width:18px;height:18px;color:var(--text-dim)">${SVG.trendUp}</span>
            Year-to-Date
          </h3>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div>
              <div class="text-dim text-sm">YTD Sales</div>
              <div style="font-size:22px;font-weight:700">${fmtRs(d.ytd.sales)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">YTD Gross Profit</div>
              <div style="font-size:22px;font-weight:700;color:var(--success-text, #16a34a)">${fmtRs(d.ytd.gross_profit)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">YTD Margin</div>
              <div style="font-size:16px;font-weight:600">${fmtPct(d.ytd.ytd_margin)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">YTD COGS</div>
              <div style="font-size:14px">${fmtRs(d.ytd.cogs)}</div>
            </div>
          </div>
          <div class="text-dim text-sm" style="margin-top:8px">Since ${esc(d.ytd.opening_date)}</div>
        </div>

        <!-- 6. Cash -->
        <div class="card" style="padding:20px;border-color:${reserveColor};border-width:2px">
          <h3 style="margin-bottom:12px;display:flex;gap:8px;align-items:center">
            <span style="display:inline-flex;width:18px;height:18px;color:${reserveColor}">${SVG.wallet}</span>
            Cash & Reserves
          </h3>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
            <div>
              <div class="text-dim text-sm">Cash in Drawer</div>
              <div style="font-size:22px;font-weight:700">${fmtRs(d.cash.cash_in_drawer)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">Stock Reserve Days</div>
              <div style="font-size:22px;font-weight:700;color:${reserveColor}">${d.cash.stock_reserve_days.toFixed(1)}</div>
              <div class="text-dim text-sm">target ${d.cash.stock_reserve_target_days.toFixed(0)}d</div>
            </div>
            <div>
              <div class="text-dim text-sm">Safe Weekly Withdrawal</div>
              <div style="font-size:16px;font-weight:600;color:var(--success-text, #16a34a)">${fmtRs(d.cash.safe_withdrawal_weekly)}</div>
            </div>
            <div>
              <div class="text-dim text-sm">Available for Withdrawal</div>
              <div style="font-size:16px;font-weight:600">${fmtRs(d.cash.available_for_withdrawal)}</div>
            </div>
          </div>
          <div style="padding:8px;background:${reserveColor}22;border-radius:6px;color:${reserveColor};font-size:12px;font-weight:600">
            ${esc(d.cash.stock_reserve_recommendation)}
          </div>
        </div>

      </div>

      <!-- The 9 questions answered (reference card) -->
      <div class="card" style="padding:20px;margin-top:16px">
        <h3 style="margin-bottom:12px">Owner's 9 Questions — Answered</h3>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px">
          <div><strong>1. Stock now?</strong> ${fmt(d.current_stock.total_qty)} pcs (${fmtRs(d.current_stock.total_value)})</div>
          <div><strong>2. Avg cost?</strong> Per category — see Current Stock table above</div>
          <div><strong>3. Category margins?</strong> See Current Margins table above</div>
          <div><strong>4. Today's sales & GP?</strong> ${fmtRs(d.daily.sales)} / ${fmtRs(d.daily.gross_profit)}</div>
          <div><strong>5. Monthly margin?</strong> ${fmtPct(d.monthly.monthly_margin)}</div>
          <div><strong>6. YTD margin?</strong> ${fmtPct(d.ytd.ytd_margin)}</div>
          <div><strong>7. Operating profit?</strong> ${fmtRs(d.monthly.operating_profit)}</div>
          <div><strong>8. Reserve for stock?</strong> ${d.cash.stock_reserve_days.toFixed(1)} days of cover</div>
          <div><strong>9. Safe withdrawal?</strong> ${fmtRs(d.cash.safe_withdrawal_weekly)}/week</div>
        </div>
      </div>`;
  } catch (e) {
    $('#sp-out').innerHTML = errorBox(e.message);
  }
});
