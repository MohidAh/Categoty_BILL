// CustomerBar — customer search and display (Phase 5 extraction)
// These functions are imported by pos.js and operate on shared DOM state
import { $, $$, esc, fmt, fmtRs, debounce, toast, openModal, closeModal } from '../../../utils.js';
import { api, apiPost } from '../../../api.js';

export function createCustomerBar(context) {
  const { cart, customerId, customerLoyaltyPts, customerCredit, loyaltyRate, updateCustomerInfo } = context;
    async function lookupCustomer() {
      const phone = $('#cust-phone').value.trim();
      const name = $('#cust-name').value.trim();
      if (!phone && !name) {
        customerId = null;
        customerLoyaltyPts = 0;
        customerCredit = 0;
        updateCustomerInfo();
        return;
      }
      try {
        const q = phone || name;
        const r = await api(`/api/customers?q=${encodeURIComponent(q)}`);
        const list = r.customers || [];
        // Try exact phone match first
        let match = phone ? list.find(c => c.phone === phone) : null;
        if (!match) match = list.find(c => c.name.toLowerCase() === name.toLowerCase());
        if (!match && list.length === 1) match = list[0];
        if (match) {
          customerId = match.id;
          customerLoyaltyPts = match.loyalty_points || 0;
          customerCredit = match.total_credit || 0;
          $('#cust-name').value = match.name;
          $('#cust-phone').value = match.phone || '';
          updateCustomerInfo();
        } else {
          // No match — will be auto-created on checkout
          customerId = null;
          customerLoyaltyPts = 0;
          customerCredit = 0;
          updateCustomerInfo();
        }
      } catch (e) {}
    }
  
    function showCustomerSearch() {
      openModal('Find Customer', `
        <input class="input" id="cust-search-input" placeholder="Type name or phone..." autocomplete="off">
        <div id="cust-search-results" class="mt-3" style="max-height:400px;overflow-y:auto"></div>`,
        `<button class="btn btn-secondary" data-modal-close>Close</button>
         <button class="btn" id="cust-new">+ New Customer</button>`);
      const input = $('#cust-search-input');
      const results = $('#cust-search-results');
      input.focus();
      input.oninput = debounce(async () => {
        const q = input.value.trim();
        if (q.length < 1) { results.innerHTML = ''; return; }
        try {
          const r = await api(`/api/customers?q=${encodeURIComponent(q)}`);
          const list = r.customers || [];
          results.innerHTML = list.length ? list.map(c => `
            <div class="cust-search-row" data-cid="${c.id}">
              <div><b>${esc(c.name)}</b> ${c.phone ? '<span class="text-dim text-sm">'+esc(c.phone)+'</span>' : ''}</div>
              <div class="text-xs text-dim">
                ⭐ ${c.loyalty_points||0} pts
                ${c.total_credit > 0 ? ' · <span class="text-danger">Rs ' + fmt(c.total_credit) + ' credit</span>' : ''}
                · spent Rs ${fmt(c.total_spent||0)}
              </div>
            </div>`).join('') : '<p class="text-dim text-sm">No matches.</p>';
          $$('.cust-search-row').forEach(row => row.onclick = () => {
            const cid = parseInt(row.dataset.cid);
            const c = list.find(x => x.id === cid);
            if (c) {
              customerId = c.id;
              customerLoyaltyPts = c.loyalty_points || 0;
              customerCredit = c.total_credit || 0;
              $('#cust-name').value = c.name;
              $('#cust-phone').value = c.phone || '';
              updateCustomerInfo();
              closeModal();
              toast('Customer: ' + c.name, 'success');
            }
          });
        } catch (e) { results.innerHTML = '<p class="text-danger">Error</p>'; }
      }, 250);
      $('#cust-new').onclick = async () => {
        const name = input.value.trim();
        if (!name) { toast('Enter name first', 'error'); return; }
        try {
          const res = await fetch('/api/customers?name=' + encodeURIComponent(name), { method: 'POST' });
          if (!res.ok) throw new Error('Failed');
          const j = await res.json();
          customerId = j.id;
          customerLoyaltyPts = 0;
          customerCredit = 0;
          $('#cust-name').value = name;
          updateCustomerInfo();
          closeModal();
          toast('New customer created', 'success');
        } catch (e) { toast('Error: ' + e.message, 'error'); }
      };
    }
  
    // ---------- holds ----------
  
}
