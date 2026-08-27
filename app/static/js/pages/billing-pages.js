// Billing extra pages — Review Queue + Payments
// These render inside the Billing app SnowUI shell.
import { route, navigate, reload } from '../router.js';
import { api, apiPost, apiPut, apiDelete } from '../api.js';
import { $, $$, esc, fmt, fmtRs, fmtDate, icon, toast, showLoading, hideLoading,
         openModal, closeModal, skeletonCards, errorBox, emptyState } from '../utils.js';

// Shared SVG icon set for billing extra pages
const SVG = {
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  checkCircle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
  flag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/></svg>',
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>',
};

// ═══════════════════════════════════════════════════
// REVIEW QUEUE — all status='review' bills with flag chips
// ═══════════════════════════════════════════════════
route('/bills/review', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-success">${SVG.checkCircle}</div>
      <div>
        <h2 class="pos-page-header-title">Review Queue</h2>
        <p class="pos-page-header-sub">Bills awaiting your review and confirmation.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-secondary btn-sm" id="rq-upload-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.upload}</span>
          Upload New
        </button>
      </div>
    </div>
    <div id="rq-content" class="card">${skeletonCards(3)}</div>`;

  $('#rq-upload-btn').onclick = () => navigate('/bills/new');

  try {
    const bills = await api('/api/bills?status=review&limit=100');
    if (!bills.length) {
      $('#rq-content').innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon" style="background:var(--success-soft);color:var(--success-text)">${SVG.check}</div>
          <h3>All caught up!</h3>
          <p>No bills waiting for review. Upload a new bill to get started.</p>
          <button class="btn mt-2" id="rq-empty-upload">
            <span style="display:inline-flex;width:14px;height:14px">${SVG.upload}</span>
            Upload Bill
          </button>
        </div>`;
      const eb = $('#rq-empty-upload');
      if (eb) eb.onclick = () => navigate('/bills/new');
      return;
    }

    $('#rq-content').innerHTML = `
      <div class="table-wrap">
        <table class="table-clickable">
          <thead><tr>
            <th>Bill #</th><th>Supplier</th><th>Date</th>
            <th class="table-num">Total</th><th>Payment</th><th>Flags</th><th></th>
          </tr></thead>
          <tbody>${bills.map(b => {
            let flags = [];
            try { flags = JSON.parse(b.flags || '[]'); } catch {}
            const flagBadges = flags.length
              ? flags.map(f => `<span class="badge badge-warning" style="margin-right:4px">${esc(f)}</span>`).join('')
              : '<span class="text-dim text-xs">—</span>';
            const total = b.written_total || b.computed_total || 0;
            return `<tr class="rq-row" data-id="${b.id}">
              <td class="font-mono text-sm">${esc(b.bill_no || '#' + b.id)}</td>
              <td>${esc(b.supplier_name || '—')}</td>
              <td class="text-sm">${fmtDate(b.bill_date)}</td>
              <td class="table-num font-semibold">${fmtRs(total)}</td>
              <td><span class="badge ${b.payment_status === 'credit' ? 'badge-danger' : 'badge-success'}">${esc(b.payment_status)}</span></td>
              <td>${flagBadges}</td>
              <td><button class="btn btn-sm" data-review="${b.id}">Review</button></td>
            </tr>`;
          }).join('')}</tbody>
        </table>
      </div>`;

    $$('.rq-row').forEach(row => {
      row.onclick = () => navigate('/bills/' + row.dataset.id);
    });
    $$('[data-review]').forEach(btn => {
      btn.onclick = (e) => { e.stopPropagation(); navigate('/bills/' + btn.dataset.review); };
    });
  } catch (e) {
    $('#rq-content').innerHTML = errorBox(e.message);
  }
});

// ═══════════════════════════════════════════════════
// PAYMENTS — outstanding credit bills + payment history
// ═══════════════════════════════════════════════════
route('/bills/payments', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-success">${SVG.wallet}</div>
      <div>
        <h2 class="pos-page-header-title">Payments</h2>
        <p class="pos-page-header-sub">Track outstanding credit and record payments from customers.</p>
      </div>
      <div class="pos-page-header-actions">
        <div class="pos-date-input">
          <span class="pos-date-input-icon">${SVG.calendar}</span>
          <input class="input input-sm" id="pm-month" type="month" value="${new Date().toISOString().slice(0, 7)}">
        </div>
      </div>
    </div>

    <div id="pm-stats" class="mb-4"></div>

    <div class="grid grid-2" style="gap:16px;align-items:start">
      <div class="card" style="padding:16px">
        <div class="card-title">
          <h3>
            <span style="display:inline-flex;width:16px;height:16px;vertical-align:-3px;margin-right:6px">${SVG.alert}</span>
            Outstanding Credit Bills
          </h3>
        </div>
        <div id="pm-outstanding"><p class="text-dim text-sm">Loading...</p></div>
      </div>
      <div class="card" style="padding:16px">
        <div class="card-title">
          <h3>
            <span style="display:inline-flex;width:16px;height:16px;vertical-align:-3px;margin-right:6px">${SVG.list}</span>
            Payment History
          </h3>
        </div>
        <div id="pm-history"><p class="text-dim text-sm">Loading...</p></div>
      </div>
    </div>`;

  $('#pm-month').onchange = loadPayments;
  await loadPayments();

  async function loadPayments() {
    try {
      const billsResp = await api('/api/bills?limit=200');
      const bills = Array.isArray(billsResp) ? billsResp : (billsResp.bills || billsResp.items || []);
      const creditBills = bills.filter(b => b.payment_status === 'credit');
      const today = new Date().toISOString().slice(0, 10);
      const selectedMonth = $('#pm-month').value;

      // Compute stats
      const totalOutstanding = creditBills.reduce((s, b) => s + (b.written_total || b.computed_total || 0), 0);
      const overdueCount = creditBills.filter(b => b.credit_due_date && b.credit_due_date < today).length;
      const overdueAmount = creditBills
        .filter(b => b.credit_due_date && b.credit_due_date < today)
        .reduce((s, b) => s + (b.written_total || b.computed_total || 0), 0);

      $('#pm-stats').innerHTML = `
        <div class="grid grid-3">
          <div class="stat-card">
            <div class="stat-card-icon chip-danger">${SVG.alert}</div>
            <div class="stat-card-label">Outstanding Bills</div>
            <div class="stat-card-value">${creditBills.length}</div>
          </div>
          <div class="stat-card">
            <div class="stat-card-icon chip-warning">${SVG.wallet}</div>
            <div class="stat-card-label">Total Outstanding</div>
            <div class="stat-card-value">${fmtRs(totalOutstanding)}</div>
          </div>
          <div class="stat-card">
            <div class="stat-card-icon chip-danger">${SVG.flag}</div>
            <div class="stat-card-label">Overdue</div>
            <div class="stat-card-value">${overdueCount} <span style="font-size:12px;color:var(--text-tertiary);font-weight:400">/ ${fmtRs(overdueAmount)}</span></div>
          </div>
        </div>`;

      // Outstanding credit bills
      if (creditBills.length === 0) {
        $('#pm-outstanding').innerHTML = `
          <div class="empty-state" style="padding:24px">
            <div class="empty-state-icon" style="background:var(--success-soft);color:var(--success-text)">${SVG.check}</div>
            <h3>No outstanding credit</h3>
            <p>All bills are paid.</p>
          </div>`;
      } else {
        $('#pm-outstanding').innerHTML = `
          <div class="table-wrap"><table>
            <thead><tr><th>Bill #</th><th>Supplier</th><th>Due Date</th><th class="table-num">Amount</th><th></th></tr></thead>
            <tbody>${creditBills.map(b => {
              const total = b.written_total || b.computed_total || 0;
              const due = b.credit_due_date;
              const isOverdue = due && due < today;
              return `<tr>
                <td class="font-mono text-sm">${esc(b.bill_no || '#' + b.id)}</td>
                <td>${esc(b.supplier_name || '—')}</td>
                <td class="text-sm ${isOverdue ? 'text-danger font-bold' : ''}">
                  ${due ? fmtDate(due) + (isOverdue ? ' &middot; overdue' : '') : '—'}
                </td>
                <td class="table-num font-semibold ${isOverdue ? 'text-danger' : ''}">${fmtRs(total)}</td>
                <td><button class="btn btn-sm" data-pay="${b.id}" data-name="${esc(b.supplier_name || '')}" data-amount="${total}">Pay</button></td>
              </tr>`;
            }).join('')}</tbody>
          </table></div>`;
      }

      // Wire up Pay buttons
      $$('[data-pay]').forEach(btn => {
        btn.onclick = () => recordPayment(
          parseInt(btn.dataset.pay),
          btn.dataset.name,
          parseFloat(btn.dataset.amount)
        );
      });

      // Payment history (customer payments)
      const payments = await api('/api/customers/payments/all');
      const pmList = payments.payments || [];
      const monthPayments = pmList.filter(p => (p.created_at || '').slice(0, 7) === selectedMonth);
      if (monthPayments.length === 0) {
        $('#pm-history').innerHTML = '<div style="text-align:center;padding:32px 16px"><div style="width:40px;height:40px;margin:0 auto 12px;background:var(--bg-2,#F1F5F9);border-radius:10px;display:flex;align-items:center;justify-content:center;color:var(--text-dim,#64748B)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:20px;height:20px"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg></div><p class="text-dim text-sm" style="margin:0">No payments recorded this month.</p></div>';
      } else {
        const monthTotal = monthPayments.reduce((s, p) => s + (p.amount || 0), 0);
        $('#pm-history').innerHTML = `
          <div class="stat-list mb-3">
            <div class="stat-row">
              <span>Month Total</span>
              <span class="stat-value font-bold text-success">${fmtRs(monthTotal)}</span>
            </div>
            <div class="stat-row">
              <span>Payments</span>
              <span class="stat-value">${monthPayments.length}</span>
            </div>
          </div>
          <div class="table-wrap"><table>
            <thead><tr><th>Date</th><th>Customer</th><th class="table-num">Amount</th><th>Method</th></tr></thead>
            <tbody>${monthPayments.map(p => `<tr>
              <td class="text-sm">${fmtDate(p.created_at)}</td>
              <td>${esc(p.customer_name || '—')}</td>
              <td class="table-num text-success font-semibold">${fmtRs(p.amount)}</td>
              <td><span class="badge badge-success">${esc(p.payment_method)}</span></td>
            </tr>`).join('')}</tbody>
          </table></div>`;
      }
    } catch (e) {
      $('#pm-outstanding').innerHTML = errorBox(e.message);
      $('#pm-history').innerHTML = '';
      $('#pm-stats').innerHTML = '';
    }
  }

  function recordPayment(billId, supplierName, amount) {
    openModal(
      'Record Payment',
      `
      <div class="stat-list mb-4">
        <div class="stat-row"><span>Bill</span><span class="font-mono">#${billId}</span></div>
        <div class="stat-row"><span>Supplier</span><span>${esc(supplierName)}</span></div>
        <div class="stat-row"><span>Outstanding</span><span class="font-bold text-danger">${fmtRs(amount)}</span></div>
      </div>
      <div class="mt-2">
        <label>Amount to Pay (Rs)</label>
        <input class="input" id="pm-amount" type="number" value="${amount}" min="0" max="${amount}" autofocus>
      </div>
      <div class="mt-2">
        <label>Payment Method</label>
        <select class="select" id="pm-method">
          <option value="cash">Cash</option>
          <option value="card">Card</option>
          <option value="online">Online Transfer</option>
        </select>
      </div>
      <div class="mt-2">
        <label>Notes (optional)</label>
        <input class="input" id="pm-notes" placeholder="e.g., Partial payment">
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="pm-confirm">Record Payment</button>`
    );
    $('#pm-confirm').onclick = async () => {
      const payAmount = parseFloat($('#pm-amount').value) || 0;
      const method = $('#pm-method').value;
      const notes = $('#pm-notes').value;
      if (payAmount <= 0) { toast('Enter amount', 'error'); return; }
      try {
        // Mark bill as paid (or partial if less than full)
        const newStatus = payAmount >= amount - 0.01 ? 'paid' : 'credit';
        await apiPut(`/api/bills/${billId}`, {
          supplier_name: supplierName,
          payment_status: newStatus,
        });
        toast(`Payment recorded: ${fmtRs(payAmount)} via ${method}${notes ? ' (' + notes + ')' : ''}`, 'success');
        closeModal();
        loadPayments();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }
});
