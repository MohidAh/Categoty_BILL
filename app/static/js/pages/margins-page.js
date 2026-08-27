// v5.0 Phase 3 — Margins page (Reports app)
// Shows both margins: Category Average (informational) and Actual Overall (primary KPI).
import { route } from '../router.js';
import { api } from '../api.js';
import { $, esc, fmtRs, fmtPct, toast, skeletonCards, errorBox } from '../utils.js';

const SVG = {
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
  trendUp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>',
};

route('/reports/margins', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.chart}</div>
      <div>
        <h2 class="pos-page-header-title">Margins</h2>
        <p class="pos-page-header-sub">Category Average Margin (informational) vs Actual Overall Gross Margin (primary KPI).</p>
      </div>
      <div class="pos-page-header-actions"></div>
    </div>
    <div id="m-out">${skeletonCards(2)}</div>`;

  try {
    const r = await api('/api/profit/margins');
    const cats = r.categories || [];
    if (cats.length === 0) {
      $('#m-out').innerHTML = `
        <div class="card text-center" style="padding:48px">
          <p style="font-weight:600;margin-bottom:8px">No categories yet</p>
          <p class="text-dim text-sm">Add price categories and confirm bills to see margin analysis.</p>
        </div>`;
      return;
    }

    // Two hero cards: Category Average (subtle) vs Actual Overall (emphasized)
    $('#m-out').innerHTML = `
      <div class="grid grid-2" style="gap:16px;margin-bottom:16px">
        <div class="card" style="padding:24px;border:1px solid var(--border)">
          <div class="text-dim text-sm" style="text-transform:uppercase;letter-spacing:0.5px;font-weight:600">
            Category Average Margin
          </div>
          <div style="font-size:28px;font-weight:600;color:var(--text-dim);margin-top:8px">
            ${fmtPct(r.category_average_margin)}
          </div>
          <div class="text-dim text-sm" style="margin-top:8px">
            <span style="display:inline-flex;width:14px;height:14px;vertical-align:middle">${SVG.info}</span>
            Informational — simple mean of category margins, ignores sales mix.
          </div>
        </div>
        <div class="card" style="padding:24px;border:2px solid var(--success, #16a34a);background:var(--success-soft, #f0fdf4)">
          <div style="text-transform:uppercase;letter-spacing:0.5px;font-weight:700;color:var(--success-text, #16a34a)">
            Actual Overall Gross Margin
          </div>
          <div style="font-size:32px;font-weight:800;color:var(--success-text, #16a34a);margin-top:8px">
            ${fmtPct(r.actual_overall_margin)}
          </div>
          <div class="text-sm" style="margin-top:8px">
            <strong>Primary KPI</strong> — Total Gross Profit ÷ Total Sales (sales-mix weighted).
          </div>
        </div>
      </div>

      <div class="card" style="padding:16px;margin-bottom:16px;background:var(--bg-2, #f3f4f6);border-left:3px solid var(--info, #3b82f6)">
        <div style="display:flex;gap:8px;align-items:flex-start">
          <span style="display:inline-flex;width:18px;height:18px;color:var(--info, #3b82f6);flex-shrink:0;margin-top:2px">${SVG.info}</span>
          <div class="text-sm">
            <strong>About these margins:</strong> Uses <strong>current running avg cost</strong>
            (weighted average of all confirmed purchase bills, less what's been sold).
            This is a <strong>forward-looking</strong> metric — "what margin will I make on the next sale?"
            <br>
            For <strong>historical margins</strong> (margin you actually realized on past sales over a date range),
            see <a href="#/reports/profit-analysis" style="color:var(--info-text, #3b82f6);text-decoration:underline">Profit Analysis</a>.
            The two numbers will differ slightly because cost prices change as new inventory arrives.
          </div>
        </div>
      </div>

      <div class="card" style="padding:16px;margin-bottom:16px">
        <div style="display:flex;gap:8px;align-items:center">
          <span style="display:inline-flex;width:18px;height:18px;color:var(--text-dim)">${SVG.info}</span>
          <div class="text-sm">
            <strong>Difference: ${r.difference_pct > 0 ? '+' : ''}${r.difference_pct}%</strong> —
            ${Math.abs(r.difference_pct) < 0.5
              ? 'Sales mix closely matches the category average — margins are consistent across categories.'
              : r.difference_pct > 0
                ? 'Category Average overstates the true margin — lower-margin categories sell more units.'
                : 'Category Average understates the true margin — higher-margin categories sell more units.'}
          </div>
        </div>
      </div>

      <div class="card">
        <h3 style="margin-bottom:12px">Per-Category Margins</h3>
        <div style="overflow-x:auto">
        <table class="table">
          <thead>
            <tr>
              <th>Code</th><th>Category</th>
              <th style="text-align:right">Sell Price</th>
              <th style="text-align:right">Avg Cost</th>
              <th style="text-align:right">Margin (Rs)</th>
              <th style="text-align:right">Margin %</th>
            </tr>
          </thead>
          <tbody>
            ${cats.map(c => {
              const marginRs = c.sell_price - c.avg_cost;
              const marginColor = c.margin_pct >= 30 ? 'var(--success-text, #16a34a)'
                : c.margin_pct >= 15 ? 'var(--warning-text, #d97706)'
                : 'var(--danger-text, #dc2626)';
              return `<tr>
                <td><strong>${esc(c.code)}</strong></td>
                <td>${esc(c.name)}</td>
                <td style="text-align:right">${fmtRs(c.sell_price)}</td>
                <td style="text-align:right">${fmtRs(c.avg_cost)}</td>
                <td style="text-align:right">${fmtRs(marginRs)}</td>
                <td style="text-align:right;font-weight:700;color:${marginColor}">${fmtPct(c.margin_pct)}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
        </div>
      </div>

      <div class="card" style="margin-top:16px">
        <h3 style="margin-bottom:8px">Totals (all-time, non-refunded sales)</h3>
        <div style="display:flex;gap:24px;flex-wrap:wrap">
          <div><span class="text-dim text-sm">Total Sales:</span> <strong>${fmtRs(r.total_sales)}</strong></div>
          <div><span class="text-dim text-sm">Total COGS:</span> <strong>${fmtRs(r.total_cogs)}</strong></div>
          <div><span class="text-dim text-sm">Total Gross Profit:</span> <strong style="color:var(--success-text, #16a34a)">${fmtRs(r.total_gross_profit)}</strong></div>
        </div>
      </div>`;
  } catch (e) {
    $('#m-out').innerHTML = errorBox(e.message);
  }
});
