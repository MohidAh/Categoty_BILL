// v7.2 — AI Usage Dashboard
// Stat cards + 14-day Chart.js chart + recent failures table + clear cache + TTL legend.

import { route } from '../router.js';
import { api, apiPost } from '../api.js';
import { $, esc, toast, openModal, closeModal, skeletonCards, errorBox, chartTheme, chartOptions } from '../utils.js';

const SVG = {
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  power: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>',
  brain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
};

let _chart = null;

route('/insights/ai-usage', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-pink">${SVG.chart}</div>
      <div>
        <h2 class="pos-page-header-title">AI Usage Dashboard</h2>
        <p class="pos-page-header-sub">Monitor AI calls, token usage, cache performance, and budget remaining.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-sm" id="ai-clear-cache" title="Clear all cached AI responses">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.trash}</span>
          Clear Cache
        </button>
        <button class="btn btn-sm" id="ai-kill-toggle">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.power}</span>
          <span id="ai-kill-label">Loading...</span>
        </button>
      </div>
    </div>
    <div id="aiu-out">${skeletonCards(3)}</div>`;

  await loadData();

  async function loadData() {
    try {
      const [usage, killStatus, config, history14d, failures, ttl] = await Promise.all([
        api('/api/ai/usage'),
        api('/api/ai/kill-switch'),
        api('/api/automation-config'),
        api('/api/ai/usage/14d'),
        api('/api/ai/failures?limit=20'),
        api('/api/ai/ttl-legend'),
      ]);
      renderUsage(usage, killStatus, config, history14d, failures, ttl);
    } catch (e) {
      $('#aiu-out').innerHTML = errorBox(e.message);
    }
  }

  function renderUsage(usage, killStatus, config, history14d, failures, ttl) {
    const isDisabled = killStatus.disabled;
    const killBtn = $('#ai-kill-toggle');
    const killLabel = $('#ai-kill-label');
    killLabel.textContent = isDisabled ? 'Enable AI' : 'Disable AI';
    killBtn.className = 'btn btn-sm ' + (isDisabled ? 'btn-success' : 'btn-danger');
    killBtn.onclick = async () => {
      try {
        await apiPost('/api/ai/kill-switch', { enabled: isDisabled ? 0 : 1 });
        toast(isDisabled ? 'AI enabled' : 'AI disabled — heuristics continue', 'success');
        await loadData();
      } catch (e) { toast('Toggle failed: ' + e.message, 'error'); }
    };

    $('#ai-clear-cache').onclick = async () => {
      if (!confirm('Clear ALL cached AI responses? Next AI call will hit the provider again.')) return;
      try {
        const r = await apiPost('/api/ai/clear-cache', {});
        toast(`Cleared ${r.deleted} cached entr${r.deleted === 1 ? 'y' : 'ies'}`, 'success');
        await loadData();
      } catch (e) { toast('Clear failed: ' + e.message, 'error'); }
    };

    const providers = usage.providers || {};
    const totalCalls = Object.values(providers).reduce((s, p) => s + p.calls, 0);
    const totalApiCalls = Object.values(providers).reduce((s, p) => s + p.api_calls, 0);
    const totalCacheHits = Object.values(providers).reduce((s, p) => s + p.cache_hits, 0);
    const totalTokens = Object.values(providers).reduce((s, p) => s + p.tokens, 0);
    const cacheHitRate = totalCalls > 0 ? Math.round(totalCacheHits / totalCalls * 100) : 0;
    const failureCount = (failures.failures || []).length;

    // 14-day totals (for hero card)
    const days = history14d.days || [];
    const totalCalls14d = days.reduce((s, d) => s + (d.calls || 0), 0);
    const totalApiCalls14d = days.reduce((s, d) => s + (d.api_calls || 0), 0);
    const totalCacheHits14d = days.reduce((s, d) => s + (d.cache_hits || 0), 0);

    // Build provider cards
    let providerHtml = '';
    for (const [name, p] of Object.entries(providers)) {
      const budgetPct = p.budget_limit > 0 ? Math.round(p.api_calls / p.budget_limit * 100) : 0;
      const barColor = budgetPct > 80 ? 'var(--danger,#DC2626)' : budgetPct > 50 ? 'var(--warning,#D97706)' : 'var(--success,#16A34A)';
      providerHtml += `<div class="card" style="padding:16px;margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <strong style="text-transform:capitalize">${esc(name)}</strong>
          <span class="chip ${budgetPct > 80 ? 'chip-danger' : 'chip-success'} chip-sm">${p.budget_remaining} calls left</span>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;font-size:13px;margin-bottom:8px">
          <div><span class="text-dim">API calls:</span> <strong>${p.api_calls}</strong></div>
          <div><span class="text-dim">Cache hits:</span> <strong>${p.cache_hits}</strong></div>
          <div><span class="text-dim">Tokens:</span> <strong>${p.tokens.toLocaleString()}</strong></div>
        </div>
        <div style="height:6px;background:var(--bg-2,#F1F5F9);border-radius:3px;overflow:hidden">
          <div style="height:100%;width:${Math.min(100, budgetPct)}%;background:${barColor};transition:width .4s"></div>
        </div>
        <div class="text-dim text-sm" style="margin-top:4px">${p.api_calls}/${p.budget_limit} daily budget used (${budgetPct}%)</div>
      </div>`;
    }
    if (Object.keys(providers).length === 0) {
      providerHtml = '<div class="card text-center text-dim" style="padding:24px">No AI calls made today. Use the AI Assistant to generate usage data.</div>';
    }

    // Failures table
    const fails = failures.failures || [];
    let failuresHtml = '';
    if (fails.length === 0) {
      failuresHtml = '<div class="card text-center text-dim" style="padding:16px">No recent failures. All AI calls completed successfully.</div>';
    } else {
      failuresHtml = `<div class="card" style="padding:0;overflow:hidden">
        <table class="table" style="font-size:12px">
          <thead>
            <tr>
              <th>When</th><th>Task</th><th>Provider</th><th>Cached</th><th>Duration</th>
            </tr>
          </thead>
          <tbody>
            ${fails.map(f => `<tr>
              <td>${esc(f.created_at || '')}</td>
              <td><code style="background:var(--bg-2,#F1F5F9);padding:1px 4px;border-radius:3px">${esc(f.task || '?')}</code></td>
              <td>${esc(f.provider || '?')}</td>
              <td>${f.cached ? 'Yes' : 'No'}</td>
              <td>${f.duration_ms || 0}ms</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
    }

    // TTL legend
    const ttlList = ttl.ttl || [];
    let ttlHtml = '';
    for (const t of ttlList) {
      ttlHtml += `<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border,#E2E8F0)">
        <div>
          <strong>${esc(t.label)}</strong>
          <div class="text-dim text-sm" style="font-size:11px"><code>${esc(t.key)}</code></div>
        </div>
        <span class="chip chip-info chip-sm" style="white-space:normal;max-width:140px;text-align:right">${esc(t.human)}</span>
      </div>`;
    }

    // Automation config toggles
    let configHtml = '';
    for (const c of (config.config || [])) {
      if (c.key === 'ai_kill_switch') continue;
      const isEnabled = c.enabled === 1;
      configHtml += `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border,#E2E8F0)">
        <div>
          <strong>${esc(c.key.replace(/_/g, ' ').replace(/\b\w/g, x => x.toUpperCase()))}</strong>
          <div class="text-dim text-sm">Level ${c.level}</div>
        </div>
        <label style="display:flex;align-items:center;cursor:pointer">
          <input type="checkbox" data-config-key="${esc(c.key)}" ${isEnabled ? 'checked' : ''} style="width:18px;height:18px;cursor:pointer">
        </label>
      </div>`;
    }

    $('#aiu-out').innerHTML = `
      <!-- Hero stats -->
      <div class="grid grid-4" style="gap:12px;margin-bottom:16px">
        <div class="card" style="padding:16px;text-align:center">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Calls Today</div>
          <div style="font-size:28px;font-weight:700;margin-top:4px">${totalCalls}</div>
          <div class="text-dim text-sm" style="margin-top:2px">${totalCalls14d} in 14 days</div>
        </div>
        <div class="card" style="padding:16px;text-align:center">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Cache Hit Rate</div>
          <div style="font-size:28px;font-weight:700;margin-top:4px;color:var(--success-text,#16A34A)">${cacheHitRate}%</div>
          <div class="text-dim text-sm" style="margin-top:2px">${totalCacheHits}/${totalCalls} hits</div>
        </div>
        <div class="card" style="padding:16px;text-align:center">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Tokens Used</div>
          <div style="font-size:28px;font-weight:700;margin-top:4px">${totalTokens.toLocaleString()}</div>
          <div class="text-dim text-sm" style="margin-top:2px">today</div>
        </div>
        <div class="card" style="padding:16px;text-align:center">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Cached Entries</div>
          <div style="font-size:28px;font-weight:700;margin-top:4px">${usage.total_cached_entries || 0}</div>
          <div class="text-dim text-sm" style="margin-top:2px">in cache now</div>
        </div>
      </div>

      ${isDisabled ? `<div class="card" style="padding:12px;background:var(--danger-soft,#FEE2E2);border:1px solid var(--danger,#DC2626);margin-bottom:16px;display:flex;gap:12px;align-items:center">
        <span style="display:inline-flex;width:20px;height:20px;color:var(--danger-text,#DC2626)">${SVG.power}</span>
        <div style="flex:1">
          <strong style="color:var(--danger-text,#DC2626)">AI Kill Switch is ON</strong> — all AI tasks are disabled.
          <span class="text-sm" style="color:var(--danger-text,#DC2626)">Heuristic features (trends, break-even, margin alerts) continue to work.</span>
        </div>
      </div>` : ''}

      <!-- 14-day chart -->
      <div class="card" style="padding:16px;margin-bottom:16px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <h3 style="margin:0">AI Calls — Last 14 Days</h3>
          <div style="display:flex;gap:12px;font-size:12px">
            <span style="display:flex;align-items:center;gap:4px">
              <span style="display:inline-block;width:10px;height:10px;background:var(--accent,#2563EB);border-radius:2px"></span>
              API calls
            </span>
            <span style="display:flex;align-items:center;gap:4px">
              <span style="display:inline-block;width:10px;height:10px;background:var(--success,#16A34A);border-radius:2px"></span>
              Cache hits
            </span>
          </div>
        </div>
        <div style="height:240px"><canvas id="aiu-chart"></canvas></div>
      </div>

      <div class="grid grid-2" style="gap:16px">
        <!-- Provider breakdown -->
        <div>
          <h3 style="margin-bottom:12px">Provider Usage (Today)</h3>
          ${providerHtml}

          <h3 style="margin:16px 0 12px">Recent Failures (${failureCount})</h3>
          ${failuresHtml}
        </div>

        <!-- Right column: TTL legend + Automation toggles -->
        <div>
          <h3 style="margin-bottom:12px">Cache TTL Legend</h3>
          <div class="card" style="padding:12px;margin-bottom:16px">
            ${ttlHtml || '<p class="text-dim text-sm">No TTL info.</p>'}
            <div class="text-dim text-sm" style="margin-top:8px;font-size:11px;display:flex;align-items:center;gap:4px">
              <span style="display:inline-flex;width:12px;height:12px">${SVG.clock}</span>
              Cached entries auto-expire based on the TTL of their task type.
            </div>
          </div>

          <h3 style="margin-bottom:12px">Automation Toggles</h3>
          <div class="card" style="padding:12px">
            ${configHtml || '<p class="text-dim text-sm">No automations configured.</p>'}
          </div>
        </div>
      </div>`;

    // Render the 14-day chart
    renderChart(days);

    // Wire config toggles
    document.querySelectorAll('[data-config-key]').forEach(cb => {
      cb.onchange = async () => {
        const key = cb.getAttribute('data-config-key');
        try {
          await apiPost(`/api/automation-config/${key}`, { enabled: cb.checked ? 1 : 0 });
          toast(`${key} ${cb.checked ? 'enabled' : 'disabled'}`, 'success');
        } catch (e) { toast('Toggle failed: ' + e.message, 'error'); cb.checked = !cb.checked; }
      };
    });
  }

  function renderChart(days) {
    if (!window.Chart) {
      console.warn('Chart.js not loaded');
      return;
    }
    const ctx = document.getElementById('aiu-chart');
    if (!ctx) return;
    if (_chart) { _chart.destroy(); _chart = null; }
    const t = chartTheme();
    const labels = days.map(d => {
      // Show MM-DD
      const parts = (d.d || '').split('-');
      return parts.length === 3 ? `${parts[1]}-${parts[2]}` : (d.d || '');
    });
    const apiData = days.map(d => d.api_calls || 0);
    const cacheData = days.map(d => d.cache_hits || 0);
    _chart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'API calls',
            data: apiData,
            backgroundColor: t.primary,
            borderColor: t.primary,
            borderWidth: 0,
            borderRadius: 4,
            stack: 'a',
          },
          {
            label: 'Cache hits',
            data: cacheData,
            backgroundColor: t.success,
            borderColor: t.success,
            borderWidth: 0,
            borderRadius: 4,
            stack: 'a',
          },
        ],
      },
      options: chartOptions({
        maintainAspectRatio: false,
        plugins: {
          tooltip: {
            callbacks: {
              afterTitle: (items) => {
                const idx = items[0].dataIndex;
                const d = days[idx];
                return d ? `Tokens: ${(d.tokens || 0).toLocaleString()}` : '';
              },
            },
          },
        },
        scales: {
          x: { stacked: true, ticks: { color: t.tickColor, maxRotation: 0, autoSkip: true } },
          y: { stacked: true, ticks: { color: t.tickColor, precision: 0 }, grid: { color: t.gridColor }, beginAtZero: true },
        },
      }),
    });
  }
});
