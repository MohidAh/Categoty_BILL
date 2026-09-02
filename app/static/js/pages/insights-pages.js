// AI Insights app — AI Assistant chat, ABC Analysis, Trends, Forecast
// All render inside the AI Insights app SnowUI shell (chip-pink color theme).
import { route, navigate, reload } from '../router.js';
import { errorState } from '../core/states.js';
import { api, apiPost } from '../api.js';
import { $, $$, esc, fmt, fmtRs, fmtDate, fmtPct, fmtDecimalPct, icon, toast, showLoading, hideLoading,
         openModal, closeModal, skeletonCards, errorBox, emptyState, chartTheme } from '../utils.js';
import { initListState } from '../list-state.js';

// Shared SVG icon set for insights pages
const SVG = {
  brain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"/></svg>',
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>',
  send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  trendUp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>',
  trendDown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>',
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
// AI ASSISTANT — full chat interface with history
// ═══════════════════════════════════════════════════
// v8.16.1: /insights redirects to /insights/agent (the AI Assistant)
// v8.16.11: The actual /insights/agent page is now in agent-chat-page.js
//           (redesigned with dark-mode chat card + coral accents).
//           This file only keeps the redirect.
route('/insights', async (el) => {
  navigate('/insights/agent');
  return;
});

// v8.16.11: The actual /insights/agent page is now in agent-chat-page.js
// (redesigned with dark-mode chat card + coral accents).

// ═══════════════════════════════════════════════════
// ABC ANALYSIS — Pareto classification
// ═══════════════════════════════════════════════════
route('/insights/abc', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-pink">${SVG.chart}</div>
      <div>
        <h2 class="pos-page-header-title">ABC Analysis (Pareto)</h2>
        <p class="pos-page-header-sub">Classify items by revenue contribution: A = top 80%, B = next 15%, C = bottom 5%.</p>
      </div>
    </div>
    <div id="abc-out">${skeletonCards(3)}</div>`;

  try {
    const abc = await api('/api/insights/abc');
    const a = abc.summary.A || { count: 0, revenue: 0 };
    const b = abc.summary.B || { count: 0, revenue: 0 };
    const c = abc.summary.C || { count: 0, revenue: 0 };
    const totalRevenue = a.revenue + b.revenue + c.revenue;

    let html = `
      <div class="grid grid-3 mb-4">
        ${statCard('Class A · Top 80%', a.count, 'chip-success', SVG.trendUp, fmtRs(a.revenue))}
        ${statCard('Class B · Next 15%', b.count, 'chip-warning', SVG.chart, fmtRs(b.revenue))}
        ${statCard('Class C · Bottom 5%', c.count, 'chip-secondary', SVG.trendDown, fmtRs(c.revenue))}
      </div>`;

    if (abc.items && abc.items.length) {
      html += `
        <div class="card">
          <div class="card-title">
            <h3>Item Classification (${abc.items.length} items)</h3>
            <span class="text-sm text-dim">Total revenue: ${fmtRs(totalRevenue)}</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr>
                <th>Item</th><th>Class</th>
                <th class="table-num">Qty</th><th class="table-num">Bills</th>
                <th class="table-num">Revenue</th><th class="table-num">Profit</th>
                <th>Margin</th>
              </tr></thead>
              <tbody>${abc.items.map(i => {
                const mc = i.margin >= 0.3 ? 'text-success' : i.margin >= 0.2 ? 'text-warning' : 'text-danger';
                const bc = i.class === 'A' ? 'badge-success' : i.class === 'B' ? 'badge-warning' : '';
                return `<tr>
                  <td class="font-semibold">${esc(i.raw)}</td>
                  <td><span class="badge ${bc}">${esc(i.class)}</span></td>
                  <td class="table-num">${fmt(i.total_qty)}</td>
                  <td class="table-num">${i.bill_count}</td>
                  <td class="table-num">${fmtRs(i.revenue)}</td>
                  <td class="table-num ${mc}">${fmtRs(i.profit)}</td>
                  <td class="${mc}">${fmtDecimalPct(i.margin, 0)}</td>
                </tr>`;
              }).join('')}</tbody>
            </table>
          </div>
        </div>`;
    } else {
      html += emptyState('No items yet', 'Confirm bills with line items to see ABC classification.', '', '');
    }

    // Tips card
    html += `
      <div class="card mt-4">
        <h3>How to use ABC Analysis</h3>
        <div class="grid grid-3 mt-3">
          <div>
            <div class="kpi-label"><span class="badge badge-success">A</span> Class A items</div>
            <p class="mt-2 text-sm">These drive 80% of revenue. Always keep in stock, negotiate bulk discounts, and prioritize reordering.</p>
          </div>
          <div>
            <div class="kpi-label"><span class="badge badge-warning">B</span> Class B items</div>
            <p class="mt-2 text-sm">Moderate impact. Monitor sales trends &mdash; promote to A or demote to C based on performance.</p>
          </div>
          <div>
            <div class="kpi-label"><span class="badge">C</span> Class C items</div>
            <p class="mt-2 text-sm">Low revenue contribution. Consider discontinuing or running clearance discounts to free up capital.</p>
          </div>
        </div>
      </div>`;

    $('#abc-out').innerHTML = html;
  } catch (e) {
    $('#abc-out').innerHTML = errorBox(e.message);
  }
});

// ═══════════════════════════════════════════════════
// TRENDS — market trends + alerts + refresh + dismiss/act
// ═══════════════════════════════════════════════════
route('/insights/trends', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-pink">${SVG.trendUp}</div>
      <div>
        <h2 class="pos-page-header-title">Market Trends & Alerts</h2>
        <p class="pos-page-header-sub">AI-analyzed trend alerts, seasonal patterns, and reorder suggestions.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-sm" id="tr-refresh-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span>
          Refresh AI Analysis
        </button>
      </div>
    </div>

    <div id="tr-stats" class="mb-4"></div>

    <div class="grid grid-2 mb-4">
      <div class="card">
        <div class="card-title"><h3>Active Trend Alerts</h3></div>
        <div id="tr-alerts">${skeletonCards(2)}</div>
      </div>
      <div class="card">
        <div class="card-title"><h3>Insights Alerts</h3></div>
        <div id="tr-insights">${skeletonCards(2)}</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><h3>Seasonal Patterns</h3></div>
      <div id="tr-seasonal">${skeletonCards(1)}</div>
    </div>`;

  $('#tr-refresh-btn').onclick = async () => {
    const btn = $('#tr-refresh-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-sm"></span> Analyzing...';
    try {
      showLoading('Running AI trend analysis...');
      const r = await apiPost('/api/trends/refresh');
      hideLoading();
      toast(r.message || 'Trends refreshed', 'success');
      loadTrends();
    } catch (e) {
      hideLoading();
      toast('Error: ' + e.message, 'error');
    }
    btn.disabled = false;
    btn.innerHTML = `<span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span> Refresh AI Analysis`;
  };

  await loadTrends();

  async function loadTrends() {
    try {
      const [trendsRes, insightsRes, seasonalRes] = await Promise.all([
        api('/api/trends'),
        api('/api/insights/alerts'),
        api('/api/trends/seasonal'),
      ]);

      const alerts = trendsRes.alerts || [];
      const insightsAlerts = insightsRes.critical || [];
      const warnings = insightsRes.warning || [];
      const seasonal = seasonalRes.alerts || [];

      // Stats
      const activeAlerts = alerts.filter(a => a.status === 'active').length;
      $('#tr-stats').innerHTML = `
        <div class="grid grid-4">
          ${statCard('Trend Alerts', alerts.length, 'chip-primary', SVG.trendUp)}
          ${statCard('Critical Alerts', insightsAlerts.length, 'chip-danger', SVG.alert)}
          ${statCard('Warnings', warnings.length, 'chip-warning', SVG.alert)}
          ${statCard('Seasonal Patterns', seasonal.length, 'chip-info', SVG.sun)}
        </div>`;

      // Trend alerts
      if (!alerts.length) {
        $('#tr-alerts').innerHTML = emptyState('No trend alerts', 'Click "Refresh AI Analysis" to generate fresh alerts.', '', '');
      } else {
        $('#tr-alerts').innerHTML = alerts.map(a => `
          <div class="alert ${a.severity === 'high' ? 'alert-danger' : a.severity === 'medium' ? 'alert-warning' : 'alert-info'} mb-2">
            <div><strong>${esc(a.title || a.trend_name || 'Trend Alert')}</strong>
              <div class="text-sm mt-1">${esc(a.message || a.suggestion || '')}</div>
              ${a.action ? `<div class="text-xs text-dim mt-2">Suggested action: ${esc(a.action)}</div>` : ''}
              <div class="flex gap-2 mt-2">
                <button class="btn btn-secondary btn-sm" data-trend-dismiss="${esc(a.id)}">Dismiss</button>
                <button class="btn btn-sm" data-trend-act="${esc(a.id)}">Acted On</button>
              </div>
            </div>
          </div>`).join('');

        $$('[data-trend-dismiss]').forEach(b => {
          b.onclick = async () => {
            try {
              await apiPost(`/api/trends/${b.dataset.trendDismiss}/dismiss`, {});
              toast('Alert dismissed', 'success');
              loadTrends();
            } catch (e) { toast('Error: ' + e.message, 'error'); }
          };
        });
        $$('[data-trend-act]').forEach(b => {
          b.onclick = async () => {
            try {
              await apiPost(`/api/trends/${b.dataset.trendAct}/acted`, {});
              toast('Marked as acted on', 'success');
              loadTrends();
            } catch (e) { toast('Error: ' + e.message, 'error'); }
          };
        });
      }

      // Insights alerts (critical + warning)
      let insightsHtml = '';
      if (insightsAlerts.length) {
        insightsHtml += insightsAlerts.map(a => `
          <div class="alert alert-danger mb-2">
            <div><strong>${esc(a.message)}</strong>
              ${a.action ? `<div class="text-xs text-dim mt-2">${esc(a.action)}</div>` : ''}
            </div>
          </div>`).join('');
      }
      if (warnings.length) {
        insightsHtml += warnings.map(a => `
          <div class="alert alert-warning mb-2">
            <div>${esc(a.message)}
              ${a.action ? `<div class="text-xs text-dim mt-2">${esc(a.action)}</div>` : ''}
            </div>
          </div>`).join('');
      }
      $('#tr-insights').innerHTML = insightsHtml || '<p class="text-dim text-sm">No active insights alerts.</p>';

      // Seasonal
      // v8.18.11 fix: the table read s.name/pattern, s.month, s.impact,
      // s.recommendation — none of which /api/trends/seasonal returns
      // (rows carry type, festival, items_to_stock, category, message,
      // priority), so every cell rendered '—'. Rewritten to the real
      // contract (the home dashboard already read it correctly).
      if (!seasonal.length) {
        $('#tr-seasonal').innerHTML = '<p class="text-dim text-sm">No seasonal patterns detected for the current month.</p>';
      } else {
        $('#tr-seasonal').innerHTML = `
          <div class="table-wrap">
            <table>
              <thead><tr><th>Season / Event</th><th>Timing</th><th>Priority</th><th>Recommendation</th></tr></thead>
              <tbody>${seasonal.map(s => `<tr>
                <td class="font-semibold">${esc(s.festival || '—')}</td>
                <td class="text-sm">${esc(s.type === 'current' ? 'This month' : 'Next month')}</td>
                <td><span class="badge ${s.priority === 'high' ? 'badge-danger' : s.priority === 'medium' ? 'badge-warning' : 'badge-success'}">${esc(s.priority || '—')}</span></td>
                <td class="text-sm">${esc(s.message || s.items_to_stock || '—')}</td>
              </tr>`).join('')}</tbody>
            </table>
          </div>`;
      }
    } catch (e) {
      $('#tr-alerts').innerHTML = errorBox(e.message);
    }
  }
});

// ═══════════════════════════════════════════════════
// FORECAST — spend forecast chart + per-item forecasting
// ═══════════════════════════════════════════════════
route('/insights/forecast', async (el, path, q) => {
  // v8.18.5: selected period persists across navigation
  const st = initListState('forecast', q, { periods: '3' });
  st.syncUrlIfRestored();
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-pink">${SVG.trendUp}</div>
      <div>
        <h2 class="pos-page-header-title">Spend Forecast</h2>
        <p class="pos-page-header-sub">3-month forecast based on historical spend patterns. Dashed line = predicted.</p>
      </div>
      <div class="pos-page-header-actions">
        <select class="select select-sm" id="fc-periods">
          <option value="3" ${st.val('periods') === '3' ? 'selected' : ''}>3 months ahead</option>
          <option value="6" ${st.val('periods') === '6' ? 'selected' : ''}>6 months ahead</option>
          <option value="12" ${st.val('periods') === '12' ? 'selected' : ''}>12 months ahead</option>
        </select>
      </div>
    </div>
    <div id="fc-out">${skeletonCards(3)}</div>`;

  $('#fc-periods').onchange = () => { st.replace({ periods: $('#fc-periods').value }); loadForecast(); };
  await loadForecast();

  async function loadForecast() {
    const periods = $('#fc-periods').value;
    try {
      const forecast = await api(`/api/insights/forecast?periods=${periods}`);
      if (!forecast.history || forecast.history.length < 2) {
        $('#fc-out').innerHTML = emptyState(
          'Not enough data for forecast',
          'Need at least 2 months of historical data to generate a forecast. Confirm more bills to enable forecasting.',
          '', ''
        );
        return;
      }

      const lastActual = forecast.history[forecast.history.length - 1];
      const nextForecast = forecast.forecast[0] || { value: 0, month: '—' };
      const trend = nextForecast.value - lastActual.value;
      const trendPct = lastActual.value > 0 ? (trend / lastActual.value) * 100 : 0;

      let html = `
        <div class="grid grid-4 mb-4">
          ${statCard('Last Month (Actual)', fmtRs(lastActual.value), 'chip-primary', SVG.chart, esc(lastActual.month))}
          ${statCard('Next Month (Forecast)', fmtRs(nextForecast.value), 'chip-info', SVG.trendUp, esc(nextForecast.month))}
          ${statCard('Trend', `${trend >= 0 ? '+' : ''}${fmtRs(trend)}`, trend >= 0 ? 'chip-warning' : 'chip-success',
              trend >= 0 ? SVG.trendUp : SVG.trendDown, `${trendPct >= 0 ? '+' : ''}${trendPct.toFixed(1)}%`)}
          ${statCard('Forecast Method', esc(forecast.method || 'Linear'), 'chip-secondary', SVG.brain, `${periods} months ahead`)}
        </div>

        <div class="card mb-4">
          <div class="card-title">
            <h3>Historical Spend & Forecast</h3>
            <span class="text-sm text-dim">Solid = actual, dashed = forecast</span>
          </div>
          <div class="chart-container"><canvas id="forecast-chart"></canvas></div>
        </div>`;

      // Forecast table
      if (forecast.forecast && forecast.forecast.length) {
        html += `
          <div class="card">
            <div class="card-title"><h3>Forecast Detail</h3></div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>Month</th><th class="table-num">Forecasted Spend</th><th class="table-num">Change vs Prev</th></tr></thead>
                <tbody>${forecast.forecast.map((f, i) => {
                  const prev = i === 0 ? lastActual.value : forecast.forecast[i - 1].value;
                  const change = f.value - prev;
                  const changePct = prev > 0 ? (change / prev) * 100 : 0;
                  return `<tr>
                    <td class="font-semibold">${esc(f.month)}</td>
                    <td class="table-num font-bold">${fmtRs(f.value)}</td>
                    <td class="table-num ${change >= 0 ? 'text-warning' : 'text-success'}">${change >= 0 ? '+' : ''}${fmtRs(change)} (${changePct.toFixed(1)}%)</td>
                  </tr>`;
                }).join('')}</tbody>
              </table>
            </div>
          </div>`;
      }

      $('#fc-out').innerHTML = html;

      // Render chart
      const ctx = $('#forecast-chart');
      if (ctx) {
        new Chart(ctx, {
          type: 'line',
          data: {
            labels: [...forecast.history.map(h => h.month), ...forecast.forecast.map(f => f.month)],
            datasets: [{
              label: 'Historical',
              data: [...forecast.history.map(h => h.value), ...forecast.forecast.map(() => null)],
              borderColor: '#ec4899',
              backgroundColor: 'rgba(236, 72, 153, 0.1)',
              tension: 0.3, borderWidth: 2, pointRadius: 3,
            }, {
              label: 'Forecast',
              data: [...forecast.history.map(() => null), forecast.history[forecast.history.length - 1].value, ...forecast.forecast.map(f => f.value)],
              borderColor: '#f59e0b',
              borderDash: [6, 4], tension: 0.3, borderWidth: 2, pointRadius: 3,
            }],
          },
          options: (() => { const t = chartTheme(); return {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { position: 'bottom', labels: { color: t.tickColor } } },
            scales: {
              y: { ticks: { color: t.tickColor }, grid: { color: t.gridColor } },
              x: { ticks: { color: t.tickColor }, grid: { display: false } },
            },
          }; })(),
        });
      }
    } catch (e) {
      $('#fc-out').innerHTML = errorBox(e.message);
    }
  }
});


// ═══════════════════════════════════════════════════════════════════════════════
// v8.16.0 — AI Market Intelligence Agent
// Searches the web for trending wholesale products + uses LLM to generate
// structured recommendations mapped to the shop's price categories.
// ═══════════════════════════════════════════════════════════════════════════════

route('/insights/market-intel', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-pink">${SVG.trendUp}</div>
      <div>
        <h2 class="pos-page-header-title">AI Market Intelligence</h2>
        <p class="pos-page-header-sub">AI searches the web for trending wholesale products and suggests what to stock at your price points.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn" id="mi-research-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span>
          Research Now
        </button>
      </div>
    </div>
    <div id="mi-out">
      <div class="card" style="padding:32px;text-align:center">
        <div style="font-size:48px;opacity:0.3;margin-bottom:12px">${SVG.trendUp}</div>
        <h3 style="margin-bottom:8px">Market Intelligence Agent</h3>
        <p class="text-dim text-sm" style="max-width:500px;margin:0 auto 16px">
          Click "Research Now" to search the web for trending wholesale products in Pakistan.
          AI will analyze results and recommend products that fit your price categories.
        </p>
        <p class="text-xs text-dim">Seasonal context will be included automatically based on the current month.</p>
      </div>
    </div>`;

  let isResearching = false;

  $('#mi-research-btn').onclick = async () => {
    if (isResearching) return;
    isResearching = true;
    const btn = $('#mi-research-btn');
    btn.disabled = true;
    btn.innerHTML = `<span style="display:inline-flex;width:14px;height:14px;animation:spin 1s linear infinite">${SVG.refresh}</span> Researching...`;
    
    $('#mi-out').innerHTML = `
      <div class="card" style="padding:48px;text-align:center">
        <div style="font-size:32px;margin-bottom:16px;animation:pulse 1.5s ease-in-out infinite">${SVG.brain}</div>
        <h3>AI is researching market trends...</h3>
        <p class="text-dim text-sm mt-2">Searching the web + analyzing with AI</p>
        <p class="text-xs text-dim mt-1">This takes 10-30 seconds</p>
      </div>`;
    
    try {
      const result = await apiPost('/api/ai/market-intelligence', {});
      renderResults(result);
    } catch (e) {
      $('#mi-out').innerHTML = errorBox(e.message, "location.reload()");
    } finally {
      isResearching = false;
      btn.disabled = false;
      btn.innerHTML = `<span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span> Research Again`;
    }
  };

  function renderResults(result) {
    const recs = result.recommendations || [];
    const searches = result.search_results || [];
    const ctx = result.shop_context || {};
    const season = ctx.seasonal_context || '';
    const cats = ctx.categories || [];

    let html = `
      <div class="card" style="padding:16px 20px;margin-bottom:16px;display:flex;gap:16px;align-items:center;flex-wrap:wrap">
        <div style="flex:1;min-width:200px">
          <strong>Generated:</strong> ${esc(result.generated_at || '')}
          ${season ? `<br><span class="text-dim text-sm">${esc(season)}</span>` : ''}
        </div>
        <div>
          ${cats.map(c => `<span class="badge badge-accent" style="margin-left:4px">${esc(c.code)}: Rs ${fmt(c.sell_price)}</span>`).join('')}
        </div>
      </div>`;

    if (recs.length === 0) {
      html += `
        <div class="card" style="padding:32px;text-align:center">
          <h3>No AI recommendations</h3>
          <p class="text-dim text-sm">The AI provider may not be configured. Check Settings → AI Providers.</p>
        </div>`;
    } else {
      html += `
        <div class="card" style="margin-bottom:16px">
          <h3 style="padding:16px 20px;border-bottom:1px solid var(--border)">AI Product Recommendations (${recs.length})</h3>
          <div class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th>Product</th>
                  <th class="table-num">Est. Wholesale Cost</th>
                  <th>Category</th>
                  <th class="table-num">Margin %</th>
                  <th>Why?</th>
                  ${recs.some(r => r.source_url) ? '<th>Source</th>' : ''}
                </tr>
              </thead>
              <tbody>
                ${recs.map(r => `
                  <tr>
                    <td style="font-weight:600">${esc(r.product_name)}</td>
                    <td class="table-num">Rs ${fmt(r.estimated_wholesale_cost)}</td>
                    <td><span class="badge badge-accent">${esc(r.suggested_category)}</span></td>
                    <td class="table-num" style="color:var(--success-text);font-weight:600">${r.estimated_margin_pct.toFixed(1)}%</td>
                    <td class="text-sm text-dim" style="max-width:300px">${esc(r.why)}</td>
                    ${recs.some(rr => rr.source_url) ? `<td>${r.source_url ? `<a href="${esc(r.source_url)}" target="_blank" class="text-xs">Link ↗</a>` : '<span class="text-dim text-xs">—</span>'}</td>` : ''}
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>`;
    }

    if (searches.length > 0) {
      html += `
        <div class="card">
          <h3 style="padding:16px 20px;border-bottom:1px solid var(--border)">Web Search Sources (${searches.length})</h3>
          <div style="padding:16px 20px">
            ${searches.map(s => `
              <div style="padding:10px 0;border-bottom:1px solid var(--border-soft)">
                <a href="${esc(s.url)}" target="_blank" style="font-weight:600;font-size:14px">${esc(s.title)}</a>
                <p class="text-sm text-dim" style="margin:4px 0 0">${esc(s.snippet)}</p>
              </div>
            `).join('')}
          </div>
        </div>`;
    }

    $('#mi-out').innerHTML = html;
  }
});
