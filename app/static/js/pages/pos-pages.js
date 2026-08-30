// POS extra pages — Returns, Shifts, Cash Drawer, Z-Report, Barcodes
// These render inside the SnowUI shell (not kiosk mode).
// Shell provides the topbar; pages render content directly (no internal topbar).
import { route, navigate, reload } from '../router.js';
import { api, apiPost, apiPut, apiDelete } from '../api.js';
import { $, $$, esc, fmt, fmtRs, fmtDate, icon, toast, showLoading, hideLoading,
         openModal, closeModal, skeletonCards, errorBox, emptyState } from '../utils.js';
import { initListState } from '../list-state.js';

// ─── Shared SVG icons (SnowUI: no emoji main icons) ───
const SVG = {
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  print: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  minus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>',
  arrowLeft: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>',
  return: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  barcode: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="4" y1="4" x2="4" y2="20"/><line x1="8" y1="4" x2="8" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/><line x1="16" y1="4" x2="16" y2="20"/><line x1="20" y1="4" x2="20" y2="20"/></svg>',
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
};

// Helper: render a stat card with SVG icon chip
function statCard(label, value, chipClass, svgIcon, sub = '') {
  return `<div class="stat-card">
    <div class="stat-card-icon ${chipClass}">${svgIcon}</div>
    <div class="stat-card-label">${esc(label)}</div>
    <div class="stat-card-value">${value}</div>
    ${sub ? `<div class="stat-card-delta">${sub}</div>` : ''}
  </div>`;
}

// ═══════════════════════════════════════════════════
// RETURNS — search sale, select items, refund/exchange
// ═══════════════════════════════════════════════════
route('/pos/returns', async (el, path, q) => {
  // v8.18.5: search + date filter persist across navigation
  const st = initListState('posReturns', q, { q: '', date: '' });
  st.syncUrlIfRestored();
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-primary">${SVG.return}</div>
      <div>
        <h2 class="pos-page-header-title">Returns & Exchanges</h2>
        <p class="pos-page-header-sub">Search a past sale to refund or exchange items.</p>
      </div>
    </div>

    <div class="card mb-4">
      <div class="filter-bar">
        <div class="search-input filter-search">
          ${SVG.search}
          <input class="input" id="r-search" placeholder="Search by invoice number or customer name..." value="${esc(st.val('q'))}">
        </div>
        <input class="input filter-select" id="r-date" type="date" style="max-width:180px" value="${esc(st.val('date'))}">
      </div>
    </div>

    <div id="r-results" class="card">
      <p class="text-dim text-sm">Search for a sale to process a return.</p>
    </div>`;

  let searchTerm = st.val('q');
  const searchInput = $('#r-search');
  searchInput.oninput = () => { searchTerm = searchInput.value; st.replace({ q: searchTerm }); loadSales(); };
  $('#r-date').onchange = () => { st.replace({ date: $('#r-date').value }); loadSales(); };

  // v8.18.5: restored search/date from a previous visit → load immediately
  if (searchTerm || st.val('date')) loadSales();

  async function loadSales() {
    const date = $('#r-date').value;
    const resultsEl = $('#r-results');
    resultsEl.innerHTML = `<div class="text-dim text-sm">Loading...</div>`;
    try {
      const sales = await api(`/api/sales?limit=50${date ? '&date=' + date : ''}`);
      const filtered = searchTerm
        ? sales.filter(s => (s.invoice_no || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
                             (s.customer_name || '').toLowerCase().includes(searchTerm.toLowerCase()))
        : sales;

      if (!filtered.length) {
        resultsEl.innerHTML = emptyState('No sales found', 'Try a different invoice number, customer name, or date.');
        return;
      }

      resultsEl.innerHTML = `
        <div class="table-wrap">
          <table class="table-clickable">
            <thead><tr>
              <th>Invoice</th><th>Date</th><th>Customer</th>
              <th class="table-num">Total</th><th>Payment</th><th>Status</th><th></th>
            </tr></thead>
            <tbody>
              ${filtered.map(s => `<tr>
                <td class="font-mono text-sm">${esc(s.invoice_no)}</td>
                <td class="text-sm">${fmtDate(s.created_at)}</td>
                <td>${esc(s.customer_name || 'Walk-in')}</td>
                <td class="table-num font-semibold">${fmtRs(s.total)}</td>
                <td><span class="badge ${s.payment_method === 'credit' ? 'badge-danger' : 'badge-success'}">${esc(s.payment_method)}</span></td>
                <td><span class="badge ${s.payment_status === 'paid' ? 'badge-success' : s.payment_status === 'refunded' ? 'badge-warning' : 'badge-danger'}">${esc(s.payment_status)}</span></td>
                <td>${s.payment_status !== 'refunded'
                  ? `<button class="btn btn-sm" onclick="window.__processReturn(${s.id})">Process Return</button>`
                  : '<span class="text-dim text-xs">Refunded</span>'}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>`;
    } catch (e) {
      resultsEl.innerHTML = errorBox(e.message);
    }
  }

  window.__processReturn = async (saleId) => {
    try {
      const sale = await api(`/api/sales/${saleId}`);
      openModal(
        `Return: ${esc(sale.invoice_no)}`,
        `
        <div class="stat-list mb-4">
          <div class="stat-row"><span>Customer</span><span>${esc(sale.customer_name || 'Walk-in')}</span></div>
          <div class="stat-row"><span>Date</span><span>${fmtDate(sale.created_at)}</span></div>
          <div class="stat-row"><span>Total</span><span class="font-bold">${fmtRs(sale.total)}</span></div>
          <div class="stat-row"><span>Payment</span><span>${esc(sale.payment_method)}</span></div>
        </div>
        <h4>Select items to return:</h4>
        <div id="return-items" class="mt-2">
          ${sale.items.map(i => `
            <div class="pos-cart-item" style="margin-bottom:6px">
              <div class="pos-cart-item-badge">${esc(i.category_code || '—')}</div>
              <div class="pos-cart-item-info">
                <div class="pos-cart-item-name">${esc(i.item_name)}</div>
                <div class="pos-cart-item-price">Rs ${fmt(i.sell_price)} each &middot; Qty: ${i.qty}</div>
              </div>
              <div style="display:flex;align-items:center;gap:6px">
                <input type="number" class="input input-sm return-qty"
                       data-item-id="${i.id}" data-sell-price="${i.sell_price}"
                       max="${i.qty}" value="0" min="0" style="width:64px">
                <span class="text-xs text-dim">/ ${i.qty}</span>
              </div>
            </div>`).join('')}
        </div>
        <div class="mt-4">
          <label>Reason</label>
          <select class="select" id="return-reason">
            <option value="defective">Defective item</option>
            <option value="wrong_item">Wrong item sold</option>
            <option value="customer_change">Customer changed mind</option>
            <option value="other">Other</option>
          </select>
        </div>
        <div class="mt-3">
          <label>Refund Method</label>
          <select class="select" id="return-method">
            <option value="cash">Cash refund</option>
            <option value="card">Card refund</option>
            <option value="store_credit">Store credit</option>
          </select>
        </div>
        <div class="mt-3" id="return-total" style="font-size:18px;font-weight:700;text-align:right">Refund: Rs 0</div>`,
        `<button class="btn btn-secondary" data-modal-close>Cancel</button>
         <button class="btn btn-danger" id="return-confirm">Process Return</button>`
      );

      // Update total when qty changes
      $$('.return-qty').forEach(input => {
        input.oninput = () => {
          let total = 0;
          $$('.return-qty').forEach(q => {
            total += (parseFloat(q.value) || 0) * parseFloat(q.dataset.sellPrice);
          });
          $('#return-total').textContent = `Refund: ${fmtRs(total)}`;
        };
      });

      $('#return-confirm').onclick = async () => {
        const items = [];
        $$('.return-qty').forEach(q => {
          const qty = parseInt(q.value) || 0;
          if (qty > 0) items.push({ sell_price: parseFloat(q.dataset.sellPrice), qty, item_name: 'Returned item' });
        });
        if (!items.length) { toast('Select at least 1 item to return', 'error'); return; }
        const reason = $('#return-reason').value;
        const method = $('#return-method').value;
        try {
          const r = await apiPost(`/api/sales/${saleId}/return`, {
            original_sale_id: saleId,
            reason, payment_method: method, exchange_items: [],
          });
          toast(`Return processed: Rs ${fmt(r.refund_amount)} refunded via ${method}`, 'success');
          closeModal();
          loadSales();
        } catch (e) { toast('Return failed: ' + e.message, 'error'); }
      };
    } catch (e) { toast('Error loading sale', 'error'); }
  };

  loadSales();
});

// ═══════════════════════════════════════════════════
// SHIFTS — start/end shift, live totals, real history
// ═══════════════════════════════════════════════════
route('/pos/shifts', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-primary">${SVG.clock}</div>
      <div>
        <h2 class="pos-page-header-title">Shifts</h2>
        <p class="pos-page-header-sub">Open and reconcile cashier shifts; review history.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn" id="shift-start-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          Start Shift
        </button>
      </div>
    </div>

    <div id="shift-active" class="mb-4"></div>

    <div class="card">
      <div class="card-title"><h3>Shift History</h3></div>
      <div id="shift-history"><p class="text-dim text-sm">Loading...</p></div>
    </div>`;

  $('#shift-start-btn').onclick = () => window.__startShiftModal();

  await loadShifts();

  async function loadShifts() {
    try {
      const [currentRes, shiftsRes] = await Promise.all([
        api('/api/shifts/current'),
        api('/api/shifts?limit=50'),
      ]);

      // Active shift card
      if (currentRes.shift) {
        const s = currentRes.shift;
        $('#shift-active').innerHTML = `
          <div class="card shift-active-card">
            <div class="shift-active-card-row">
              <div>
                <div class="badge badge-success mb-2">
                  <span class="badge-dot badge-dot-success"></span> Active Shift
                </div>
                <h3 style="margin:0">Shift #${s.id}</h3>
                <p class="text-dim text-sm mt-1">Started: ${esc(s.start_time)}</p>
                <p class="text-sm mt-1">Opening Cash: <b>${fmtRs(s.opening_cash)}</b></p>
              </div>
              <button class="btn btn-danger" id="shift-end-btn">
                <span style="display:inline-flex;width:14px;height:14px">${SVG.stop}</span>
                End Shift
              </button>
            </div>
          </div>`;
        $('#shift-end-btn').onclick = () => window.__endShiftModal(s.id, s.opening_cash);
      } else {
        $('#shift-active').innerHTML = `
          <div class="card shift-empty-card">
            <div class="shift-empty-card-icon chip-warning">${SVG.alert}</div>
            <div>
              <h3 style="margin:0 0 4px">No active shift</h3>
              <p class="text-dim text-sm" style="margin:0">Start a shift to track sales and reconcile the cash drawer.</p>
            </div>
            <button class="btn" onclick="window.__startShiftModal()">
              <span style="display:inline-flex;width:14px;height:14px">${SVG.play}</span>
              Start Now
            </button>
          </div>`;
      }

      // History table
      const shifts = shiftsRes.shifts || [];
      if (!shifts.length) {
        $('#shift-history').innerHTML = '<p class="text-dim text-sm">No shifts recorded yet.</p>';
        return;
      }
      $('#shift-history').innerHTML = `
        <div class="table-wrap">
          <table>
            <thead><tr>
              <th>ID</th><th>Employee</th><th>Start</th><th>End</th>
              <th class="table-num">Opening</th><th class="table-num">Closing</th>
              <th class="table-num">Variance</th><th>Status</th>
            </tr></thead>
            <tbody>
              ${shifts.map(s => `<tr ${s.status === 'open' ? 'class="row-open"' : ''}>
                <td class="font-mono">${s.id}</td>
                <td>${esc(s.employee_name || '—')}</td>
                <td class="text-sm">${esc(s.start_time)}</td>
                <td class="text-sm ${s.end_time ? '' : 'text-dim'}">${esc(s.end_time || '—')}</td>
                <td class="table-num">${fmtRs(s.opening_cash)}</td>
                <td class="table-num">${s.closing_cash != null ? fmtRs(s.closing_cash) : '—'}</td>
                <td class="table-num ${s.variance == null ? '' : s.variance >= 0 ? 'text-success' : 'text-danger'}">
                  ${s.variance == null ? '—' : (s.variance >= 0 ? '+' : '') + fmtRs(s.variance)}
                </td>
                <td><span class="badge ${s.status === 'open' ? 'badge-success' : 'badge-secondary'}">${esc(s.status)}</span></td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>`;
    } catch (e) {
      $('#shift-history').innerHTML = errorBox(e.message);
    }
  }

  window.__startShiftModal = () => {
    api('/api/employees').then(r => {
      const emps = r.employees || [];
      if (!emps.length) {
        toast('Add an employee first (Settings → Employees)', 'error');
        return;
      }
      openModal(
        'Start New Shift',
        `
        <div class="mt-2">
          <label>Cashier</label>
          <select class="select" id="shift-emp">
            ${emps.map(e => `<option value="${e.id}">${esc(e.name)} (${esc(e.role)})</option>`).join('')}
          </select>
        </div>
        <div class="mt-3">
          <label>Opening Cash (Rs)</label>
          <input class="input" id="shift-opening" type="number" value="5000" min="0" step="100">
        </div>`,
        `<button class="btn btn-secondary" data-modal-close>Cancel</button>
         <button class="btn" id="shift-start-confirm">Start Shift</button>`
      );
      $('#shift-start-confirm').onclick = async () => {
        try {
          const empId = parseInt($('#shift-emp').value);
          const openingCash = parseFloat($('#shift-opening').value) || 0;
          await apiPost(`/api/shifts/start?employee_id=${empId}&opening_cash=${openingCash}`, {});
          toast('Shift started', 'success');
          closeModal();
          loadShifts();
        } catch (e) { toast('Error: ' + e.message, 'error'); }
      };
    }).catch(e => toast('Error loading employees: ' + e.message, 'error'));
  };

  window.__endShiftModal = (shiftId, openingCash) => {
    // v4.0 Phase 4: use the new denomination-aware close modal.
    // Falls back to the legacy simple flow if the new modal isn't loaded.
    if (window.__openShiftCloseModal) {
      // Check if blind close is enabled in settings
      const blindEnabled = localStorage.getItem('bb-blind-close') === 'true';
      window.__openShiftCloseModal({ shiftId, openingCash, blind: blindEnabled });
      return;
    }
    // Legacy fallback (kept for safety; matches pre-Phase-4 behavior)
    openModal(
      'End Shift',
      `
      <p>Enter the counted closing cash to reconcile the drawer.</p>
      <div class="mt-3">
        <label>Opening Cash</label>
        <input class="input" value="${fmtRs(openingCash)}" disabled>
      </div>
      <div class="mt-2">
        <label>Closing Cash (counted)</label>
        <input class="input" id="shift-closing" type="number" value="${openingCash}" min="0" step="100" autofocus>
      </div>
      <div id="shift-variance" class="mt-2 text-sm"></div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn btn-danger" id="shift-end-confirm">End Shift</button>`
    );
    $('#shift-closing').oninput = () => {
      const closing = parseFloat($('#shift-closing').value) || 0;
      const diff = closing - openingCash;
      $('#shift-variance').innerHTML = `Variance: <b class="${diff >= 0 ? 'text-success' : 'text-danger'}">${diff >= 0 ? '+' : ''}${fmtRs(diff)}</b>`;
    };
    $('#shift-end-confirm').onclick = async () => {
      const closing = parseFloat($('#shift-closing').value) || 0;
      try {
        const r = await apiPost(`/api/shifts/end?closing_cash=${closing}`, {});
        if (r.error) { toast(r.error, 'error'); return; }
        toast(`Shift ended. Variance: ${fmtRs(r.difference)}`, 'success');
        closeModal();
        loadShifts();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  };
});

// ═══════════════════════════════════════════════════
// CASH DRAWER — shift-scoped, cash in/out, expected vs actual
// ═══════════════════════════════════════════════════
route('/pos/cash-drawer', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-success">${SVG.wallet}</div>
      <div>
        <h2 class="pos-page-header-title">Cash Drawer</h2>
        <p class="pos-page-header-sub">Track cash in/out movements and reconcile at shift end.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-secondary btn-sm" id="cd-in-btn">
          <span style="display:inline-flex;width:12px;height:12px">${SVG.plus}</span>
          Cash In
        </button>
        <button class="btn btn-secondary btn-sm" id="cd-out-btn">
          <span style="display:inline-flex;width:12px;height:12px">${SVG.minus}</span>
          Cash Out
        </button>
      </div>
    </div>

    <div id="cd-status" class="mb-4"><p class="text-dim text-sm">Loading...</p></div>

    <div class="card">
      <div class="card-title"><h3>Today's Transactions</h3></div>
      <div id="cd-history"><p class="text-dim text-sm">Loading...</p></div>
    </div>`;

  $('#cd-in-btn').onclick = () => window.__cashActionModal('in');
  $('#cd-out-btn').onclick = () => window.__cashActionModal('out');

  async function loadDrawer() {
    try {
      const d = await api('/api/cash-drawer');
      $('#cd-status').innerHTML = `
        <div class="grid grid-4">
          ${statCard('Current Cash', fmtRs(d.current_cash), 'chip-success', SVG.wallet)}
          ${statCard('Opening', fmtRs(d.opening_cash), 'chip-primary', SVG.play)}
          ${statCard('Entries', d.entries, 'chip-info', SVG.chart)}
          ${statCard('Status', `<span style="text-transform:capitalize">${esc(d.status)}</span>`,
                     d.status === 'open' ? 'chip-success' : 'chip-warning', SVG.alert)}
        </div>`;

      const history = d.history || [];
      $('#cd-history').innerHTML = history.length
        ? `<div class="table-wrap"><table>
            <thead><tr><th>Time</th><th>Type</th><th class="table-num">Amount</th><th>Description</th></tr></thead>
            <tbody>${history.map(h => `<tr>
              <td class="text-sm">${esc(h.created_at?.slice(11) || '')}</td>
              <td><span class="badge ${h.amount > 0 ? 'badge-success' : 'badge-danger'}">${esc(h.type)}</span></td>
              <td class="table-num ${h.amount > 0 ? 'text-success' : 'text-danger'}">${h.amount > 0 ? '+' : ''}${fmtRs(h.amount)}</td>
              <td class="text-sm">${esc(h.description || '—')}</td>
            </tr>`).join('')}</tbody>
          </table></div>`
        : '<p class="text-dim text-sm">No transactions today.</p>';
    } catch (e) {
      $('#cd-status').innerHTML = errorBox(e.message);
    }
  }

  window.__cashActionModal = (type) => {
    const isIn = type === 'in';
    openModal(
      `${isIn ? 'Cash In' : 'Cash Out'}`,
      `
      <div class="mt-2">
        <label>Amount (Rs)</label>
        <input class="input" id="cd-amount" type="number" value="0" min="0" step="100" autofocus>
      </div>
      <div class="mt-2">
        <label>Reason</label>
        <input class="input" id="cd-reason" placeholder="${isIn ? 'e.g., Float top-up' : 'e.g., Petty expense'}">
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn ${isIn ? '' : 'btn-danger'}" id="cd-confirm">${isIn ? 'Add Cash' : 'Remove Cash'}</button>`
    );
    $('#cd-confirm').onclick = async () => {
      const amount = parseFloat($('#cd-amount').value) || 0;
      const reason = $('#cd-reason').value;
      if (amount <= 0) { toast('Enter amount', 'error'); return; }
      try {
        await apiPost(`/api/cash-drawer/${type}`, { amount, description: reason });
        toast(`${isIn ? 'Cash in' : 'Cash out'} recorded`, 'success');
        closeModal();
        loadDrawer();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  };

  loadDrawer();
});

// ═══════════════════════════════════════════════════
// Z-REPORT — date picker, payment breakdown, category table, print
// ═══════════════════════════════════════════════════
route('/pos/z-report', async (el) => {
  const today = new Date().toISOString().slice(0, 10);
  el.innerHTML = `
    <div class="pos-page-header pos-zreport-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.chart}</div>
      <div>
        <h2 class="pos-page-header-title">Z-Report</h2>
        <p class="pos-page-header-sub">End-of-day reconciliation report.</p>
      </div>
      <div class="pos-page-header-actions">
        <div class="pos-date-input">
          <span class="pos-date-input-icon">${SVG.calendar}</span>
          <input class="input input-sm" id="zr-date" type="date" value="${today}">
        </div>
        <button class="btn btn-secondary btn-sm" id="zr-print-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.print}</span>
          Print
        </button>
      </div>
    </div>

    <div id="zr-out" class="card zreport-card"><p class="text-dim text-sm">Loading...</p></div>`;

  $('#zr-date').onchange = loadReport;
  $('#zr-print-btn').onclick = () => window.print();
  loadReport();

  async function loadReport() {
    const date = $('#zr-date').value;
    try {
      const r = await api(`/api/sales/z-report?date=${date}`);
      const reportDate = $('#zr-date').value;
      $('#zr-out').innerHTML = `
        <div class="zreport-print-head">
          <h2>Z-Report &mdash; ${esc(reportDate)}</h2>
          <p class="text-dim text-sm">BillBook POS &middot; Generated ${new Date().toLocaleString('en-PK')}</p>
        </div>

        <div class="zreport-grid">
          <div class="zreport-section">
            <h3 class="zreport-section-title">Sales Summary</h3>
            <div class="stat-list">
              <div class="stat-row"><span>Total Sales</span><span class="stat-value">${r.sale_count}</span></div>
              <div class="stat-row"><span>Paid</span><span class="stat-value text-success">${r.paid_count}</span></div>
              ${r.partial_count ? `<div class="stat-row"><span>Partial</span><span class="stat-value text-warning">${r.partial_count}</span></div>` : ''}
              <div class="stat-row"><span>Credit</span><span class="stat-value text-danger">${r.credit_count}</span></div>
              ${r.refunded_count ? `<div class="stat-row"><span>Refunded</span><span class="stat-value text-warning">${r.refunded_count}</span></div>` : ''}
            </div>
          </div>

          <div class="zreport-section">
            <h3 class="zreport-section-title">Payment Breakdown</h3>
            <div class="stat-list">
              <div class="stat-row" style="border-top:2px solid var(--border)">
                <span class="font-bold">Cash Expected</span>
                <span class="stat-value font-bold">${fmtRs(r.cash_expected)}</span>
              </div>
              <div class="stat-row"><span>Card</span><span class="stat-value">${fmtRs(r.card_total)}</span></div>
              <div class="stat-row"><span>Online</span><span class="stat-value">${fmtRs(r.total_online || 0)}</span></div>
              <div class="stat-row"><span>Credit Outstanding</span><span class="stat-value text-danger">${fmtRs(r.credit_total)}</span></div>
            </div>
          </div>

          <div class="zreport-section">
            <h3 class="zreport-section-title">Profitability</h3>
            <div class="stat-list">
              <div class="stat-row" style="border-top:2px solid var(--border)">
                <span class="font-bold">Net Revenue</span>
                <span class="stat-value font-bold text-success">${fmtRs(r.total_revenue || r.total_sales)}</span>
              </div>
              <div class="stat-row"><span>Cost of Goods</span><span class="stat-value">${fmtRs(r.total_cost)}</span></div>
              <div class="stat-row"><span>Gross Profit</span><span class="stat-value font-bold text-primary">${fmtRs(r.total_profit)}</span></div>
              <div class="stat-row"><span>Margin</span><span class="stat-value">${(r.margin * 100).toFixed(1)}%</span></div>
            </div>
          </div>
        </div>

        ${r.by_category && r.by_category.length ? `
          <div class="zreport-cat-section">
            <h3 class="zreport-section-title">By Category</h3>
            <div class="table-wrap">
              <table>
                <thead><tr>
                  <th>Cat</th><th class="table-num">Qty</th>
                  <th class="table-num">Revenue</th><th class="table-num">Profit</th>
                </tr></thead>
                <tbody>
                  ${r.by_category.map(c => `<tr>
                    <td><span class="badge badge-accent">${esc(c.code)}</span></td>
                    <td class="table-num">${c.qty}</td>
                    <td class="table-num">${fmtRs(c.revenue)}</td>
                    <td class="table-num text-success">${fmtRs(c.profit)}</td>
                  </tr>`).join('')}
                </tbody>
              </table>
            </div>
          </div>` : ''}
      `;
    } catch (e) {
      $('#zr-out').innerHTML = errorBox(e.message);
    }
  }
});

// ═══════════════════════════════════════════════════
// BARCODES — list category barcodes for printing
// (Moved from /more to POS app — scan in POS to add to cart)
// ═══════════════════════════════════════════════════
route('/barcodes', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-warning">${SVG.barcode}</div>
      <div>
        <h2 class="pos-page-header-title">Barcodes & QR Codes</h2>
        <p class="pos-page-header-sub">Print these barcodes and stick them on category buttons. Scan with any phone camera to instantly add items to the cart in POS.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-secondary btn-sm" id="bc-print-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.print}</span>
          Print All
        </button>
      </div>
    </div>

    <div class="card">
      <div id="barcodes-list" class="barcodes-grid">${skeletonCards(4)}</div>
    </div>`;

  $('#bc-print-btn').onclick = () => window.print();

  try {
    const r = await api('/api/barcodes');
    const list = r.barcodes || [];
    $('#barcodes-list').innerHTML = list.length
      ? list.map(b => `
          <div class="barcode-card">
            <div class="barcode-card-header">
              <span class="pos-cat-code" style="background:${esc(b.color)}">${esc(b.code)}</span>
              <div>
                <div class="font-bold">${esc(b.name)}</div>
                <div class="text-xs text-dim">Rs ${fmt(b.sell_price)}</div>
              </div>
            </div>
            <img src="${esc(b.qr_url)}" alt="QR code" class="barcode-qr" />
            <img src="${esc(b.barcode_url)}" alt="Barcode" class="barcode-barcode" />
            <div class="font-mono text-xs text-dim text-center">${esc(b.barcode_payload)}</div>
          </div>`).join('')
      : emptyState('No categories yet', 'Add price categories in Settings to generate barcodes.');
  } catch (e) {
    $('#barcodes-list').innerHTML = errorBox(e.message);
  }
});
