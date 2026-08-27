// HoldsManager + QuotesManager — hold/recall/quote (Phase 5 extraction)
import { $, $$, esc, fmt, fmtRs, fmtDate, toast, openModal, closeModal, icon } from '../../../utils.js';
import { api, apiPost, apiDelete } from '../../../api.js';

// v8.4: Local helper — fmtTime was defined in pos.js but not exported to this module
function fmtTime(ts) {
  if (!ts) return '';
  return ts.slice(11, 16);
}

  async function holdCart() {
    if (!cart.length) { toast('Cart is empty', 'info'); return; }
    try {
      const total = cart.reduce((s, i) => s + i.price * i.qty, 0) - (discountType === 'percent' ? cart.reduce((s, i) => s + i.price * i.qty, 0) * discountVal/100 : discountVal);
      const r = await apiPost('/api/pos/holds', {
        customer_name: $('#cust-name').value,
        customer_phone: $('#cust-phone').value,
        notes: $('#sale-notes').value,
        items: cart.map(i => ({ catId: i.catId, code: i.code, price: i.price, qty: i.qty, name: i.name })),
        discount: discountVal, discount_type: discountType, total,
      });
      toast(`Parked as ${r.reference}`, 'success');
      clearCart();
      holdsCount++;
      $('#holds-badge').textContent = holdsCount;
      $('#holds-badge').style.display = 'inline-block';
      loadHoldsPreview();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  async function loadHoldsPreview() {
    try {
      const r = await api('/api/pos/holds');
      const list = r.holds || [];
      holdsCount = list.length;
      $('#holds-badge').textContent = holdsCount;
      $('#holds-badge').style.display = holdsCount > 0 ? 'inline-block' : 'none';
      const card = $('#pos-holds-card');
      if (!list.length) { card.style.display = 'none'; return; }
      card.style.display = 'block';
      $('#pos-holds-list').innerHTML = list.slice(0, 5).map(h => `
        <div class="hold-row">
          <div><b>${esc(h.reference)}</b> <span class="text-dim text-sm">${fmtTime(h.created_at)}</span></div>
          <div class="text-sm">${esc(h.customer_name || 'Walk-in')} · ${h.items?.length || 0} items · Rs ${fmt(h.total)}</div>
          <div class="flex gap-1 mt-1">
            <button class="btn btn-sm" data-recall="${h.id}">Recall</button>
            <button class="btn btn-ghost btn-sm" data-del-hold="${h.id}">${icon('trash', 12)}</button>
          </div>
        </div>`).join('');
      $$('#pos-holds-list [data-recall]').forEach(b => b.onclick = () => recallHold(parseInt(b.dataset.recall)));
      $$('#pos-holds-list [data-del-hold]').forEach(b => b.onclick = async () => {
        if (!confirm('Delete this held order?')) return;
        try { await apiDelete(`/api/pos/holds/${b.dataset.delHold}`); toast('Deleted', 'success'); loadHoldsPreview(); }
        catch (e) { toast('Error', 'error'); }
      });
    } catch (e) {}
  }

  async function recallHold(hid) {
    try {
      const h = await api(`/api/pos/holds/${hid}`);
      if (cart.length && !confirm('Current cart will be replaced. Continue?')) return;
      cart = (h.items || []).map(i => ({ catId: i.catId, price: i.price, code: i.code, name: i.name, qty: i.qty }));
      discountVal = h.discount || 0;
      discountType = h.discount_type || 'amount';
      $('#cust-name').value = h.customer_name || '';
      $('#cust-phone').value = h.customer_phone || '';
      $('#sale-notes').value = h.notes || '';
      $('#discount-input').value = discountVal;
      $('#discount-type').value = discountType;
      renderCart();
      // Delete the hold now that it's been recalled
      await apiDelete(`/api/pos/holds/${hid}`);
      loadHoldsPreview();
      toast(`Recalled ${h.reference}`, 'success');
      // Try customer lookup
      lookupCustomer();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  function showHoldsModal() {
    openModal('Held Orders', `<div id="holds-modal-list" class="stat-list">Loading...</div>`,
      `<button class="btn btn-secondary" data-modal-close>Close</button>`);
    api('/api/pos/holds').then(r => {
      const list = r.holds || [];
      $('#holds-modal-list').innerHTML = list.length ? list.map(h => `
        <div class="hold-row">
          <div><b>${esc(h.reference)}</b> · ${fmtDate(h.created_at)} ${fmtTime(h.created_at)}</div>
          <div class="text-sm">${esc(h.customer_name || 'Walk-in')} · ${h.items?.length || 0} items · Rs ${fmt(h.total)}</div>
          ${h.notes ? `<div class="text-xs text-dim">${esc(h.notes)}</div>` : ''}
          <div class="flex gap-1 mt-1">
            <button class="btn btn-sm" data-recall="${h.id}">Recall</button>
            <button class="btn btn-ghost btn-sm" data-del-hold="${h.id}">${icon('trash', 12)} Delete</button>
          </div>
        </div>`).join('') : '<p class="text-dim text-sm">No held orders.</p>';
      $$('#holds-modal-list [data-recall]').forEach(b => b.onclick = () => { closeModal(); recallHold(parseInt(b.dataset.recall)); });
      $$('#holds-modal-list [data-del-hold]').forEach(b => b.onclick = async () => {
        if (!confirm('Delete?')) return;
        try { await apiDelete(`/api/pos/holds/${b.dataset.delHold}`); toast('Deleted', 'success'); showHoldsModal(); loadHoldsPreview(); }
        catch (e) { toast('Error', 'error'); }
      });
    }).catch(() => toast('Error loading holds', 'error'));
  }

  // ---------- quotations ----------
  async function saveQuote() {
    if (!cart.length) { toast('Cart is empty', 'info'); return; }
    openModal('Save as Quotation', `
      <p>This will save the current cart as a quotation. The customer can review it and you can convert it to a sale later.</p>
      <div class="mt-3">
        <label class="text-xs text-dim">Customer</label>
        <input class="input" id="quote-cust" value="${esc($('#cust-name').value)}" placeholder="Customer name">
      </div>
      <div class="mt-2">
        <label class="text-xs text-dim">Phone</label>
        <input class="input" id="quote-phone" value="${esc($('#cust-phone').value)}" placeholder="Phone">
      </div>
      <div class="mt-2">
        <label class="text-xs text-dim">Valid for (days)</label>
        <input class="input" id="quote-days" type="number" value="7" min="1" max="90">
      </div>
      <div class="mt-2">
        <label class="text-xs text-dim">Notes</label>
        <textarea class="input" id="quote-notes" rows="2">${esc($('#sale-notes').value)}</textarea>
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="quote-save-btn">Save Quotation</button>`);
    $('#quote-save-btn').onclick = async () => {
      const total = cart.reduce((s, i) => s + i.price * i.qty, 0) - (discountType === 'percent' ? cart.reduce((s, i) => s + i.price * i.qty, 0) * discountVal/100 : discountVal);
      try {
        const r = await apiPost('/api/quotations', {
          customer_name: $('#quote-cust').value,
          customer_phone: $('#quote-phone').value,
          notes: $('#quote-notes').value,
          items: cart.map(i => ({ catId: i.catId, code: i.code, price: i.price, qty: i.qty, name: i.name })),
          discount: discountVal, discount_type: discountType, total,
          valid_days: parseInt($('#quote-days').value) || 7,
        });
        toast(`Quotation ${r.quote_no} saved`, 'success');
        closeModal();
        clearCart();
        // Offer print
        openModal('Quotation Saved', `
          <p>Quote <b>${r.quote_no}</b> saved. Valid until <b>${r.valid_until}</b>.</p>`,
          `<button class="btn btn-secondary" data-modal-close>Done</button>
           <button class="btn" onclick="window.open('/api/quotations/${r.id}/receipt','_blank')">Print Quote</button>`);
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }
