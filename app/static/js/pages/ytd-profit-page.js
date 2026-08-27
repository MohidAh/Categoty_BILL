// v5.0 Phase 5 — YTD Profit page (Reports app)
import { route } from '../router.js';
import { api } from '../api.js';
import { $, esc, fmtRs, fmtPct, toast, skeletonCards, errorBox,
         chartTheme, chartOptions } from '../utils.js';

const SVG = {
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
};

let _chart = null;

route('/reports/ytd', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.chart}</div>
      <div>
        <h2 class="pos-page-header-title">Year-to-Date Profit</h2>
        <p class="pos-page-header-sub">Cumulative GP ÷ Cumulative Sales — NOT the average of monthly margins.</p>
      </div>
      <div class="pos-page-header-actions"></div>
    </div>
    <div id="ytd-out">${skeletonCards(2)}</div>`;

  try {
    const r = await api('/api/profit/ytd');
    if (r.ytd_sales === 0) {
      $('#ytd-out').innerHTML = `
        <div class="card text-center" style="padding:48px">
          <p style="font-weight:600;margin-bottom:8px">No sales yet</p>
          <p class="text-dim text-sm">Record sales to see YTD profit. Opening date: ${esc(r.opening_date)}</p>
        </div>`;
      return;
    }

    $('#ytd-out').innerHTML = `
      <div class="card" style="padding:24px;margin-bottom:16px;border:2px solid var(--success, #16a34a);background:var(--success-soft, #f0fdf4)">
        <div style="text-transform:uppercase;letter-spacing:0.5px;font-weight:700;color:var(--success-text, #16a34a)">
          YTD Gross Margin (Primary KPI)
        </div>
        <div style="font-size:32px;font-weight:800;color:var(--success-text, #16a34a);margin-top:8px">
          ${fmtPct(r.ytd_margin)}
        </div>
        <div class="text-sm" style="margin-top:8px">
          From <strong>${esc(r.opening_date)}</strong> to <strong>${esc(r.today)}</strong> —
          Cumulative GP ${fmtRs(r.ytd_gross_profit)} ÷ Cumulative Sales ${fmtRs(r.ytd_sales)}
        </div>
      </div>

      <div class="grid grid-4" style="gap:12px;margin-bottom:16px">
        <div class="card" style="padding:16px">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">YTD Sales</div>
          <div style="font-size:22px;font-weight:700;margin-top:4px">${fmtRs(r.ytd_sales)}</div>
        </div>
        <div class="card" style="padding:16px">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">YTD COGS</div>
          <div style="font-size:22px;font-weight:700;margin-top:4px">${fmtRs(r.ytd_cogs)}</div>
        </div>
        <div class="card" style="padding:16px">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">YTD Gross Profit</div>
          <div style="font-size:22px;font-weight:700;margin-top:4px;color:var(--success-text, #16a34a)">${fmtRs(r.ytd_gross_profit)}</div>
        </div>
        <div class="card" style="padding:16px">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">YTD Operating Profit</div>
          <div style="font-size:22px;font-weight:700;margin-top:4px;color:${r.ytd_operating_profit >= 0 ? 'var(--success-text, #16a34a)' : 'var(--danger-text, #dc2626)'}">${fmtRs(r.ytd_operating_profit)}</div>
        </div>
      </div>

      <div class="card" style="padding:16px;margin-bottom:16px">
        <div style="display:flex;gap:8px;align-items:center">
          <span style="display:inline-flex;width:18px;height:18px;color:var(--text-dim)">${SVG.info}</span>
          <div class="text-sm">
            <strong>Method check:</strong> YTD margin ${fmtPct(r.ytd_margin)} vs avg-of-monthly-margins ${fmtPct(r.avg_of_monthly_margins)}
            (difference ${r.method_difference > 0 ? '+' : ''}${r.method_difference}%).
            The cumulative method is correct — it weights each month by its sales volume.
          </div>
        </div>
      </div>

      <div class="card" style="padding:24px">
        <h3 style="margin-bottom:12px">Monthly Trend</h3>
        <div id="ytd-chart-wrap" style="height:300px"></div>
      </div>`;

    // Render the monthly trend chart
    const wrap = $('#ytd-chart-wrap');
    if (wrap && typeof Chart !== 'undefined' && r.monthly && r.monthly.length > 0) {
      const theme = chartTheme();
      const colors = theme.colors || ['#cc785c', '#5db8a6', '#d4a017', '#5db872'];
      if (_chart) { _chart.destroy(); _chart = null; }
      const canvas = document.createElement('canvas');
      wrap.innerHTML = '';
      wrap.appendChild(canvas);
      _chart = new Chart(canvas, chartOptions({
        type: 'line',
        data: {
          labels: r.monthly.map(m => m.month),
          datasets: [
            {
              label: 'Gross Profit (Rs)',
              data: r.monthly.map(m => m.gross_profit),
              borderColor: colors[0],
              backgroundColor: colors[0] + '33',
              yAxisID: 'y',
              tension: 0.3,
              fill: true,
            },
            {
              label: 'Margin %',
              data: r.monthly.map(m => m.margin_pct),
              borderColor: colors[1] || '#5db8a6',
              backgroundColor: 'transparent',
              yAxisID: 'y1',
              tension: 0.3,
              borderDash: [5, 5],
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: theme.textColor } } },
          scales: {
            x: { grid: { color: theme.gridColor }, ticks: { color: theme.textColor } },
            y: {
              position: 'left',
              grid: { color: theme.gridColor },
              ticks: { color: theme.textColor, callback: v => 'Rs ' + v.toLocaleString() },
              title: { display: true, text: 'Gross Profit (Rs)', color: theme.textColor },
            },
            y1: {
              position: 'right',
              grid: { display: false },
              ticks: { color: theme.textColor, callback: v => v + '%' },
              title: { display: true, text: 'Margin %', color: theme.textColor },
            },
          },
        },
      }));
    }
  } catch (e) {
    $('#ytd-out').innerHTML = errorBox(e.message);
  }
});
