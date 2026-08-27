// Customer Detail page — extracted from customers-pages.js (Phase 5 size fix)
import { route, navigate, reload } from '../../../router.js';
import { api, apiPost, apiPut, apiDelete } from '../../../api.js';
import { $, $$, esc, fmt, fmtRs, fmtDate, toast, openModal, closeModal, skeletonCards, errorBox, emptyState } from '../../../utils.js';

route('/customers/', async (el, path) => {
  const id = path.split('/').pop();
  let cust;
  try {
    cust = await api(`/api/customers/${id}`);
  } catch (e) {
    el.innerHTML = emptyState('Customer not found', esc(e.message), '', '');
    return;
  }

  const tier = getTier(cust.loyalty_points || 0);
  const loyaltyValue = (cust.loyalty_points || 0) * (cust.loyalty_rate || 1);

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-info">${SVG.users}</div>
      <div>
        <h2 class="pos-page-header-title">${esc(cust.name)}</h2>
        <p class="pos-page-header-sub">
          ${esc(cust.phone || 'No phone')}
          ${cust.address ? ' &middot; ' + esc(cust.address) : ''}
          &middot; Member since ${fmtDate(cust.created_at)}
        </p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-secondary btn-sm" id="cd-back-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.arrowLeft}</span>
          Back
        </button>
        <button class="btn btn-secondary btn-sm" id="cd-edit-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.edit}</span>
          Edit
        </button>
        ${cust.total_credit > 0 ? `<button class="btn btn-sm" id="cd-pay-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.wallet}</span>
          Record Payment
        </button>` : ''}
        <button class="btn btn-danger btn-sm" id="cd-delete-btn">${SVG.trash}</button>
      </div>
    </div>

    <div class="grid grid-4 mb-4">
      ${statCard('Total Spent', fmtRs(cust.total_spent), 'chip-primary', SVG.wallet)}
      ${statCard('Outstanding Credit', fmtRs(cust.total_credit), cust.total_credit > 0 ? 'chip-danger' : 'chip-success', SVG.alert)}
      ${statCard('Loyalty Points', fmt(cust.loyalty_points || 0), 'chip-warning', SVG.star, `Worth ${fmtRs(loyaltyValue)}`)}
      ${statCard('Tier', `<span class="badge ${tier.chip}" style="font-size:14px">${tier.name}</span>`, tier.chip, SVG.gift, `Redeemed: ${fmt(cust.loyalty_redeemed || 0)}`)}
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-title"><h3>Recent Sales</h3></div>
        <div id="cd-sales"></div>
      </div>
      <div class="card">
        <div class="card-title"><h3>Recent Payments</h3></div>
        <div id="cd-payments"></div>
      </div>
    </div>

    <div class="card mt-4">
      <div class="card-title"><h3>Loyalty Redemption History</h3></div>
      <div id="cd-redemptions"></div>
    </div>`;

  $('#cd-back-btn').onclick = () => navigate('/customers');
  $('#cd-edit-btn').onclick = () => openEditModal(cust);
  const payBtn = $('#cd-pay-btn');
  if (payBtn) payBtn.onclick = () => openPaymentModal(cust.id, cust.name, cust.total_credit);
  $('#cd-delete-btn').onclick = async () => {
    if (!confirm(`Delete customer "${cust.name}"? This cannot be undone.`)) return;
    try {
      await apiDelete(`/api/customers/${cust.id}`);
      toast('Customer deleted', 'success');
      navigate('/customers');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  };

  // Render recent sales
  const sales = cust.recent_sales || [];
  if (!sales.length) {
    $('#cd-sales').innerHTML = '<p class="text-dim text-sm">No sales recorded yet.</p>';
  } else {
    $('#cd-sales').innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr><th>Invoice</th><th>Date</th><th class="table-num">Total</th><th>Status</th></tr></thead>
          <tbody>${sales.map(s => `<tr class="cd-sale-row" data-id="${s.id}" style="cursor:pointer">
            <td class="font-mono text-sm">${esc(s.invoice_no || '#' + s.id)}</td>
            <td class="text-sm">${fmtDate(s.created_at)}</td>
            <td class="table-num font-semibold">${fmtRs(s.total)}</td>
            <td><span class="badge ${s.payment_status === 'paid' ? 'badge-success' : s.payment_status === 'refunded' ? 'badge-warning' : 'badge-danger'}">${esc(s.payment_status)}</span></td>
          </tr>`).join('')}</tbody>
        </table>
      </div>`;
    $$('.cd-sale-row').forEach(row => {
      row.onclick = () => navigate('/pos/sale/' + row.dataset.id);
    });
  }

  // Render recent payments
  const payments = cust.recent_payments || [];
  if (!payments.length) {
    $('#cd-payments').innerHTML = '<p class="text-dim text-sm">No payments recorded yet.</p>';
  } else {
    $('#cd-payments').innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr><th>Date</th><th class="table-num">Amount</th><th>Method</th><th>Notes</th></tr></thead>
          <tbody>${payments.map(p => `<tr>
            <td class="text-sm">${fmtDate(p.created_at)}</td>
            <td class="table-num text-success font-semibold">${fmtRs(p.amount)}</td>
            <td><span class="badge badge-success">${esc(p.payment_method)}</span></td>
            <td class="text-sm">${esc(p.notes || '—')}</td>
          </tr>`).join('')}</tbody>
        </table>
      </div>`;
  }

  // Load redemption history
  try {
    const r = await api(`/api/customers/${cust.id}/loyalty-redemptions`);
    const redemptions = r.redemptions || [];
    if (!redemptions.length) {
      $('#cd-redemptions').innerHTML = '<p class="text-dim text-sm">No redemptions yet.</p>';
    } else {
      $('#cd-redemptions').innerHTML = `
        <div class="table-wrap">
          <table>
            <thead><tr><th>Date</th><th class="table-num">Points</th><th class="table-num">Rupee Value</th><th>Sale</th></tr></thead>
            <tbody>${redemptions.map(r => `<tr>
              <td class="text-sm">${fmtDate(r.created_at)}</td>
              <td class="table-num font-semibold">${fmt(r.points_used)}</td>
              <td class="table-num text-success font-semibold">${fmtRs(r.rupee_value)}</td>
              <td>${r.sale_id ? `<a href="#/pos/sale/${r.sale_id}" class="text-dim text-sm">Sale #${r.sale_id}</a>` : '<span class="text-dim text-xs">—</span>'}</td>
            </tr>`).join('')}</tbody>
          </table>
        </div>`;
    }
  } catch (e) {
    $('#cd-redemptions').innerHTML = `<p class="text-dim text-sm">Could not load redemptions.</p>`;
  }

  function openEditModal(c) {
    openModal(
      'Edit Customer',
      `
      <div><label>Name</label><input class="input" id="ec-name" value="${esc(c.name)}"></div>
      <div class="mt-3"><label>Phone</label><input class="input" id="ec-phone" value="${esc(c.phone || '')}"></div>
      <div class="mt-3"><label>Address</label><input class="input" id="ec-address" value="${esc(c.address || '')}"></div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="ec-save-btn">
         <span style="display:inline-flex;width:14px;height:14px">${SVG.save}</span>
         Save Changes
       </button>`
    );
    $('#ec-save-btn').onclick = async () => {
      try {
        await apiPut(`/api/customers/${c.id}`, {
          name: $('#ec-name').value,
          phone: $('#ec-phone').value,
          address: $('#ec-address').value,
        });
        toast('Customer updated', 'success');
        closeModal();
        reload();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }

  function openPaymentModal(customerId, name, amount) {
    openModal(
      'Record Payment',
      `
      <div class="stat-list mb-4">
        <div class="stat-row"><span>Customer</span><span>${esc(name)}</span></div>
        <div class="stat-row"><span>Outstanding</span><span class="font-bold text-danger">${fmtRs(amount)}</span></div>
      </div>
      <div class="mt-2">
        <label>Amount (Rs)</label>
        <input class="input" id="dp-amount" type="number" value="${amount}" min="0" max="${amount}" step="0.01" autofocus>
      </div>
      <div class="mt-2">
        <label>Payment Method</label>
        <select class="select" id="dp-method">
          <option value="cash">Cash</option>
          <option value="card">Card</option>
          <option value="online">Online Transfer</option>
        </select>
      </div>
      <div class="mt-2">
        <label>Notes</label>
        <input class="input" id="dp-notes" placeholder="Optional notes">
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="dp-save-btn">
         <span style="display:inline-flex;width:14px;height:14px">${SVG.save}</span>
         Record
       </button>`
    );
    $('#dp-save-btn').onclick = async () => {
      const amt = parseFloat($('#dp-amount').value) || 0;
      const method = $('#dp-method').value;
      const notes = $('#dp-notes').value;
      if (amt <= 0) { toast('Enter amount', 'error'); return; }
      try {
        await apiPost('/api/customers/payments', {
          customer_id: customerId,
          customer_name: name,
          amount: amt,
          payment_method: method,
          notes,
        });
        toast(`Payment recorded: ${fmtRs(amt)} from ${name}`, 'success');
        closeModal();
        reload();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }
});
