// Dashboard page with skeletons + error handling + sparklines + activity feed + month comparison + top suppliers + chart
import { route } from '../router.js';
import { api, apiPost } from '../api.js';
import { $, esc, fmt, fmtRs, fmtDate, icon, iconHtml, toast,
         skeletonKpis, skeletonCards, errorBox, sparkline, chartTheme } from '../utils.js';

route('/', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-pink">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>
      </div>
      <div>
        <h2 class="pos-page-header-title">Dashboard</h2>
        <p class="pos-page-header-sub">Business overview at a glance.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-secondary" id="dash-new-bill-btn">
          <span style="display:inline-flex;width:14px;height:14px">${icon('plus', 14)}</span>
          New Bill
        </button>
      </div>
    </div>
    <div id="dash-content">
      ${skeletonKpis(4)}
      <div class="mt-4">${skeletonCards(2)}</div>
    </div>`;

  const newBillBtn = $('#dash-new-bill-btn');
  if (newBillBtn) newBillBtn.onclick = () => location.hash = '/bills/new';

  try {
    const d = await api('/api/insights/dashboard');
    renderDashboard(d);
  } catch (e) {
    $('#dash-content').innerHTML = errorBox(e.message, "location.reload()");
  }
});

const ACTIVITY_ICONS = {
  bill_created: 'plus', bill_confirmed: 'check', bill_deleted: 'trash',
  bill_restored: 'backup', bill_edited: 'edit',
  supplier_created: 'suppliers', supplier_edited: 'edit', supplier_deleted: 'trash',
  backup_created: 'backup', category_changed: 'settings',
};
const ACTIVITY_COLORS = {
  bill_created: 'text-accent', bill_confirmed: 'text-success', bill_deleted: 'text-danger',
  bill_restored: 'text-success', bill_edited: 'text-warning',
  supplier_created: 'text-accent', supplier_edited: 'text-warning', supplier_deleted: 'text-danger',
  backup_created: 'text-dim', category_changed: 'text-warning',
};

function timeAgo(isoStr) {
  const d = new Date(isoStr + (isoStr.endsWith('Z') ? '' : 'Z'));
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return 'just now';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
  if (sec < 604800) return Math.floor(sec / 86400) + 'd ago';
  return fmtDate(isoStr);
}

function renderDashboard(d) {
  const k = d.kpis;
  const a = d.alerts;
  const sp = d.sparklines || {};
  const activity = d.activity || [];
  const recurring = d.recurring || [];
  const mc = d.month_comparison || {};
  const topSup = d.top_suppliers || [];
  const cats = d.category_breakdown || [];

  // Alerts
  let alertsHtml = '';
  if (recurring.length) {
    alertsHtml += `<div class="alert alert-warning">
      ${iconHtml('alert', 'alert-icon')}
      <div><strong>${recurring.length} supplier${recurring.length > 1 ? 's' : ''} missing</strong>
      ${recurring.slice(0, 2).map(r => `<div class="text-sm mt-2" style="cursor:pointer" onclick="location.hash='/suppliers/${r.supplier_id}'">
        <strong>${esc(r.supplier_name)}</strong> — last bill ${r.days_since}d ago
      </div>`).join('')}
      </div></div>`;
  }
  if (a.critical.length) {
    alertsHtml += `<div class="alert alert-danger">
      ${iconHtml('alert', 'alert-icon')}
      <div><strong>${a.critical.length} critical</strong>
      ${a.critical.slice(0, 2).map(x => `<div class="text-sm mt-2">${esc(x.message)}</div>`).join('')}
      </div></div>`;
  }
  if (a.warning.length) {
    alertsHtml += `<div class="alert alert-warning">
      ${iconHtml('alert', 'alert-icon')}
      <div><strong>${a.warning.length} warning${a.warning.length > 1 ? 's' : ''}</strong>
      ${a.warning.slice(0, 2).map(x => `<div class="text-sm mt-2">${esc(x.message)}</div>`).join('')}
      </div></div>`;
  }
  if (!a.critical.length && !a.warning.length && !recurring.length) {
    alertsHtml = `<div class="alert alert-success">${iconHtml('check', 'alert-icon')}<div><strong>All clear</strong><div class="text-sm mt-2">No active alerts.</div></div></div>`;
  }

  // Activity feed
  const activityHtml = activity.length ? `
    <div class="activity-feed">
      ${activity.map(ev => {
        const iconName = ACTIVITY_ICONS[ev.event_type] || 'check';
        const colorClass = ACTIVITY_COLORS[ev.event_type] || 'text-dim';
        const link = ev.entity_type === 'bill' && ev.entity_id ? `location.hash='/bills/${ev.entity_id}'` :
                     ev.entity_type === 'supplier' && ev.entity_id ? `location.hash='/suppliers/${ev.entity_id}'` : '';
        return `<div class="activity-item" ${link ? `onclick="${link}" style="cursor:pointer"` : ''}>
          <span class="activity-icon ${colorClass}">${icon(iconName, 13)}</span>
          <div class="activity-body">
            <div class="activity-desc">${esc(ev.description)}</div>
            <div class="activity-time">${timeAgo(ev.created_at)}</div>
          </div>
        </div>`;
      }).join('')}
    </div>` : '';

  // Month comparison
  const spentUp = mc.spent_change_pct > 0;
  const billsUp = mc.bills_change_pct > 0;
  const mcHtml = `
    <div class="card">
      <div class="card-title"><h3>This Month vs Last Month</h3></div>
      <div class="stat-list mt-3">
        <div class="stat-row">
          <span class="stat-label">Spent this month</span>
          <span class="stat-value">${fmtRs(mc.this_month_spent)}</span>
        </div>
        <div class="stat-row">
          <span class="stat-label">Spent last month</span>
          <span class="stat-value text-dim">${fmtRs(mc.last_month_spent)}</span>
        </div>
        <div class="stat-row">
          <span class="stat-label">Change</span>
          <span class="stat-value ${spentUp ? 'text-danger' : 'text-success'}">
            ${spentUp ? '↑' : '↓'} ${Math.abs(mc.spent_change_pct)}%
          </span>
        </div>
        <div class="stat-row">
          <span class="stat-label">Bills this month</span>
          <span class="stat-value">${mc.this_month_bills}</span>
        </div>
        <div class="stat-row">
          <span class="stat-label">Bills last month</span>
          <span class="stat-value text-dim">${mc.last_month_bills}</span>
        </div>
        <div class="stat-row">
          <span class="stat-label">Change</span>
          <span class="stat-value ${billsUp ? 'text-warning' : 'text-success'}">
            ${billsUp ? '↑' : '↓'} ${Math.abs(mc.bills_change_pct)}%
          </span>
        </div>
      </div>
    </div>`;

  // Top suppliers
  const topSupHtml = topSup.length ? `
    <div class="card">
      <div class="card-title"><h3>Top Suppliers</h3></div>
      <div class="table-wrap">
        <table class="table-clickable">
          <thead><tr><th>Supplier</th><th class="table-num">Bills</th><th class="table-num">Spent</th></tr></thead>
          <tbody>
            ${topSup.map((s, i) => `<tr onclick="location.hash='/suppliers/${s.name ? 'search' : ''}'" style="cursor:default">
              <td><span class="text-dim">${i + 1}.</span> <span class="font-semibold">${esc(s.name)}</span></td>
              <td class="table-num">${s.bill_count}</td>
              <td class="table-num">${fmtRs(s.total_spent)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>` : '';

  // Category breakdown this month
  const catHtml = cats.length ? `
    <div class="card">
      <div class="card-title"><h3>This Month by Category</h3></div>
      <div class="stat-list">
        ${cats.map(c => `<div class="stat-row">
          <span class="stat-label flex items-center gap-2">
            <span class="tag-chip" style="background:${c.color || 'var(--primary)'};width:8px;height:8px;padding:0;border-radius:50%"></span>
            ${esc(c.name || 'Uncategorized')} (Rs ${fmt(c.sell_price)})
          </span>
          <span class="stat-value">${fmtRs(c.cost)}</span>
        </div>`).join('')}
      </div>
    </div>` : '';

  // Spending chart (14-day)
  const chartHtml = sp.labels && sp.labels.length ? `
    <div class="card">
      <div class="card-title"><h3>14-Day Spending Trend</h3></div>
      <div class="chart-container"><canvas id="dash-chart"></canvas></div>
    </div>` : '';

  $('#dash-content').innerHTML = `
    <!-- KPI Row -->
    <div class="grid grid-4 dashboard-kpis">
      <div class="kpi">
        <div class="kpi-label">${icon('bills', 12)} Total Bills</div>
        <div class="kpi-value">${fmt(k.total_bills)}</div>
        <div class="kpi-sub">${k.confirmed} confirmed · ${k.review} in review</div>
        ${sp.bills ? `<div class="kpi-sparkline" style="color:var(--accent-text)">${sparkline(sp.bills)}</div>` : ''}
      </div>
      <div class="kpi kpi-success">
        <div class="kpi-label">${icon('wallet', 12)} Total Spent</div>
        <div class="kpi-value">${fmtRs(k.total_spent)}</div>
        <div class="kpi-sub">${mc.this_month_bills || 0} bills this month</div>
        ${sp.spend ? `<div class="kpi-sparkline" style="color:var(--success-text)">${sparkline(sp.spend)}</div>` : ''}
      </div>
      <div class="kpi ${k.outstanding > 0 ? 'kpi-danger' : ''}">
        <div class="kpi-label">${icon('alert', 12)} Outstanding</div>
        <div class="kpi-value">${fmtRs(k.outstanding)}</div>
        <div class="kpi-sub">Urdhaar (credit)</div>
        ${sp.outstanding ? `<div class="kpi-sparkline" style="color:var(--danger-text)">${sparkline(sp.outstanding)}</div>` : ''}
      </div>
      <div class="kpi kpi-accent">
        <div class="kpi-label">${icon('users', 12)} Suppliers</div>
        <div class="kpi-value">${fmt(k.suppliers)}</div>
        <div class="kpi-sub">Active relationships</div>
        ${sp.suppliers ? `<div class="kpi-sparkline" style="color:var(--accent-text)">${sparkline(sp.suppliers)}</div>` : ''}
      </div>
    </div>

    <!-- Main 3-column row -->
    <div class="dashboard-row-3">
      <div class="card">
        <div class="card-title">
          <h3>Recent Bills</h3>
          <button class="btn btn-ghost btn-sm" onclick="location.hash='/bills'">View all →</button>
        </div>
        ${d.recent.length ? `
          <div class="table-wrap">
            <table class="table-clickable">
              <thead><tr><th>ID</th><th>Supplier</th><th>Date</th><th class="table-num">Total</th><th>Status</th></tr></thead>
              <tbody>
                ${d.recent.slice(0, 8).map(b => `<tr onclick="location.hash='/bills/${b.id}'">
                  <td class="text-dim">#${b.id}</td>
                  <td>${esc(b.supplier_name || '—')}</td>
                  <td class="text-sm">${fmtDate(b.bill_date)}</td>
                  <td class="table-num">${fmtRs(b.total)}</td>
                  <td><span class="badge ${b.status === 'confirmed' ? 'badge-success' : 'badge-warning'} badge-dot">${b.status}</span></td>
                </tr>`).join('')}
              </tbody>
            </table>
          </div>` : `<div class="empty-state" style="padding:24px"><div class="empty-state-icon">${icon('inbox')}</div><h3>No bills yet</h3><p>Upload your first bill</p><button class="btn btn-sm" id="dash-empty-upload">${icon('upload', 12)} Upload</button></div>`}
      </div>
      <div class="card">
        <div class="card-title"><h3>Alerts</h3></div>
        ${alertsHtml}
      </div>
      <div class="card">
        <div class="card-title"><h3>Recent Activity</h3></div>
        ${activityHtml || '<p class="text-dim text-sm">No activity yet.</p>'}
      </div>
    </div>

    <!-- Second row: chart + month comparison + top suppliers -->
    <div class="dashboard-row-3 mt-4">
      ${chartHtml}
      ${mcHtml}
      ${topSupHtml}
    </div>

    <!-- v8.4: Market Trends full-width row -->
    <div class="card mt-4">
      <div class="card-title">
        <h3>${icon('trendUp', 16)} Market Trends & Reorder</h3>
        <button class="btn btn-ghost btn-sm" onclick="refreshTrends()">Refresh Trends</button>
      </div>
      <div id="trend-alerts-container">
        <p class="text-dim text-sm">Click "Refresh Trends" to fetch latest market opportunities from Google Trends + AI analysis.</p>
      </div>
      <div id="reorder-container" class="mt-4"></div>
    </div>

    <!-- Quick actions -->
    <div class="card mt-4">
      <div class="card-title"><h3>Quick Actions</h3></div>
      <div class="flex gap-2 flex-wrap">
        <button class="btn" onclick="location.hash='/bills/new'">${icon('upload', 14)} Upload Bill</button>
        <button class="btn btn-secondary" onclick="location.hash='/items'">${icon('search', 14)} Search Items</button>
        <button class="btn btn-secondary" onclick="location.href='/api/export/bills.xlsx'">${icon('download', 14)} Export</button>
        <button class="btn btn-secondary" onclick="location.href='/api/reports/monthly-close.pdf?year=${new Date().getFullYear()}&month=${String(new Date().getMonth() + 1).padStart(2,'0')}'">${icon('download', 14)} Monthly PDF</button>
      </div>
      ${k.review > 0 ? `<div class="alert alert-warning mt-4">${iconHtml('alert', 'alert-icon')}<div><strong>${k.review} bill${k.review > 1 ? 's' : ''} need review</strong> — <a href="#/bills?status=review">review now →</a></div></div>` : ''}
    </div>`;

  // Load trend alerts + reorder + seasonal + dead stock asynchronously
  loadTrendAlerts();
  loadReorderReminders();
  loadSeasonalAlerts();
  loadDeadStock();

  async function loadTrendAlerts() {
    try {
      const r = await api('/api/trends');
      const container = $('#trend-alerts-container');
      if (r.alerts && r.alerts.length) {
        container.innerHTML = r.alerts.slice(0, 5).map(a => `
          <div class="alert alert-info mb-2">
            <div>
              <strong>${esc(a.keyword)}</strong>
              <span class="badge ${a.priority === 'high' ? 'badge-danger' : a.priority === 'medium' ? 'badge-warning' : ''}">${a.priority || 'low'}</span>
              <div class="text-sm mt-2">${esc(a.suggestion)}</div>
              ${a.reasoning ? `<div class="text-xs text-dim mt-1">${esc(a.reasoning)}</div>` : ''}
              <div class="flex gap-2 mt-2">
                <button class="btn btn-secondary btn-sm" onclick="actTrend(${a.id})">Acted on</button>
                <button class="btn btn-ghost btn-sm" onclick="dismissTrend(${a.id})">Dismiss</button>
              </div>
            </div>
          </div>`).join('');
      } else {
        container.innerHTML = '<p class="text-dim text-sm">No trend alerts yet. Click "Refresh Trends" to analyze market opportunities.</p>';
      }
    } catch (e) {
      // Silent fail — trends are optional
    }
  }

  async function loadReorderReminders() {
    try {
      const r = await api('/api/reorder-reminders');
      const container = $('#reorder-container');
      if (r.reminders && r.reminders.length) {
        container.innerHTML = '<h4 class="mb-2">Reorder Reminders</h4>' + r.reminders.slice(0, 5).map(rem => `
          <div class="alert ${rem.priority === 'high' ? 'alert-danger' : 'alert-warning'} mb-2">
            <div>
              <strong>${esc(rem.item_name)}</strong>
              <span class="badge ${rem.priority === 'high' ? 'badge-danger' : 'badge-warning'}">${rem.priority}</span>
              <div class="text-sm mt-1">Last bought ${rem.days_since}d ago (avg gap ${rem.avg_gap_days}d) — suggest ${rem.suggested_quantity} pcs${rem.supplier_name ? ` from ${esc(rem.supplier_name)}` : ''}</div>
            </div>
          </div>`).join('');
      } else {
        container.innerHTML = '<p class="text-dim text-sm">No reorder reminders — need 3+ purchases of an item to establish a pattern.</p>';
      }
    } catch (e) {
      // Silent fail
    }
  }

  window.refreshTrends = async () => {
    const container = $('#trend-alerts-container');
    container.innerHTML = '<div class="alert alert-info"><span class="spinner-sm"></span> Fetching trends + AI analysis... (may take 10-30s)</div>';
    try {
      await apiPost('/api/trends/refresh', {});
      toast('Trend analysis complete!', 'success');
      loadTrendAlerts();
    } catch (e) {
      container.innerHTML = `<div class="alert alert-danger">Error: ${esc(e.message)}</div>`;
    }
  };
  window.actTrend = async (id) => {
    await apiPost(`/api/trends/${id}/acted`, {});
    toast('Marked as acted on', 'success');
    loadTrendAlerts();
  };
  window.dismissTrend = async (id) => {
    await apiPost(`/api/trends/${id}/dismiss`, {});
    loadTrendAlerts();
  };

  async function loadSeasonalAlerts() {
    try {
      const r = await api('/api/trends/seasonal');
      const container = $('#trend-alerts-container');
      if (r.alerts && r.alerts.length) {
        const seasonalHtml = r.alerts.map(a => `
          <div class="alert ${a.priority === 'high' ? 'alert-warning' : 'alert-info'} mb-2">
            <div>${esc(a.message)}</div>
          </div>`).join('');
        container.insertAdjacentHTML('afterbegin', seasonalHtml);
      }
    } catch (e) { /* silent */ }
  }

  async function loadDeadStock() {
    try {
      const r = await api('/api/trends/dead-stock');
      const container = $('#reorder-container');
      if (r.alerts && r.alerts.length) {
        const deadHtml = '<h4 class="mb-2 mt-4">Dead Stock Clearance</h4>' +
          r.alerts.slice(0, 3).map(a => `
            <div class="alert alert-danger mb-2">
              <div>
                <strong>${esc(a.item_name)}</strong> — ${a.days_since}d unsold
                <div class="text-sm mt-1">${esc(a.action)} — ${a.suggested_discount} off (tied capital: ${fmtRs(a.tied_capital)})</div>
              </div>
            </div>`).join('');
        container.insertAdjacentHTML('beforeend', deadHtml);
      }
    } catch (e) { /* silent */ }
  }

  // Draw spending chart
  if (sp.labels && sp.spend) {
    const ctx = $('#dash-chart');
    if (ctx) {
      new Chart(ctx, {
        type: 'bar',
        data: {
          labels: sp.labels,
          datasets: [{
            label: 'Daily Spend (Rs)',
            data: sp.spend,
            backgroundColor: chartTheme().primarySoft,
            borderColor: chartTheme().primary,
            borderWidth: 1,
            borderRadius: 3,
          }],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            y: { ticks: { color: chartTheme().tickColor, font: { size: 10 } }, grid: { color: chartTheme().gridColor } },
            x: { ticks: { color: chartTheme().tickColor, font: { size: 9 }, maxRotation: 45 }, grid: { display: false } },
          },
        },
      });
    }
  }

  // Wire up empty-state upload button
  const emptyUpload = $('#dash-empty-upload');
  if (emptyUpload) emptyUpload.onclick = () => location.hash = '/bills/new';
}
