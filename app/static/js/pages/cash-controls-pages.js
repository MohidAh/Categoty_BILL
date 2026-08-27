// v4.0 Phase 4 — Cash & Theft Controls frontend
// Manager PIN modal, Suspicious Activity list page, Audit Trail page,
// denomination-aware shift close. All use SnowUI patterns.
import { route } from '../router.js';
import { api, apiPost } from '../api.js';
import { $, esc, fmtRs, fmtDate, toast, openModal, closeModal,
         skeletonCards, errorBox } from '../utils.js';

const SVG = {
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
};

// ─── Manager PIN modal ────────────────────────────────────────
// Returns a Promise that resolves to the PIN string if approved, or null if cancelled.
// opts.title, opts.reason (displayed to user), opts.confirmLabel
export function askManagerPin(opts = {}) {
  return new Promise((resolve) => {
    openModal(
      opts.title || 'Manager Authorization Required',
      `
      <div style="display:flex;gap:12px;align-items:center;margin-bottom:16px">
        <div style="width:40px;height:40px;background:var(--bg-warning-soft, #fef3c7);color:var(--warning-text, #d97706);border-radius:10px;display:flex;align-items:center;justify-content:center">
          <span style="display:inline-flex;width:20px;height:20px">${SVG.lock}</span>
        </div>
        <div>
          <div style="font-weight:600">${esc(opts.reason || 'This action requires manager approval.')}</div>
          ${opts.detail ? `<div class="text-dim text-sm">${esc(opts.detail)}</div>` : ''}
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Manager PIN</label>
        <input class="input" id="pin-input" type="password" inputmode="numeric" pattern="[0-9]*"
               maxlength="8" placeholder="Enter PIN" autocomplete="off" autofocus>
      </div>
      <p class="text-dim text-sm">Only employees with the Manager or Admin role can authorize this action.</p>
      `,
      `<button class="btn" data-close>Cancel</button>
       <button class="btn btn-primary" id="pin-confirm-btn">
         <span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span>
         ${esc(opts.confirmLabel || 'Authorize')}
       </button>`,
    );
    const input = $('#pin-input');
    input.focus();
    input.onkeydown = (e) => { if (e.key === 'Enter') $('#pin-confirm-btn').click(); };
    $('#pin-confirm-btn').onclick = () => {
      const pin = input.value.trim();
      if (!pin) { toast('Enter a PIN', 'error'); return; }
      closeModal();
      resolve(pin);
    };
  });
}

// ─── Denomination-aware shift close ───────────────────────────
// Renders a denomination pad inside the existing shift-end modal flow.
// opts.shiftId, opts.openingCash, opts.blind (if true, hide expected)
export async function openShiftCloseModal(opts = {}) {
  const blind = !!opts.blind;
  const denominations = [5000, 1000, 500, 100, 50, 20, 10, 5, 2, 1];

  // Fetch current expected cash (only shown if NOT blind)
  let expectedCash = null;
  if (!blind) {
    try {
      const r = await api('/api/shifts/current');
      if (r.shift) {
        // Compute expected via a quick call to /api/cash-drawer/status
        const status = await api('/api/cash-drawer/status');
        expectedCash = status.current_cash;
      }
    } catch (e) { /* ignore */ }
  }

  openModal(
    blind ? 'Blind Shift Close — Count Cash' : 'End Shift — Count Cash',
    `
    ${blind ? `
      <div class="card" style="background:var(--bg-warning-soft, #fef3c7);padding:12px;margin-bottom:16px">
        <strong>Blind close mode:</strong> Expected cash is hidden. Count the drawer honestly.
        Variance will be computed server-side after submission.
      </div>
    ` : `
      <div class="card" style="padding:12px;margin-bottom:16px;display:flex;justify-content:space-between;align-items:center">
        <span>Expected Cash in Drawer:</span>
        <strong style="font-size:18px">${expectedCash !== null ? fmtRs(expectedCash) : '—'}</strong>
      </div>
    `}
    <p class="text-sm text-dim" style="margin-bottom:8px">Enter the count of each denomination:</p>
    <div class="grid grid-2" style="gap:8px">
      ${denominations.map(d => `
        <div class="form-group" style="margin-bottom:0">
          <label class="form-label">Rs ${d} notes</label>
          <input class="input" type="number" min="0" data-denom="${d}" value="0" placeholder="0">
        </div>
      `).join('')}
      <div class="form-group" style="margin-bottom:0">
        <label class="form-label">Coins (total Rs)</label>
        <input class="input" type="number" min="0" step="0.5" data-denom="coins" value="0" placeholder="0">
      </div>
    </div>
    <div class="card" style="padding:12px;margin-top:16px;display:flex;justify-content:space-between;align-items:center">
      <span>Counted Total:</span>
      <strong id="denom-total" style="font-size:22px">Rs 0</strong>
    </div>
    <div class="form-group" style="margin-top:12px">
      <label class="form-label">Manager PIN (for blind close confirmation)</label>
      <input class="input" id="shift-mgr-pin" type="password" inputmode="numeric" maxlength="8" placeholder="Optional" autocomplete="off">
    </div>
    `,
    `<button class="btn" data-close>Cancel</button>
     <button class="btn btn-danger" id="shift-close-confirm">
       <span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span>
       Close Shift
     </button>`,
  );

  // Live-update the counted total
  function updateTotal() {
    let total = 0;
    document.querySelectorAll('[data-denom]').forEach(inp => {
      const d = inp.getAttribute('data-denom');
      const v = parseFloat(inp.value) || 0;
      if (d === 'coins') total += v;
      else total += parseInt(d, 10) * v;
    });
    $('#denom-total').textContent = fmtRs(total);
  }
  document.querySelectorAll('[data-denom]').forEach(inp => inp.oninput = updateTotal);
  updateTotal();

  $('#shift-close-confirm').onclick = async () => {
    const denom = {};
    document.querySelectorAll('[data-denom]').forEach(inp => {
      const d = inp.getAttribute('data-denom');
      const v = parseFloat(inp.value) || 0;
      if (v > 0) denom[d] = v;
    });
    const pin = $('#shift-mgr-pin').value.trim();
    try {
      const result = await apiPost('/api/shifts/end-v2', {
        denominations: denom, blind, manager_pin: pin || null,
      });
      toast(`Shift closed. Counted: ${fmtRs(result.counted_cash)}, Variance: ${fmtRs(result.variance)}`,
            result.variance === 0 ? 'success' : 'warning');
      closeModal();
      // Reload the page to show updated shift state
      window.location.reload();
    } catch (e) {
      toast('Close failed: ' + e.message, 'error');
    }
  };
}

// Make available globally for inline onclick handlers from existing shift UI
window.__openShiftCloseModal = openShiftCloseModal;
window.__askManagerPin = askManagerPin;

// ─── Suspicious Activity page (Reports app) ───────────────────
route('/reports/suspicious', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-danger">${SVG.alert}</div>
      <div>
        <h2 class="pos-page-header-title">Suspicious Activity</h2>
        <p class="pos-page-header-sub">Refunds, discount overrides, price overrides, and large shift variances.</p>
      </div>
      <div class="pos-page-header-actions"></div>
    </div>
    <div id="sa-out">${skeletonCards(2)}</div>`;

  try {
    const r = await api('/api/alerts/suspicious?limit=200');
    const alerts = r.alerts || [];
    if (alerts.length === 0) {
      $('#sa-out').innerHTML = `
        <div class="card text-center" style="padding:48px">
          <div style="width:64px;height:64px;margin:0 auto 16px;background:var(--bg-2, #f3f4f6);border-radius:50%;display:flex;align-items:center;justify-content:center;color:var(--text-dim)">
            <span style="display:inline-flex;width:32px;height:32px">${SVG.check}</span>
          </div>
          <h3 style="margin-bottom:8px">No suspicious activity</h3>
          <p class="text-dim text-sm">Refunds, discount overrides, and shift variances will appear here.</p>
        </div>`;
      return;
    }
    $('#sa-out').innerHTML = `
      <div class="card">
        <div style="overflow-x:auto">
        <table class="table">
          <thead>
            <tr>
              <th>When</th>
              <th>Event</th>
              <th>Description</th>
              <th>Manager</th>
            </tr>
          </thead>
          <tbody>
            ${alerts.map(a => {
              const meta = typeof a.metadata === 'string' ? JSON.parse(a.metadata || '{}') : (a.metadata || {});
              const isManager = meta.manager_pin_provided;
              return `<tr>
                <td class="text-sm">${esc(fmtDate(a.created_at))}</td>
                <td><span class="chip chip-danger chip-sm">${esc(meta.original_event || a.event_type)}</span></td>
                <td>${esc(a.description)}</td>
                <td>${isManager
                  ? '<span class="chip chip-success chip-sm">Authorized</span>'
                  : '<span class="chip chip-secondary chip-sm">—</span>'}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
        </div>
      </div>`;
  } catch (e) {
    $('#sa-out').innerHTML = errorBox(e.message);
  }
});

// ─── Audit Trail page (Reports app) ───────────────────────────
route('/reports/audit', async (el) => {
  const today = new Date().toISOString().slice(0, 10);
  const weekAgo = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.refresh}</div>
      <div>
        <h2 class="pos-page-header-title">Audit Trail</h2>
        <p class="pos-page-header-sub">Every system event with filters and CSV export.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-secondary btn-sm" id="audit-export-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
          Export CSV
        </button>
      </div>
    </div>

    <div class="card mb-4">
      <div class="report-filters">
        <div><label>Event Type</label>
          <select class="input" id="audit-event-type">
            <option value="">All events</option>
            <option value="suspicious">Suspicious</option>
            <option value="sale_created">Sale created</option>
            <option value="sale_refunded">Refund</option>
            <option value="bill_created">Bill created</option>
            <option value="bill_confirmed">Bill confirmed</option>
            <option value="bill_deleted">Bill deleted</option>
            <option value="expense_added">Expense added</option>
            <option value="recalc_cogs">COGS recalc</option>
            <option value="recurring_expenses_generated">Recurring generated</option>
          </select>
        </div>
        <div><label>Entity Type</label>
          <select class="input" id="audit-entity-type">
            <option value="">All</option>
            <option value="sale">Sale</option>
            <option value="bill">Bill</option>
            <option value="supplier">Supplier</option>
            <option value="expense">Expense</option>
            <option value="shift">Shift</option>
            <option value="category">Category</option>
          </select>
        </div>
        <div><label>From</label><input class="input" id="audit-start" type="date" value="${weekAgo}"></div>
        <div><label>To</label><input class="input" id="audit-end" type="date" value="${today}"></div>
        <button class="btn" id="audit-search-btn">Search</button>
      </div>
    </div>
    <div id="audit-out">${skeletonCards(2)}</div>`;

  $('#audit-search-btn').onclick = loadAudit;
  $('#audit-export-btn').onclick = exportAudit;
  await loadAudit();

  async function loadAudit() {
    const eventType = $('#audit-event-type').value;
    const entityType = $('#audit-entity-type').value;
    const start = $('#audit-start').value;
    const end = $('#audit-end').value;
    let url = '/api/activity?limit=500';
    if (eventType) url += `&event_type=${encodeURIComponent(eventType)}`;
    if (entityType) url += `&entity_type=${encodeURIComponent(entityType)}`;
    if (start) url += `&start=${start}`;
    if (end) url += `&end=${end}`;
    try {
      const r = await api(url);
      const rows = r.activity || [];
      if (rows.length === 0) {
        $('#audit-out').innerHTML = `
          <div class="card text-center text-dim" style="padding:32px">
            <p style="font-weight:600;margin-bottom:4px">No events match these filters</p>
            <p class="text-sm">Try widening the date range or clearing filters.</p>
          </div>`;
        return;
      }
      $('#audit-out').innerHTML = `
        <div class="card">
          <div style="overflow-x:auto">
          <table class="table">
            <thead>
              <tr><th>When</th><th>Event</th><th>Entity</th><th>Description</th></tr>
            </thead>
            <tbody>
              ${rows.map(a => `<tr>
                <td class="text-sm">${esc(a.created_at)}</td>
                <td><span class="chip chip-secondary chip-sm">${esc(a.event_type)}</span></td>
                <td class="text-sm">${esc(a.entity_type || '—')} ${a.entity_id ? '#' + a.entity_id : ''}</td>
                <td>${esc(a.description)}</td>
              </tr>`).join('')}
            </tbody>
          </table>
          </div>
          <div class="text-dim text-sm" style="padding:8px">${rows.length} events</div>
        </div>`;
    } catch (e) {
      $('#audit-out').innerHTML = errorBox(e.message);
    }
  }

  function exportAudit() {
    const eventType = $('#audit-event-type').value;
    const entityType = $('#audit-entity-type').value;
    const start = $('#audit-start').value;
    const end = $('#audit-end').value;
    let url = '/api/activity/export?';
    const params = [];
    if (eventType) params.push(`event_type=${encodeURIComponent(eventType)}`);
    if (entityType) params.push(`entity_type=${encodeURIComponent(entityType)}`);
    if (start) params.push(`start=${start}`);
    if (end) params.push(`end=${end}`);
    url += params.join('&');
    window.location.href = url;
  }
});
