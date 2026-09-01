import { initListState } from '../list-state.js';

// v8.7: Items page — bill-wise master-detail view.
// Shows bills first (lightweight list with aggregates); clicking a bill
// expands it inline to reveal its bill_items (fetched lazily via
// GET /api/bills/{id}). Search filters by supplier, bill_no, or item name.
//
// Replaces the old flat item-list view (which showed all bill_items across
// all bills in one ungrouped list — confusing when a single item appears in
// multiple bills).
import { route, navigate } from '../router.js';
import { api } from '../api.js';
import { pagination } from '../components/pagination.js';
import { $, $$, esc, fmt, fmtRs, fmtDate, icon, iconHtml, debounce,
         skeletonRows, errorBox, emptyState } from '../utils.js';

const SVG = {
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  bills: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
};

route('/items', async (el, path, q) => {
  // v8.18.5: search + page persist across navigation (URL first, storage
  // fallback) — list → bill detail → Back keeps your search and page.
  const st = initListState('items', q, { q: '', page: 1 });
  st.syncUrlIfRestored();
  const search = st.val('q');
  const page = st.val('page');

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-warning">${SVG.bills}</div>
      <div>
        <h2 class="pos-page-header-title">Items by Bill</h2>
        <p class="pos-page-header-sub">Browse bills and expand any to see its line items. Search by supplier, bill #, or item name.</p>
      </div>
    </div>

    <div class="card mb-4">
      <div class="filter-bar">
        <div class="search-input filter-search">
          ${SVG.search}
          <input class="input" id="i-search" placeholder="Search by supplier, bill #, or item name (e.g. 'Toy Car' or 'ABC Trading')" value="${esc(search)}">
        </div>
      </div>
    </div>

    <div id="i-stats"></div>
    <div id="i-results" class="card mt-4">${skeletonRows(8, 6)}</div>`;

  // Debounced search → silent URL/state update + local reload
  const debouncedSearch = debounce(() => doSearch(), 400);
  $('#i-search').oninput = debouncedSearch;

  function doSearch() {
    st.replace({ q: $('#i-search').value, page: 1 });
    load();
  }

  // v8.7: fetch the bill list (lightweight — no items embedded)
  await load();

  async function load() {
  const curQ = st.val('q');
  const curPage = st.val('page');
  try {
    const data = await api(`/api/items/bills?q=${encodeURIComponent(curQ)}&page=${curPage}`);

    // v8.19.1: backend clamps the page when the requested one no longer
    // exists (bills deleted off the last page / search shrank the result) —
    // follow it so the pager, URL and saved state show the page served.
    if (data.page && Number(data.page) !== Number(curPage)) {
      st.replace({ page: data.page });
    }

    // Summary stats
    if (data.total > 0) {
      const totalCost = data.bills.reduce((s, b) => s + (b.total_cost || 0), 0);
      const totalItems = data.bills.reduce((s, b) => s + (b.item_count || 0), 0);
      $('#i-stats').innerHTML = `
        <div class="grid grid-3 mb-4">
          <div class="stat-card">
            <div class="stat-card-icon chip-primary">${SVG.bills}</div>
            <div class="stat-card-label">Total Bills</div>
            <div class="stat-card-value">${data.total}</div>
          </div>
          <div class="stat-card">
            <div class="stat-card-icon chip-success">${SVG.wallet}</div>
            <div class="stat-card-label">Total Items</div>
            <div class="stat-card-value">${fmt(totalItems)}</div>
          </div>
          <div class="stat-card">
            <div class="stat-card-icon chip-info">${SVG.wallet}</div>
            <div class="stat-card-label">Total Cost</div>
            <div class="stat-card-value">${fmtRs(totalCost)}</div>
          </div>
        </div>`;
    } else {
      $('#i-stats').innerHTML = '';
    }

    // Master list — each row is a bill; click to expand
    if (data.bills.length === 0) {
      $('#i-results').innerHTML = curQ
        ? emptyState('No bills found', `No bills match "${esc(curQ)}". Try a different search term.`, '', '')
        : emptyState('No bills yet', 'Upload a bill to see items here. Bills in both review and confirmed status are shown.', '', '');
      return;
    }

    $('#i-results').innerHTML = `
      <div class="card-title"><h3>Bills (${data.total})</h3></div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr>
            <th style="width:32px"></th>
            <th>Bill #</th>
            <th>Supplier</th>
            <th>Date</th>
            <th class="table-num">Total</th>
            <th>Items</th>
            <th>Cats</th>
            <th class="table-num">Cost</th>
            <th>Status</th>
            <th>Payment</th>
            <th></th>
          </tr></thead>
          <tbody id="i-bills-tbody">
            ${data.bills.map(b => renderBillRow(b)).join('')}
          </tbody>
        </table>
      </div>
      ${pagination(data, data.page || curPage, '/items', { q: curQ })}
      <div class="text-xs text-dim text-center mt-3">
        ${data.total} bills found &middot; showing page ${data.page} of ${data.pages_total || 1}
      </div>`;

    // v8.19.1: local refresh on pagination clicks — re-fetch and re-render
    // just the results card instead of rebuilding the whole page shell.
    $$('.pagination button:not([disabled])').forEach(btn => {
      const originalOnclick = btn.getAttribute('onclick');
      if (!originalOnclick) return;
      btn.removeAttribute('onclick');
      btn.onclick = (e) => {
        e.preventDefault();
        const match = originalOnclick.match(/page=(\d+)/);
        const newPage = match ? parseInt(match[1]) : 1;
        st.push({ page: newPage });
        load();
      };
    });

    // Wire row click → toggle expand
    $$('.i-bill-row').forEach(row => {
      row.onclick = (e) => {
        // Don't toggle if the user clicked the "Edit Bill" button or a link
        if (e.target.closest('.i-edit-btn') || e.target.closest('a')) return;
        toggleBillExpand(row);
      };
    });
  } catch (e) {
    $('#i-results').innerHTML = errorBox(e.message, "location.reload()");
  }
  } // end of load()
});

// Render a single bill row in the master list
function renderBillRow(b) {
  const statusBadge = b.status === 'confirmed'
    ? '<span class="badge badge-success">Confirmed</span>'
    : '<span class="badge badge-warning">Review</span>';
  const total = b.written_total || b.computed_total || 0;
  return `
    <tr class="i-bill-row" data-bill-id="${b.id}" style="cursor:pointer">
      <td class="i-chevron-cell" style="text-align:center">
        <span class="i-chevron" style="display:inline-block;transition:transform 0.2s">${SVG.chevron}</span>
      </td>
      <td><strong>#${b.id}</strong></td>
      <td>${esc(b.supplier_name || '—')}</td>
      <td class="text-sm text-dim">${fmtDate(b.bill_date)}</td>
      <td class="table-num">${fmtRs(total)}</td>
      <td class="text-sm">${b.item_count || 0}</td>
      <td class="text-sm text-dim">${b.category_count || '—'}</td>
      <td class="table-num text-sm">${fmtRs(b.total_cost || 0)}</td>
      <td>${statusBadge}</td>
      <td><span class="badge ${b.payment_status === 'credit' ? 'badge-danger' : 'badge-success'}">${esc(b.payment_status)}</span></td>
      <td>
        <button class="btn btn-secondary btn-sm i-edit-btn" onclick="event.stopPropagation(); window.location.hash='/bills/${b.id}'" title="Open bill in editor">
          ${SVG.edit} Edit
        </button>
      </td>
    </tr>
    <tr class="i-bill-detail-row" data-bill-id="${b.id}" style="display:none">
      <td colspan="11" style="padding:0;border-top:none">
        <div class="i-bill-detail-content" style="padding:12px 16px;background:var(--bg-elevated)">
          <em class="text-dim">Click to load items...</em>
        </div>
      </td>
    </tr>`;
}

// Cache for lazy-loaded bill details (bill_id → bill detail dict)
const _billDetailCache = new Map();

// Toggle expand/collapse of a bill's items
async function toggleBillExpand(rowEl) {
  const billId = parseInt(rowEl.dataset.billId);
  const detailRow = document.querySelector(`.i-bill-detail-row[data-bill-id="${billId}"]`);
  if (!detailRow) return;
  const chevron = rowEl.querySelector('.i-chevron');
  const content = detailRow.querySelector('.i-bill-detail-content');

  // If currently expanded → collapse
  if (detailRow.style.display !== 'none') {
    detailRow.style.display = 'none';
    if (chevron) chevron.style.transform = 'rotate(0deg)';
    return;
  }

  // Expand
  detailRow.style.display = '';
  if (chevron) chevron.style.transform = 'rotate(180deg)';

  // If content already loaded → just show it
  if (_billDetailCache.has(billId)) {
    content.innerHTML = renderBillItems(_billDetailCache.get(billId));
    return;
  }

  // Fetch bill detail (includes items) via existing GET /api/bills/{id}
  content.innerHTML = '<em class="text-dim">Loading items...</em>';
  try {
    const bill = await api(`/api/bills/${billId}`);
    _billDetailCache.set(billId, bill);
    content.innerHTML = renderBillItems(bill);
  } catch (e) {
    content.innerHTML = `<div class="text-danger text-sm">Failed to load items: ${esc(e.message)}</div>`;
  }
}

// Render the items table for an expanded bill
function renderBillItems(bill) {
  const items = bill.items || [];
  if (items.length === 0) {
    return '<em class="text-dim">This bill has no items.</em>';
  }
  return `
    <div class="table-wrap" style="background:var(--bg-default)">
      <table class="table">
        <thead><tr>
          <th>Sr</th>
          <th>Item</th>
          <th>Code</th>
          <th>Category</th>
          <th class="table-num">Price</th>
          <th class="table-num">Qty</th>
          <th>Unit</th>
          <th class="table-num">Line Total</th>
        </tr></thead>
        <tbody>
          ${items.map((it, idx) => `<tr>
            <td class="text-dim">${idx + 1}</td>
            <td>${esc(it.raw || '—')}</td>
            <td class="text-sm text-dim">${esc(it.item_code || '—')}</td>
            <td class="text-sm">${esc(it.cat_name || '—')}</td>
            <td class="table-num">${fmtRs(it.price)}</td>
            <td class="table-num">${fmt(it.qty)}</td>
            <td class="text-sm">${esc(it.unit || '')}</td>
            <td class="table-num">${fmtRs(it.line_total)}</td>
          </tr>`).join('')}
        </tbody>
        <tfoot>
          <tr style="font-weight:600">
            <td colspan="7">Total</td>
            <td class="table-num">${fmtRs(items.reduce((s, i) => s + (i.line_total || 0), 0))}</td>
          </tr>
        </tfoot>
      </table>
    </div>`;
}
