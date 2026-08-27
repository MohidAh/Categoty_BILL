// Customers app pages — All Customers, Credit Outstanding, Loyalty Tiers, Import, Detail
// All render inside the Customers app SnowUI shell (chip-info color theme).
import { route, navigate, reload } from '../router.js';
import { errorState } from '../core/states.js';
import { api, apiPost, apiPut, apiDelete } from '../api.js';
import { $, $$, esc, fmt, fmtRs, fmtDate, icon, toast, showLoading, hideLoading,
         openModal, closeModal, skeletonCards, errorBox, emptyState } from '../utils.js';

// Shared SVG icon set for customers pages
const SVG = {
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
  star: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  phone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>',
  map: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  arrowLeft: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>',
  gift: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 12 20 22 4 22 4 12"/><rect x="2" y="7" width="20" height="5"/><line x1="12" y1="22" x2="12" y2="7"/><path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"/><path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"/></svg>',
};

// Helper: stat card with SVG icon chip
function statCard(label, value, chipClass, svgIcon, sub = '') {
  return `<div class="stat-card">
    <div class="stat-card-icon ${chipClass}">${svgIcon}</div>
    <div class="stat-card-label">${esc(label)}</div>
    <div class="stat-card-value">${value}</div>
    ${sub ? `<div class="stat-card-delta">${sub}</div>` : ''}
  </div>`;
}

// Loyalty tier thresholds (points-based)
const TIERS = [
  { name: 'Bronze',  min: 0,    max: 50,   chip: 'chip-secondary', color: '#a78bfa' },
  { name: 'Silver',  min: 50,   max: 200,  chip: 'chip-info',      color: '#60a5fa' },
  { name: 'Gold',    min: 200,  max: 500,  chip: 'chip-warning',   color: '#fbbf24' },
  { name: 'Platinum', min: 500, max: Infinity, chip: 'chip-pink',  color: '#ec4899' },
];

function getTier(points) {
  return TIERS.find(t => points >= t.min && points < t.max) || TIERS[0];
}

// ═══════════════════════════════════════════════════
// ALL CUSTOMERS — searchable list with stat cards
// ═══════════════════════════════════════════════════
route('/customers', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-info">${SVG.users}</div>
      <div>
        <h2 class="pos-page-header-title">All Customers</h2>
        <p class="pos-page-header-sub">Browse, search, and manage your customer base.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn" id="c-add-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          Add Customer
        </button>
      </div>
    </div>

    <div id="c-stats" class="mb-4"></div>

    <div class="card mb-4">
      <div class="filter-bar">
        <div class="search-input filter-search">
          ${SVG.search}
          <input class="input" id="c-search" placeholder="Search by name or phone number">
        </div>
      </div>
    </div>

    <div class="card">
      <div id="c-list">${skeletonCards(3)}</div>
    </div>`;

  $('#c-add-btn').onclick = () => openAddModal();

  let allCustomers = [];
  try {
    const r = await api('/api/customers');
    allCustomers = r.customers || [];
  } catch (e) {
    $('#c-list').innerHTML = errorBox(e.message);
    return;
  }

  renderStats();
  renderList();

  $('#c-search').oninput = renderList;

  function renderStats() {
    const total = allCustomers.length;
    const withCredit = allCustomers.filter(c => (c.total_credit || 0) > 0).length;
    const totalCredit = allCustomers.reduce((s, c) => s + (c.total_credit || 0), 0);
    const totalLoyalty = allCustomers.reduce((s, c) => s + (c.loyalty_points || 0), 0);
    $('#c-stats').innerHTML = `
      <div class="grid grid-4">
        ${statCard('Total Customers', total, 'chip-primary', SVG.users)}
        ${statCard('With Credit', withCredit, 'chip-danger', SVG.wallet)}
        ${statCard('Outstanding', fmtRs(totalCredit), 'chip-danger', SVG.alert)}
        ${statCard('Loyalty Points', fmt(totalLoyalty), 'chip-warning', SVG.star)}
      </div>`;
  }

  function renderList() {
    const q = $('#c-search').value.toLowerCase().trim();
    const filtered = q
      ? allCustomers.filter(c => (c.name || '').toLowerCase().includes(q) || (c.phone || '').toLowerCase().includes(q))
      : allCustomers;

    if (!filtered.length) {
      $('#c-list').innerHTML = emptyState(
        'No customers found',
        q ? 'Try a different search.' : 'Add your first customer to start tracking sales and loyalty.',
        q ? '' : 'Add Customer', ''
      );
      const eb = document.querySelector('.empty-state button');
      if (eb) eb.onclick = () => openAddModal();
      return;
    }

    $('#c-list').innerHTML = `
      <div class="table-wrap">
        <table class="table-clickable">
          <thead><tr>
            <th>Name</th><th>Phone</th>
            <th class="table-num">Total Spent</th>
            <th class="table-num">Credit</th>
            <th class="table-num">Loyalty Pts</th>
            <th>Tier</th><th></th>
          </tr></thead>
          <tbody>${filtered.map(c => {
            const tier = getTier(c.loyalty_points || 0);
            return `<tr class="cust-row" data-id="${c.id}">
              <td class="font-semibold">${esc(c.name)}</td>
              <td class="text-sm">${esc(c.phone || '—')}</td>
              <td class="table-num">${fmtRs(c.total_spent)}</td>
              <td class="table-num ${c.total_credit > 0 ? 'text-danger font-semibold' : 'text-dim'}">${fmtRs(c.total_credit)}</td>
              <td class="table-num">${fmt(c.loyalty_points || 0)}</td>
              <td><span class="badge ${tier.chip}">${tier.name}</span></td>
              <td><button class="btn btn-ghost btn-sm btn-icon" data-cust-delete="${c.id}" data-cust-name="${esc(c.name).replace(/"/g, '&quot;')}" title="Delete">${SVG.trash}</button></td>
            </tr>`;
          }).join('')}</tbody>
        </table>
      </div>`;

    $$('.cust-row').forEach(row => {
      row.onclick = (e) => {
        if (e.target.closest('[data-cust-delete]')) return;
        navigate('/customers/' + row.dataset.id);
      };
    });
    $$('[data-cust-delete]').forEach(btn => {
      btn.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete customer "${btn.dataset.custName}"? This cannot be undone.`)) return;
        try {
          await apiDelete(`/api/customers/${btn.dataset.custDelete}`);
          toast('Customer deleted', 'success');
          // Update local list
          allCustomers = allCustomers.filter(c => c.id !== parseInt(btn.dataset.custDelete));
          renderStats();
          renderList();
        } catch (err) { toast('Error: ' + err.message, 'error'); }
      };
    });
  }

  function openAddModal() {
    openModal(
      'Add Customer',
      `
      <div><label>Name</label><input class="input" id="ac-name" placeholder="Customer name" autofocus></div>
      <div class="mt-3"><label>Phone</label><input class="input" id="ac-phone" placeholder="03001234567"></div>
      <div class="mt-3"><label>Address (optional)</label><input class="input" id="ac-address" placeholder="Street, City"></div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="ac-save-btn">
         <span style="display:inline-flex;width:14px;height:14px">${SVG.save}</span>
         Save
       </button>`
    );
    $('#ac-save-btn').onclick = async () => {
      const name = $('#ac-name').value.trim();
      const phone = $('#ac-phone').value.trim();
      const address = $('#ac-address').value.trim();
      if (!name) { toast('Name is required', 'error'); return; }
      try {
        // Use POST /api/customers to create
        const r = await apiPost(`/api/customers?name=${encodeURIComponent(name)}&phone=${encodeURIComponent(phone)}`, {});
        // Update address if provided via PUT
        if (address && r.id) {
          await apiPut(`/api/customers/${r.id}`, { address });
        }
        toast('Customer added', 'success');
        closeModal();
        reload();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }
});

// ═══════════════════════════════════════════════════
// CREDIT OUTSTANDING — customers with total_credit > 0
// ═══════════════════════════════════════════════════
route('/customers/credit', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-info">${SVG.wallet}</div>
      <div>
        <h2 class="pos-page-header-title">Credit Outstanding</h2>
        <p class="pos-page-header-sub">Customers with unpaid credit. Record payments to clear balances.</p>
      </div>
    </div>
    <div id="cr-stats" class="mb-4"></div>
    <div class="card">
      <div id="cr-list">${skeletonCards(3)}</div>
    </div>`;

  try {
    const r = await api('/api/customers');
    const all = r.customers || [];
    const creditList = all.filter(c => (c.total_credit || 0) > 0)
                          .sort((a, b) => b.total_credit - a.total_credit);

    const totalOutstanding = creditList.reduce((s, c) => s + c.total_credit, 0);
    const maxCredit = creditList.length ? Math.max(...creditList.map(c => c.total_credit)) : 0;
    $('#cr-stats').innerHTML = `
      <div class="grid grid-3">
        ${statCard('Customers with Credit', creditList.length, 'chip-danger', SVG.users)}
        ${statCard('Total Outstanding', fmtRs(totalOutstanding), 'chip-danger', SVG.wallet)}
        ${statCard('Largest Balance', fmtRs(maxCredit), 'chip-warning', SVG.alert)}
      </div>`;

    if (!creditList.length) {
      $('#cr-list').innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon" style="background:var(--success-soft);color:var(--success-text)">${SVG.check}</div>
          <h3>No outstanding credit</h3>
          <p>All customers have settled their balances.</p>
        </div>`;
      return;
    }

    $('#cr-list').innerHTML = `
      <div class="table-wrap">
        <table class="table-clickable">
          <thead><tr>
            <th>Name</th><th>Phone</th>
            <th class="table-num">Total Spent</th>
            <th class="table-num">Outstanding</th>
            <th>Last Activity</th><th></th>
          </tr></thead>
          <tbody>${creditList.map(c => `<tr class="cr-row" data-id="${c.id}">
            <td class="font-semibold">${esc(c.name)}</td>
            <td class="text-sm">${esc(c.phone || '—')}</td>
            <td class="table-num">${fmtRs(c.total_spent)}</td>
            <td class="table-num text-danger font-bold">${fmtRs(c.total_credit)}</td>
            <td class="text-sm text-dim">${fmtDate(c.created_at)}</td>
            <td><button class="btn btn-sm" data-pay="${c.id}" data-name="${esc(c.name).replace(/"/g, '&quot;')}" data-amount="${c.total_credit}">
              Record Payment
            </button></td>
          </tr>`).join('')}</tbody>
        </table>
      </div>`;

    $$('.cr-row').forEach(row => {
      row.onclick = (e) => {
        if (e.target.closest('[data-pay]')) return;
        navigate('/customers/' + row.dataset.id);
      };
    });
    $$('[data-pay]').forEach(btn => {
      btn.onclick = (e) => {
        e.stopPropagation();
        openPaymentModal(
          parseInt(btn.dataset.pay),
          btn.dataset.name,
          parseFloat(btn.dataset.amount)
        );
      };
    });
  } catch (e) {
    $('#cr-list').innerHTML = errorBox(e.message);
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
        <input class="input" id="cp-amount" type="number" value="${amount}" min="0" max="${amount}" step="0.01" autofocus>
      </div>
      <div class="mt-2">
        <label>Payment Method</label>
        <select class="select" id="cp-method">
          <option value="cash">Cash</option>
          <option value="card">Card</option>
          <option value="online">Online Transfer</option>
        </select>
      </div>
      <div class="mt-2">
        <label>Notes</label>
        <input class="input" id="cp-notes" placeholder="Optional notes">
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="cp-save-btn">
         <span style="display:inline-flex;width:14px;height:14px">${SVG.save}</span>
         Record
       </button>`
    );
    $('#cp-save-btn').onclick = async () => {
      const amt = parseFloat($('#cp-amount').value) || 0;
      const method = $('#cp-method').value;
      const notes = $('#cp-notes').value;
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

// ═══════════════════════════════════════════════════
// LOYALTY TIERS — list grouped by tier + redemption history
// ═══════════════════════════════════════════════════
route('/customers/loyalty', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-info">${SVG.star}</div>
      <div>
        <h2 class="pos-page-header-title">Loyalty Tiers</h2>
        <p class="pos-page-header-sub">Reward frequent shoppers. Points earned per Rs 100 spent; redeem for discounts at checkout.</p>
      </div>
    </div>
    <div id="ly-stats" class="mb-4"></div>

    <div class="grid grid-2 mb-4">
      <div class="card">
        <div class="card-title"><h3>Tier Breakdown</h3></div>
        <div id="ly-tiers"></div>
      </div>
      <div class="card">
        <div class="card-title"><h3>Top Customers</h3></div>
        <div id="ly-top"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><h3>Recent Redemptions</h3></div>
      <div id="ly-history">${skeletonCards(2)}</div>
    </div>`;

  try {
    const [custRes, rateRes, redRes] = await Promise.all([
      api('/api/customers'),
      api('/api/loyalty/rate'),
      api('/api/loyalty/redemptions?limit=20'),
    ]);
    const all = custRes.customers || [];
    const rate = rateRes.rate || 1;
    const pointsPerRs = rateRes.points_per_rs || 100;
    const redemptions = redRes.redemptions || [];

    // Stats
    const totalCustomers = all.length;
    const totalPoints = all.reduce((s, c) => s + (c.loyalty_points || 0), 0);
    const totalRedeemed = all.reduce((s, c) => s + (c.loyalty_redeemed || 0), 0);
    const rupeeValue = totalPoints * rate;

    $('#ly-stats').innerHTML = `
      <div class="grid grid-4">
        ${statCard('Total Customers', totalCustomers, 'chip-primary', SVG.users)}
        ${statCard('Active Points', fmt(totalPoints), 'chip-warning', SVG.star, `Worth ${fmtRs(rupeeValue)}`)}
        ${statCard('Points Redeemed', fmt(totalRedeemed), 'chip-success', SVG.gift, `Worth ${fmtRs(totalRedeemed * rate)}`)}
        ${statCard('Earn Rate', `${pointsPerRs} Rs`, 'chip-info', SVG.wallet, `= 1 point`)
        }
      </div>`;

    // Tier breakdown
    const tiersHtml = TIERS.map(t => {
      const members = all.filter(c => {
        const pts = c.loyalty_points || 0;
        return pts >= t.min && pts < t.max;
      });
      return `<div class="loyalty-tier-row">
        <div class="loyalty-tier-chip ${t.chip}">
          <span class="loyalty-tier-name">${t.name}</span>
          <span class="loyalty-tier-range">${t.min}${t.max === Infinity ? '+' : '-' + t.max} pts</span>
        </div>
        <div class="loyalty-tier-count">${members.length} <span class="text-dim text-xs">customers</span></div>
      </div>`;
    }).join('');
    $('#ly-tiers').innerHTML = tiersHtml || '<p class="text-dim text-sm">No customers yet.</p>';

    // Top customers (by loyalty points)
    const top = [...all].sort((a, b) => (b.loyalty_points || 0) - (a.loyalty_points || 0)).slice(0, 5);
    if (!top.length) {
      $('#ly-top').innerHTML = '<p class="text-dim text-sm">No customers yet.</p>';
    } else {
      $('#ly-top').innerHTML = `
        <div class="table-wrap">
          <table>
            <thead><tr><th>Customer</th><th class="table-num">Points</th><th class="table-num">Value</th><th>Tier</th></tr></thead>
            <tbody>${top.map(c => {
              const tier = getTier(c.loyalty_points || 0);
              return `<tr class="ly-top-row" data-id="${c.id}" style="cursor:pointer">
                <td class="font-semibold">${esc(c.name)}</td>
                <td class="table-num">${fmt(c.loyalty_points || 0)}</td>
                <td class="table-num">${fmtRs((c.loyalty_points || 0) * rate)}</td>
                <td><span class="badge ${tier.chip}">${tier.name}</span></td>
              </tr>`;
            }).join('')}</tbody>
          </table>
        </div>`;
      $$('.ly-top-row').forEach(row => {
        row.onclick = () => navigate('/customers/' + row.dataset.id);
      });
    }

    // Redemption history
    if (!redemptions.length) {
      $('#ly-history').innerHTML = `
        <div class="empty-state" style="padding:24px">
          <div class="empty-state-icon" style="background:var(--info-soft);color:var(--info-text)">${SVG.gift}</div>
          <h3>No redemptions yet</h3>
          <p>Customers can redeem loyalty points at checkout for a discount.</p>
        </div>`;
    } else {
      $('#ly-history').innerHTML = `
        <div class="table-wrap">
          <table>
            <thead><tr><th>Date</th><th>Customer</th><th class="table-num">Points Used</th><th class="table-num">Rupee Value</th><th>Sale</th></tr></thead>
            <tbody>${redemptions.map(r => `<tr>
              <td class="text-sm">${fmtDate(r.created_at)}</td>
              <td>${esc(r.customer_name || '—')}</td>
              <td class="table-num font-semibold">${fmt(r.points_used)}</td>
              <td class="table-num text-success font-semibold">${fmtRs(r.rupee_value)}</td>
              <td>${r.sale_id ? `<a href="#/pos/sale/${r.sale_id}" class="text-dim text-sm">Sale #${r.sale_id}</a>` : '<span class="text-dim text-xs">—</span>'}</td>
            </tr>`).join('')}</tbody>
          </table>
        </div>`;
    }
  } catch (e) {
    $('#ly-history').innerHTML = errorBox(e.message);
  }
});

// ═══════════════════════════════════════════════════
// IMPORT — paste CSV or upload file
// ═══════════════════════════════════════════════════
route('/customers/import', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-info">${SVG.upload}</div>
      <div>
        <h2 class="pos-page-header-title">Import Customers</h2>
        <p class="pos-page-header-sub">Bulk-import customers via CSV paste or file upload. Duplicate phones are skipped.</p>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <h3>Paste CSV</h3>
        <p class="text-dim text-sm mb-3">Format: <code>name, phone, address</code> (header row optional)</p>
        <textarea class="textarea" id="im-csv" rows="10" placeholder="name,phone,address
Ali Khan,03001234567,Gulberg Lahore
Fatima Bibi,03007654321,Clifton Karachi
Ahmed Raza,03211234567,F-8 Islamabad"></textarea>
        <button class="btn mt-3" id="im-import-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.upload}</span>
          Import
        </button>
        <div id="im-result" class="mt-3"></div>
      </div>

      <div class="card">
        <h3>Upload File</h3>
        <p class="text-dim text-sm mb-3">Upload a <code>.csv</code> file with the same format.</p>
        <input class="input" type="file" id="im-file" accept=".csv,text/csv">
        <div id="im-file-preview" class="mt-3"></div>
        <button class="btn mt-3" id="im-file-btn" disabled>
          <span style="display:inline-flex;width:14px;height:14px">${SVG.upload}</span>
          Import File
        </button>
        <div id="im-file-result" class="mt-3"></div>
      </div>
    </div>

    <div class="card mt-4">
      <h3>How it works</h3>
      <div class="grid grid-3 mt-3">
        <div>
          <div class="kpi-label">
            <span style="display:inline-flex;width:12px;height:12px;vertical-align:-2px;margin-right:6px">${SVG.file}</span>
            Format
          </div>
          <p class="mt-2 text-sm">CSV with columns: <code>name</code> (required), <code>phone</code>, <code>address</code>. Header row is auto-detected and skipped.</p>
        </div>
        <div>
          <div class="kpi-label">
            <span style="display:inline-flex;width:12px;height:12px;vertical-align:-2px;margin-right:6px">${SVG.check}</span>
            Deduplication
          </div>
          <p class="mt-2 text-sm">Customers with an existing phone number are skipped automatically. Rows with empty names are also skipped.</p>
        </div>
        <div>
          <div class="kpi-label">
            <span style="display:inline-flex;width:12px;height:12px;vertical-align:-2px;margin-right:6px">${SVG.alert}</span>
            Errors
          </div>
          <p class="mt-2 text-sm">Per-row errors are reported individually. Successful rows are committed even if some rows fail.</p>
        </div>
      </div>
    </div>`;

  let fileRows = [];

  function parseCsv(text) {
    const lines = text.trim().split(/\r?\n/).filter(l => l.trim());
    if (!lines.length) return [];
    // Detect header
    const firstCols = lines[0].toLowerCase().split(',').map(c => c.trim());
    const hasHeader = firstCols.includes('name') || firstCols.includes('phone') || firstCols.includes('address');
    const dataLines = hasHeader ? lines.slice(1) : lines;
    return dataLines.map(line => {
      const cols = line.split(',').map(c => c.trim());
      return {
        name: cols[0] || '',
        phone: cols[1] || '',
        address: cols[2] || '',
      };
    }).filter(r => r.name);
  }

  function renderResult(elId, res) {
    if (res.errors && res.errors.length) {
      $(`#${elId}`).innerHTML = `
        <div class="alert alert-warning">
          <span style="display:inline-flex;width:16px;height:16px">${SVG.alert}</span>
          <div>
            <strong>Partial import</strong>
            <div class="text-sm mt-1">Added: ${res.added} &middot; Skipped: ${res.skipped} &middot; Errors: ${res.errors.length}</div>
            <ul class="text-xs mt-2" style="margin:0;padding-left:16px">${res.errors.slice(0, 5).map(e => `<li>${esc(e)}</li>`).join('')}</ul>
          </div>
        </div>`;
    } else {
      $(`#${elId}`).innerHTML = `
        <div class="alert alert-success">
          <span style="display:inline-flex;width:16px;height:16px">${SVG.check}</span>
          <div>
            <strong>Import complete</strong>
            <div class="text-sm mt-1">Added: ${res.added} &middot; Skipped (duplicates/empty): ${res.skipped}</div>
          </div>
        </div>`;
    }
  }

  $('#im-import-btn').onclick = async () => {
    const text = $('#im-csv').value;
    if (!text.trim()) { toast('Paste CSV data first', 'error'); return; }
    const rows = parseCsv(text);
    if (!rows.length) { toast('No valid rows found', 'error'); return; }
    try {
      const res = await apiPost('/api/customers/import', { rows });
      renderResult('im-result', res);
      toast(`Imported ${res.added} customer(s)`, 'success');
    } catch (e) {
      $('#im-result').innerHTML = `<div class="alert alert-danger"><strong>Error:</strong> ${esc(e.message)}</div>`;
    }
  };

  $('#im-file').onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const text = await file.text();
    fileRows = parseCsv(text);
    const fileBtn = $('#im-file-btn');
    if (fileRows.length) {
      fileBtn.disabled = false;
      $('#im-file-preview').innerHTML = `<p class="text-sm">Found <b>${fileRows.length}</b> valid rows in file. Click Import to continue.</p>`;
    } else {
      fileBtn.disabled = true;
      $('#im-file-preview').innerHTML = `<p class="text-sm text-danger">No valid rows found in file.</p>`;
    }
  };

  $('#im-file-btn').onclick = async () => {
    if (!fileRows.length) return;
    try {
      const res = await apiPost('/api/customers/import', { rows: fileRows });
      renderResult('im-file-result', res);
      toast(`Imported ${res.added} customer(s)`, 'success');
    } catch (e) {
      $('#im-file-result').innerHTML = `<div class="alert alert-danger"><strong>Error:</strong> ${esc(e.message)}</div>`;
    }
  };
});

// ═══════════════════════════════════════════════════
// CUSTOMER DETAIL — profile + recent sales + payments + redemptions
// ═══════════════════════════════════════════════════

// Customer detail route extracted to apps/pos/components/customer-detail.js
