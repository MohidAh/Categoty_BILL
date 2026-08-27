// KioskLogic extras — scan, cash actions, z-report, customer display (Phase 5)
// v8.4: Functions are exported both as ES module exports AND window.* globals
// so they can be called from pos.js (via import) and from inline onclick handlers.
import { $, $$, esc, fmt, fmtRs, fmtDate, toast, openModal, closeModal, icon } from '../../../utils.js';
import { api, apiPost } from '../../../api.js';

export function showScanModal() {
    openModal('Scan Barcode', `
      <p class="text-dim text-sm">Use a USB barcode scanner or paste the barcode payload below.</p>
      <input class="input" id="scan-input" placeholder="Scan or paste barcode (BBCAT:1:250)" autofocus>
      <p class="text-xs text-dim mt-2">Pattern: <code>BBCAT:&lt;id&gt;:&lt;price&gt;</code></p>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="scan-go">Add to Cart</button>`);
    const input = $('#scan-input');
    input.focus();
    input.onkeydown = (e) => { if (e.key === 'Enter') $('#scan-go').click(); };
    // Auto-fire on barcode-scanner input (USB scanners send Enter automatically)
    let lastInput = '';
    input.oninput = () => {
      const v = input.value;
      // If looks like a complete BBCAT payload, auto-fire after a tiny delay
      if (v.startsWith('BBCAT:') && v.split(':').length === 3 && v !== lastInput) {
        lastInput = v;
        setTimeout(() => $('#scan-go').click(), 50);
      }
    };
    $('#scan-go').onclick = async () => {
      const payload = input.value.trim();
      if (!payload) { toast('Scan a barcode first', 'error'); return; }
      try {
        const r = await apiPost('/api/barcodes/scan', { payload });
        // C9 fix (v8.13.4): addToCart is a window global exposed by pos.js.
        // Previously called as a bare identifier, which threw ReferenceError
        // because addToCart is a closure-internal function in pos.js's
        // route handler.
        if (typeof window.addToCart !== 'function') {
          toast('Cart is not ready — open POS first', 'error');
          return;
        }
        window.addToCart(r.id, r.sell_price, r.code, r.name, r.color);
        toast(`Added ${r.code} — ${r.name}`, 'success');
        closeModal();
      } catch (e) { toast('Invalid barcode: ' + e.message, 'error'); }
    };
  }

  // ---------- cash drawer ----------
export function showCashActions() {
    api('/api/cash-drawer').then(d => {
      openModal('Cash Drawer', `
        <div class="stat-list">
          <div class="stat-row"><span>Status</span><span class="stat-value"><span class="badge ${d.status === 'open' ? 'badge-success' : 'badge-warning'}">${d.status}</span></span></div>
          <div class="stat-row"><span>Opening</span><span class="stat-value">${fmtRs(d.opening_cash)}</span></div>
          <div class="stat-row"><span>Current</span><span class="stat-value font-bold">${fmtRs(d.current_cash)}</span></div>
          <div class="stat-row"><span>Entries today</span><span class="stat-value">${d.entries}</span></div>
        </div>
        <div class="grid grid-2 mt-3">
          <button class="btn" id="btn-cash-in">Cash In</button>
          <button class="btn btn-secondary" id="btn-cash-out">Cash Out</button>
        </div>`,
        `<button class="btn btn-secondary" data-modal-close>Close</button>`);
      $('#btn-cash-in').onclick = () => cashActionModal('in');
      $('#btn-cash-out').onclick = () => cashActionModal('out');
    });
  }
  function cashActionModal(type) {
    const isOut = type === 'out';
    openModal(`${isOut ? 'Cash Out' : 'Cash In'}`, `
      <div class="mt-2">
        <label class="text-xs text-dim">Amount</label>
        <input class="input" id="cash-amt" type="number" value="0" min="0" autofocus>
      </div>
      <div class="mt-2">
        <label class="text-xs text-dim">Description</label>
        <input class="input" id="cash-desc" placeholder="${isOut ? 'e.g., Petty expense' : 'e.g., Float top-up'}">
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn ${isOut ? 'btn-danger' : ''}" id="cash-confirm">${isOut ? 'Remove Cash' : 'Add Cash'}</button>`);
    $('#cash-confirm').onclick = async () => {
      const amount = parseFloat($('#cash-amt').value) || 0;
      const desc = $('#cash-desc').value;
      if (amount <= 0) { toast('Enter amount', 'error'); return; }
      try {
        await apiPost(`/api/cash-drawer/${type}`, { amount, description: desc });
        toast(`${isOut ? 'Cash out' : 'Cash in'} recorded`, 'success');
        closeModal();
        showCashActions();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }

  // ---------- Z-Report ----------
export async function showZReport() {
    const today = new Date().toISOString().slice(0, 10);
    try {
      const r = await api(`/api/sales/z-report?date=${today}`);
      openModal(`Z-Report — ${today}`, `
        <div class="stat-list">
          <div class="stat-row"><span>Total Sales (incl. refunds)</span><span class="stat-value">${r.sale_count}</span></div>
          ${r.refunded_count > 0 ? `<div class="stat-row"><span>Refunded</span><span class="stat-value text-warning">${r.refunded_count}</span></div>` : ''}
          <div class="stat-row"><span>Paid</span><span class="stat-value text-success">${r.paid_count}</span></div>
          ${r.partial_count > 0 ? `<div class="stat-row"><span>Partial</span><span class="stat-value text-warning">${r.partial_count}</span></div>` : ''}
          <div class="stat-row"><span>Credit</span><span class="stat-value text-danger">${r.credit_count}</span></div>
          <div class="stat-row" style="border-top:2px solid var(--border)"><span class="font-bold">Cash Expected</span><span class="stat-value font-bold">${fmtRs(r.cash_expected)}</span></div>
          <div class="stat-row"><span>Card</span><span class="stat-value">${fmtRs(r.card_total)}</span></div>
          <div class="stat-row"><span>Online</span><span class="stat-value">${fmtRs(r.total_online || 0)}</span></div>
          <div class="stat-row"><span>Credit Outstanding</span><span class="stat-value text-danger">${fmtRs(r.credit_total)}</span></div>
          ${r.partial_count > 0 ? `<div class="stat-row"><span>Partial Unpaid</span><span class="stat-value text-warning">${fmtRs(r.total_partial || 0)}</span></div>` : ''}
          <div class="stat-row" style="border-top:2px solid var(--border)"><span class="font-bold">Net Revenue</span><span class="stat-value font-bold text-success">${fmtRs(r.total_revenue || r.total_sales)}</span></div>
          <div class="stat-row"><span>Cost of Goods</span><span class="stat-value">${fmtRs(r.total_cost)}</span></div>
          <div class="stat-row"><span>Gross Profit</span><span class="stat-value font-bold text-accent">${fmtRs(r.total_profit)}</span></div>
          <div class="stat-row"><span>Margin</span><span class="stat-value">${(r.margin*100).toFixed(1)}%</span></div>
          ${r.first_invoice ? `<div class="stat-row"><span>First Invoice</span><span class="font-mono text-sm">${esc(r.first_invoice)}</span></div>` : ''}
          ${r.last_invoice ? `<div class="stat-row"><span>Last Invoice</span><span class="font-mono text-sm">${esc(r.last_invoice)}</span></div>` : ''}
        </div>
        ${r.by_category.length ? `<div class="table-wrap mt-3"><table><thead><tr><th>Cat</th><th class="table-num">Qty</th><th class="table-num">Revenue</th><th class="table-num">Profit</th></tr></thead><tbody>${r.by_category.map(c=>`<tr><td><span class="badge badge-accent">${c.code}</span></td><td class="table-num">${c.qty}</td><td class="table-num">${fmtRs(c.revenue)}</td><td class="table-num text-success">${fmtRs(c.profit)}</td></tr>`).join('')}</tbody></table></div>` : ''}
      `, `<button class="btn btn-secondary" data-modal-close>Close</button>`);
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  // ---------- customer-facing display ----------
export function toggleCustomerDisplay() {
    // C9 fix (v8.13.4): delegate to pos.js's closure-internal function via
    // the window global it exposes. Previously this flipped a local
    // isCustomerDisplay that was never declared in this module — so the
    // toggle silently broke on first use (ReferenceError on the assignment
    // inside the if-block, or silently mutated an outer global).
    if (typeof window._posToggleCustomerDisplay === 'function') {
      window._posToggleCustomerDisplay();
    } else {
      toast('POS not initialized — customer display toggle ignored', 'info');
    }
  }

  function updateCustomerDisplay() {
    // The cart+total already reflects what customer should see.
    // In customer mode, we hide cost info — which is already the default.
  }

// v8.4: Export all functions to window so inline onclick handlers can call them.
// These are referenced by pos.js secondary action buttons:
//   onclick="showCashActions()", onclick="showZReport()", onclick="toggleCustomerDisplay()"
window.showScanModal = showScanModal;
window.showCashActions = showCashActions;
window.showZReport = showZReport;
window.toggleCustomerDisplay = toggleCustomerDisplay;

