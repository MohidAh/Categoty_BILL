// CheckoutModal — checkout flow (Phase 5 extraction)
import { $, $$, esc, fmt, fmtRs, toast, openModal, closeModal, showLoading, hideLoading } from '../../../utils.js';
import { apiPost } from '../../../api.js';
import { queueSale, isOnline, generateOfflineInvoiceNo, getQueueCount, triggerFlush } from '../../../core/offline.js';

  async function checkout() {
    if (!cart.length) return;
    const payMethod = $$('input[name="pay-method"]:checked')[0]?.value || 'cash';
    let splitCash = 0, splitCard = 0, splitOnline = 0;
    if (payMethod === 'split') {
      splitCash = parseFloat($('#split-cash').value) || 0;
      splitCard = parseFloat($('#split-card').value) || 0;
      splitOnline = parseFloat($('#split-online').value) || 0;
      const total = Math.max(0, cart.reduce((s, i) => s + i.price * i.qty, 0)
        - (discountType === 'percent' ? cart.reduce((s, i) => s + i.price * i.qty, 0) * discountVal/100 : discountVal)
        - loyaltyDiscountVal);
      if (splitCash + splitCard + splitOnline < total - 0.01) {
        toast('Split amount is less than total', 'error');
        return;
      }
    }
    showLoading('Processing...');
    try {
      const items = cart.map(i => ({
        category_id: i.catId, category_code: i.code,
        sell_price: i.price, qty: i.qty, item_name: `${i.code} (${i.name})`,
      }));
      const payload = {
        customer_name: $('#cust-name').value,
        customer_phone: $('#cust-phone').value,
        customer_id: customerId,
        discount: discountVal, discount_type: discountType,
        payment_method: payMethod,
        split_cash: splitCash, split_card: splitCard, split_online: splitOnline,
        loyalty_points_used: loyaltyPointsToRedeem,
        notes: $('#sale-notes').value,
        quotation_id: pendingQuoteId,
        items,
      };

      // ─── Offline detection: queue sale if no network ───
      if (!isOnline()) {
        hideLoading();
        const offlineInvoiceNo = generateOfflineInvoiceNo();
        await queueSale(payload);
        const queueCount = await getQueueCount();
        const cartCount = cart.length;
        const cartTotal = cart.reduce((s, i) => s + i.price * i.qty, 0)
          - (discountType === 'percent' ? cart.reduce((s, i) => s + i.price * i.qty, 0) * discountVal/100 : discountVal)
          - loyaltyDiscountVal;
        openModal('Sale Queued (Offline)', `
          <div style="text-align:center;padding:16px 0">
            <p class="text-sm text-dim">${esc(offlineInvoiceNo)}</p>
            <p style="font-size:32px;font-weight:800;margin:8px 0;color:var(--warning-text)">${fmtRs(cartTotal)}</p>
            <p class="text-sm">${cartCount} items &middot; ${esc(payMethod)}</p>
            <div class="alert alert-warning mt-3">
              <div><strong>You are offline.</strong> Sale has been saved locally and will sync automatically when you reconnect.</div>
              <div class="text-xs text-dim mt-2">${queueCount} sale(s) in queue</div>
            </div>
          </div>`,
          `<button class="btn btn-secondary" data-modal-close>Done</button>
           <button class="btn" id="offline-retry-btn">Try Sync Now</button>`);
        const retryBtn = $('#offline-retry-btn');
        if (retryBtn) retryBtn.onclick = async () => {
          retryBtn.disabled = true;
          retryBtn.textContent = 'Syncing...';
          await triggerFlush();
          setTimeout(() => {
            closeModal();
            toast('Sync attempted — check queue status', 'info');
            reload();
          }, 1500);
        };
        clearCart();
        $('#cust-name').value = ''; $('#cust-phone').value = '';
        customerId = null;
        customerLoyaltyPts = 0;
        customerCredit = 0;
        pendingQuoteId = null;
        updateCustomerInfo();
        return;
      }

      const r = await apiPost('/api/sales', payload);
      hideLoading();
      // v4.0 Phase 4: if backend flagged discount_pin_required, the api() wrapper
      // would have thrown (status 403). The catch block below handles it.
      // Receipt modal
      openModal('Sale Complete', `
        <div style="text-align:center;padding:16px 0">
          <p class="text-sm text-dim">${esc(r.invoice_no)}</p>
          <p style="font-size:36px;font-weight:800;margin:8px 0;color:var(--success-text)">${fmtRs(r.total)}</p>
          <p class="text-sm">${cart.length} items · ${payMethod}${payMethod === 'split' ? ` (C${fmt(splitCash)}/C${fmt(splitCard)}/O${fmt(splitOnline)})` : ''}</p>
          ${r.payment_status === 'credit' ? '<p class="badge badge-danger mt-2">CREDIT</p>' : ''}
          ${r.payment_status === 'partial' ? '<p class="badge badge-warning mt-2">PARTIAL</p>' : ''}
          ${r.loyalty_points_used > 0 ? `<p class="text-xs text-success mt-2">${r.loyalty_points_used} loyalty points used (−Rs ${fmt(r.loyalty_discount)})</p>` : ''}
        </div>`,
        `<button class="btn btn-secondary" data-modal-close>Done</button>
         <button class="btn" onclick="window.open('/api/sales/${r.id}/receipt','_blank')">Print</button>
         <button class="btn btn-secondary" onclick="sendReceipt(${r.id})">WhatsApp</button>`);
      clearCart();
      $('#cust-name').value = ''; $('#cust-phone').value = '';
      customerId = null;
      customerLoyaltyPts = 0;
      customerCredit = 0;
      pendingQuoteId = null;
      updateCustomerInfo();
    } catch (e) {
      hideLoading();
      // v4.0 Phase 4: handle discount_pin_required (403) by asking for manager PIN and retrying
      if (e.status === 403 && e.detail && e.detail.code === 'discount_pin_required' && !payload.manager_pin) {
        const pin = await window.__askManagerPin({
          title: 'Discount Authorization Required',
          reason: `Discount ${e.detail.discount_pct}% exceeds max ${e.detail.max_allowed}% allowed without manager approval.`,
          detail: 'Enter a manager PIN to authorize this discount and complete the sale.',
          confirmLabel: 'Authorize & Complete Sale',
        });
        if (pin) {
          // Re-open the checkout flow with manager_pin set
          payload.manager_pin = pin;
          // Re-run checkout() — but we need to preserve cart state, which we have.
          // Just call the same flow again with the updated payload.
          showLoading('Authorizing...');
          try {
            const r2 = await apiPost('/api/sales', payload);
            hideLoading();
            // Render the same receipt modal as the success path
            openModal('Sale Complete', `
              <div style="text-align:center;padding:16px 0">
                <p class="text-sm text-dim">${esc(r2.invoice_no)}</p>
                <p style="font-size:36px;font-weight:800;margin:8px 0;color:var(--success-text)">${fmtRs(r2.total)}</p>
                <p class="text-sm">${cart.length} items · ${payMethod}</p>
              </div>
              <div class="flex gap-2 mt-3">
                <button class="btn btn-secondary" data-modal-close>Close</button>
                <button class="btn" onclick="window.printReceipt(${r2.id})">Print Receipt</button>
              </div>`);
            clearCart();
            $('#cust-name').value = ''; $('#cust-phone').value = '';
            customerId = null;
            customerLoyaltyPts = 0;
            customerCredit = 0;
            pendingQuoteId = null;
            updateCustomerInfo();
          } catch (e2) {
            hideLoading();
            toast('Sale failed after PIN: ' + e2.message, 'error');
          }
        }
        return;
      }
      toast('Sale failed: ' + e.message, 'error');
    }
  }


  window.sendReceipt = async (id) => {
    try {
      const r = await api(`/api/sales/${id}/whatsapp`);
      if (r.url) window.open(r.url, '_blank');
      else toast('No customer phone', 'info');
    } catch (e) { toast('Error', 'error'); }
  };

  // ---------- keyboard shortcuts (POS-only) ----------
  const posKeyHandler = (e) => {
    // Ignore when typing
    const tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
      // F-keys still allowed
      if (e.key.startsWith('F') && e.key.length > 1) {} else return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    // F1-F7 → add first 7 categories (dynamic, supports up to 7)
    // F8 = scan, F9 = checkout, F10 = hold, F11 = clear, F12 = quote
    const fMatch = /^F([1-7])$/.exec(e.key);
    if (fMatch) {
      e.preventDefault();
      const idx = parseInt(fMatch[1]) - 1;
      const c = posCats[idx];
      if (c) addToCart(c.id, c.sell_price, c.code, c.name, c.color);
      return;
    }
    if (e.key === 'F8') { e.preventDefault(); showScanModal(); return; }
    if (e.key === 'F9') { e.preventDefault(); checkout(); return; }
    if (e.key === 'F10') { e.preventDefault(); holdCart(); return; }
    if (e.key === 'F11') { e.preventDefault(); if (cart.length && confirm('Clear cart?')) clearCart(); return; }
    if (e.key === 'F12') { e.preventDefault(); saveQuote(); return; }
  };
  document.addEventListener('keydown', posKeyHandler);

  // ---------- initial render ----------
  renderCart();
  updateCustomerInfo();
  loadHoldsPreview();

  // Listen for quote-conversion events (from /pos/quotes)
  const quoteHandler = (e) => {
    const q = e.detail;
    if (!q) return;
    cart = (q.items || []).map(i => ({ catId: i.catId, price: i.price, code: i.code, name: i.name, qty: i.qty }));
    discountVal = q.discount || 0;
    discountType = q.discount_type || 'amount';
    $('#cust-name').value = q.customer_name || '';
    $('#cust-phone').value = q.customer_phone || '';
    $('#sale-notes').value = q.notes || '';
    $('#discount-input').value = discountVal;
    $('#discount-type').value = discountType;
    renderCart();
    lookupCustomer();
    pendingQuoteId = q.id;
    toast(`Loaded quote ${q.quote_no} — checkout to convert`, 'info');
  };
  window.addEventListener('pos:load-quote', quoteHandler);
  // Cleanup when navigating away
  el._cleanup = () => {
    document.removeEventListener('keydown', posKeyHandler);
    window.removeEventListener('pos:load-quote', quoteHandler);
  };
