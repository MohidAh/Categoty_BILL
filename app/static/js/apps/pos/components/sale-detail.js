// POS Sale Detail — extracted from pos.js Phase 5
import { route, navigate } from '../../../router.js';
import { api, apiPost, apiDelete, apiPut } from '../../../api.js';
import { $, esc, fmt, fmtRs, fmtDate, icon, toast, openModal, closeModal, errorBox } from '../../../utils.js';
import { renderKioskBar, initKioskBar } from '../../../components/kiosk-bar.js';

// v8.4: Local helper — paymentBadge was defined in pos.js but not exported
function paymentBadge(method) {
  const m = {
    cash: 'badge-success', card: 'badge-accent', online: 'badge-accent',
    credit: 'badge-danger', split: 'badge-warning',
  };
  const labels = { cash: 'Cash', card: 'Card', online: 'Online', credit: 'Credit', split: 'Split' };
  return `<span class="badge ${m[method] || 'badge-success'}">${labels[method] || method}</span>`;
}

route('/pos/sale/', async (el, path) => {
  const id = path.split('/').pop();
  // v8.18.11: guard bare/invalid id instead of firing a pointless API call
  if (!id || !/^\d+$/.test(id)) {
    el.innerHTML = '<div class="empty-state"><h3>Not found</h3></div>';
    return;
  }
  let sale;
  try { sale = await api(`/api/sales/${id}`); }
  catch { el.innerHTML = '<div class="empty-state"><h3>Not found</h3></div>'; return; }
  el.innerHTML = `
    ${renderKioskBar('/pos/sales')}
    <div class="kiosk-content">
      <div class="topbar">
        <div class="topbar-title">
          <button class="btn btn-ghost btn-icon" onclick="location.hash='/pos/sales'">${icon('arrowLeft', 16)}</button>
          <div><h1>${esc(sale.invoice_no)}</h1></div>
        </div>
        <div class="topbar-actions">
          <button class="btn btn-secondary btn-sm" onclick="window.open('/api/sales/${id}/receipt','_blank')">Print</button>
          <button class="btn btn-secondary btn-sm" onclick="sendReceipt(${id})">WhatsApp</button>
          ${sale.payment_status !== 'refunded' ? `<button class="btn btn-secondary btn-sm" onclick="refundSale(${id})">↩ Refund</button>` : ''}
          <button class="btn btn-secondary btn-sm" onclick="editSale(${id})">Edit</button>
          <button class="btn btn-danger btn-sm" onclick="deleteSaleDetail(${id})">${icon('trash', 12)}</button>
        </div>
      </div>
      <div class="grid grid-2">
      <div class="card"><h3>Details</h3><div class="stat-list mt-3">
        <div class="stat-row"><span>Customer</span><span>${esc(sale.customer_name || 'Walk-in')}</span></div>
        <div class="stat-row"><span>Phone</span><span>${esc(sale.customer_phone || '—')}</span></div>
        <div class="stat-row"><span>Payment</span><span>${paymentBadge(sale.payment_method)}</span></div>
        <div class="stat-row"><span>Status</span><span><span class="badge ${sale.payment_status==='paid'?'badge-success':sale.payment_status==='credit'?'badge-danger':'badge-warning'}">${sale.payment_status}</span></span></div>
        ${sale.split_cash ? `<div class="stat-row"><span>Split Cash</span><span>${fmtRs(sale.split_cash)}</span></div>` : ''}
        ${sale.split_card ? `<div class="stat-row"><span>Split Card</span><span>${fmtRs(sale.split_card)}</span></div>` : ''}
        ${sale.split_online ? `<div class="stat-row"><span>Split Online</span><span>${fmtRs(sale.split_online)}</span></div>` : ''}
        <div class="stat-row"><span>Subtotal</span><span>${fmtRs(sale.subtotal)}</span></div>
        ${sale.discount > 0 ? `<div class="stat-row"><span>Discount</span><span class="text-danger">−${fmtRs(sale.discount)}</span></div>` : ''}
        ${sale.loyalty_discount > 0 ? `<div class="stat-row"><span>Loyalty (−${sale.loyalty_points_used} pts)</span><span class="text-danger">−${fmtRs(sale.loyalty_discount)}</span></div>` : ''}
        <div class="stat-row" style="border-top:2px solid var(--border)"><span class="font-bold">Total</span><span class="font-bold">${fmtRs(sale.total)}</span></div>
        ${sale.payment_status === 'partial' || (sale.split_cash + sale.split_card + sale.split_online > 0 && sale.split_cash + sale.split_card + sale.split_online < sale.total) ? `
          <div class="stat-row"><span>Paid</span><span class="text-success">${fmtRs(sale.split_cash + sale.split_card + sale.split_online)}</span></div>
          <div class="stat-row"><span class="font-bold">Balance Due</span><span class="font-bold text-danger">${fmtRs(sale.total - sale.split_cash - sale.split_card - sale.split_online)}</span></div>
        ` : ''}
        ${sale.notes ? `<div class="stat-row"><span>Notes</span><span>${esc(sale.notes)}</span></div>` : ''}
      </div></div>
      <div class="card"><h3>Items (${sale.items.length})</h3>
        <div class="table-wrap mt-3"><table>
          <thead><tr><th>Cat</th><th>Item</th><th class="table-num">Price</th><th class="table-num">Qty</th><th class="table-num">Total</th></tr></thead>
          <tbody>${sale.items.map(i => `<tr>
            <td><span class="badge badge-accent">${i.category_code || '—'}</span></td>
            <td>${esc(i.item_name)}</td><td class="table-num">${fmtRs(i.sell_price)}</td>
            <td class="table-num">${i.qty}</td><td class="table-num font-semibold">${fmtRs(i.line_total)}</td>
          </tr>`).join('')}</tbody>
        </table></div>
      </div>
    </div>
    </div>`;
  initKioskBar();
  window.deleteSaleDetail = async (sid) => {
    if (!confirm('Delete?')) return;
    try { await apiDelete(`/api/sales/${sid}`); toast('Deleted', 'success'); navigate('/pos/sales'); }
    catch (e) { toast('Error', 'error'); }
  };
  window.refundSale = async (sid) => {
    if (!confirm('Process refund?')) return;
    try { await apiPost(`/api/sales/${sid}/refund`, {}); toast('Refunded', 'success'); reload(); }
    catch (e) { toast('Error', 'error'); }
  };
  window.editSale = (sid) => {
    openModal('Edit Sale', `
      <div class="mt-2">
        <label class="text-xs text-dim">Notes</label>
        <textarea class="input" id="edit-notes" rows="3">${esc(sale.notes || '')}</textarea>
      </div>
      <div class="mt-2">
        <label class="text-xs text-dim">Payment Method</label>
        <select class="select" id="edit-pm">
          <option value="cash" ${sale.payment_method==='cash'?'selected':''}>Cash</option>
          <option value="card" ${sale.payment_method==='card'?'selected':''}>Card</option>
          <option value="online" ${sale.payment_method==='online'?'selected':''}>Online</option>
          <option value="credit" ${sale.payment_method==='credit'?'selected':''}>Credit</option>
        </select>
      </div>
      <div class="mt-2">
        <label class="text-xs text-dim">Status</label>
        <select class="select" id="edit-ps">
          <option value="paid" ${sale.payment_status==='paid'?'selected':''}>Paid</option>
          <option value="credit" ${sale.payment_status==='credit'?'selected':''}>Credit</option>
          <option value="partial" ${sale.payment_status==='partial'?'selected':''}>Partial</option>
          <option value="refunded" ${sale.payment_status==='refunded'?'selected':''}>Refunded</option>
        </select>
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="edit-save">Save</button>`);
    $('#edit-save').onclick = async () => {
      try {
        await apiPut(`/api/sales/${sid}`, {
          notes: $('#edit-notes').value,
          payment_method: $('#edit-pm').value,
          payment_status: $('#edit-ps').value,
        });
        toast('Saved', 'success');
        closeModal();
        reload();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  };
  window.sendReceipt = async (sid) => {
    try { const r = await api(`/api/sales/${sid}/whatsapp`); if (r.url) window.open(r.url, '_blank'); else toast('No phone', 'info'); }
    catch (e) { toast('Error', 'error'); }
  };
});

// ==================================================================
// /pos/quotes — quotations list
// ==================================================================
