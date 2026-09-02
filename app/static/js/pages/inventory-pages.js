// Inventory app pages — Stock Levels, Adjustments, Purchase Orders, Reorder, Dead Stock
// All render inside the Inventory app SnowUI shell (chip-warning color theme).
import { route, navigate, reload } from '../router.js';
import { api, apiPost, apiPut, apiDelete } from '../api.js';
import { $, $$, esc, fmt, fmtRs, fmtDate, icon, toast, showLoading, hideLoading,
         openModal, closeModal, skeletonCards, errorBox, emptyState } from '../utils.js';
import { initListState } from '../list-state.js';

// Shared SVG icon set for inventory pages
const SVG = {
  box: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>',
  layers: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 7l-8-4-8 4 8 4 8-4z"/><path d="M4 7v10l8 4 8-4V7"/><line x1="12" y1="11" x2="12" y2="21"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  clipboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/></svg>',
  shoppingBag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/></svg>',
  refreshCw: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><polyline points="21 3 21 8 16 8"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  minus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>',
  arrowLeft: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>',
  whatsapp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>',
  print: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>',
  trendDown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
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
// STOCK LEVELS — full inventory table with filter
// ═══════════════════════════════════════════════════
route('/stock', async (el, path, q) => {
  // v8.18.5: search + stock filter persist across navigation
  const st = initListState('stock', q, { q: '', filter: '' });
  st.syncUrlIfRestored();
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-warning">${SVG.layers}</div>
      <div>
        <h2 class="pos-page-header-title">Stock Levels</h2>
        <p class="pos-page-header-sub">Live stock per category &mdash; purchased minus sold plus adjustments.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-secondary" id="st-rebuild-btn" title="Rebuild running weighted-avg stock state from all bills + sales">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span>
          Rebuild Stock State
        </button>
        <button class="btn" id="st-adjust-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span>
          New Adjustment
        </button>
      </div>
    </div>

    <div id="st-stats" class="mb-4"></div>

    <div class="card mb-4">
      <div class="filter-bar">
        <div class="search-input filter-search">
          ${SVG.search}
          <input class="input" id="st-search" placeholder="Filter by category name or code" value="${esc(st.val('q'))}">
        </div>
        <select class="select filter-select" id="st-filter">
          <option value="">All stock</option>
          <option value="low" ${st.val('filter') === 'low' ? 'selected' : ''}>Low stock (&lt;10)</option>
          <option value="out" ${st.val('filter') === 'out' ? 'selected' : ''}>Out of stock</option>
          <option value="ok" ${st.val('filter') === 'ok' ? 'selected' : ''}>In stock</option>
        </select>
      </div>
    </div>

    <div class="card">
      <div id="st-table">${skeletonCards(3)}</div>
    </div>`;

  $('#st-adjust-btn').onclick = () => navigate('/stock/adjustments?action=new');
  $('#st-rebuild-btn').onclick = async () => {
    if (!confirm('Rebuild the running weighted-average stock state from all confirmed bills and sales?\n\nThis rewrites every sale_items.cost_price to the correct avg-at-time-of-sale. Idempotent — safe to run anytime.')) return;
    try {
      showLoading('Rebuilding stock state...');
      const r = await apiPost('/api/inventory/rebuild-stock-state', {});
      hideLoading();
      const cats = r.categories || [];
      const rewrote = r.rewrote_sales || 0;
      toast(`Rebuilt ${cats.length} categories, rewrote ${rewrote} sale_items`, 'success');
      // Reload the page to show updated state
      reload();
    } catch (e) {
      hideLoading();
      toast('Rebuild failed: ' + e.message, 'error');
    }
  };

  let allItems = [];
  try {
    const r = await api('/api/inventory');
    allItems = r.items || [];
  } catch (e) {
    $('#st-table').innerHTML = errorBox(e.message);
    return;
  }

  function renderStats() {
    const totalItems = allItems.length;
    const lowCount = allItems.filter(i => i.low_stock && !i.out_of_stock).length;
    const outCount = allItems.filter(i => i.out_of_stock).length;
    const totalValue = allItems.reduce((s, i) => s + (i.stock_value || 0), 0);
    const potentialProfit = allItems.reduce((s, i) => s + (i.potential_profit || 0), 0);

    $('#st-stats').innerHTML = `
      <div class="grid grid-4">
        ${statCard('Categories', totalItems, 'chip-primary', SVG.box)}
        ${statCard('Low Stock', lowCount, 'chip-warning', SVG.alert)}
        ${statCard('Out of Stock', outCount, 'chip-danger', SVG.alert)}
        ${statCard('Stock Value', fmtRs(totalValue), 'chip-success', SVG.layers,
          `Profit potential: ${fmtRs(potentialProfit)}`)}
      </div>`;
  }

  function renderTable() {
    const q = $('#st-search').value.toLowerCase().trim();
    const f = $('#st-filter').value;
    let filtered = allItems;
    if (q) {
      filtered = filtered.filter(i =>
        (i.category_name || '').toLowerCase().includes(q) ||
        (i.code || '').toLowerCase().includes(q)
      );
    }
    if (f === 'low') filtered = filtered.filter(i => i.low_stock && !i.out_of_stock);
    if (f === 'out') filtered = filtered.filter(i => i.out_of_stock);
    if (f === 'ok') filtered = filtered.filter(i => !i.low_stock && !i.out_of_stock);

    if (!filtered.length) {
      $('#st-table').innerHTML = emptyState(
        'No stock items found',
        q || f ? 'Try adjusting your filters.' : 'Confirm a bill with line items to see stock levels.',
        '', ''
      );
      return;
    }

    $('#st-table').innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Code</th><th>Category</th>
            <th class="table-num">Purchased</th><th class="table-num">Sold</th>
            <th class="table-num">Adj.</th><th class="table-num">Stock</th>
            <th class="table-num">Avg Cost</th><th class="table-num">Stock Value</th>
            <th>Status</th>
          </tr></thead>
          <tbody>${filtered.map(i => {
            const statusBadge = i.negative_stock
              ? '<span class="badge badge-danger" title="Stock is negative — oversold or unrecorded adjustment">⚠ Negative</span>'
              : i.out_of_stock
                ? '<span class="badge badge-danger">Out</span>'
                : i.low_stock
                  ? '<span class="badge badge-warning">Low</span>'
                  : '<span class="badge badge-success">OK</span>';
            const stockClass = i.negative_stock ? 'text-danger font-bold'
              : i.out_of_stock ? 'text-danger font-bold'
              : i.low_stock ? 'text-warning font-bold'
              : 'font-semibold';
            const rowClass = i.negative_stock ? ' style="background:var(--danger-soft, #fef2f2)"' : '';
            return `<tr${rowClass}>
              <td><span class="pos-cat-code" style="background:${esc(i.color || '#888')}">${esc(i.code || '—')}</span></td>
              <td class="font-semibold">${esc(i.category_name)}</td>
              <td class="table-num">${fmt(i.purchased)}</td>
              <td class="table-num">${fmt(i.sold)}</td>
              <td class="table-num ${i.adjustments >= 0 ? 'text-success' : 'text-danger'}">${i.adjustments >= 0 ? '+' : ''}${fmt(i.adjustments)}</td>
              <td class="table-num ${stockClass}">${fmt(i.stock)}</td>
              <td class="table-num">${fmtRs(i.avg_cost)}</td>
              <td class="table-num font-semibold">${fmtRs(i.stock_value)}</td>
              <td>${statusBadge}</td>
            </tr>`;
          }).join('')}</tbody>
        </table>
      </div>`;
  }

  renderStats();
  renderTable();
  // v8.18.5: persist filter state (silent URL update)
  $('#st-search').oninput = () => { st.replace({ q: $('#st-search').value }); renderTable(); };
  $('#st-filter').onchange = () => { st.replace({ filter: $('#st-filter').value }); renderTable(); };
});

// ═══════════════════════════════════════════════════
// ADJUSTMENTS — list + new adjustment modal
// ═══════════════════════════════════════════════════
route('/stock/adjustments', async (el, path, q) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-warning">${SVG.refreshCw}</div>
      <div>
        <h2 class="pos-page-header-title">Stock Adjustments</h2>
        <p class="pos-page-header-sub">Manual stock corrections with audit trail. Positive delta adds stock, negative removes.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn" id="adj-new-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          New Adjustment
        </button>
      </div>
    </div>
    <div class="card">
      <div class="card-title"><h3>Adjustment History</h3></div>
      <div id="adj-list">${skeletonCards(3)}</div>
    </div>`;

  $('#adj-new-btn').onclick = () => openAdjustmentModal();

  // Auto-open modal if ?action=new
  if (q.action === 'new') openAdjustmentModal();

  await loadAdjustments();

  async function loadAdjustments() {
    try {
      const r = await api('/api/inventory/adjust');
      const list = r.adjustments || [];
      if (!list.length) {
        $('#adj-list').innerHTML = emptyState(
          'No adjustments yet',
          'Use New Adjustment to add or remove stock for any category. All changes are logged with reason.',
          'New Adjustment', ''
        );
        const eb = document.querySelector('.empty-state button');
        if (eb) eb.onclick = () => openAdjustmentModal();
        return;
      }
      $('#adj-list').innerHTML = `
        <div class="table-wrap">
          <table>
            <thead><tr>
              <th>Date</th><th>Code</th><th>Category</th>
              <th class="table-num">Delta</th><th>Reason</th>
            </tr></thead>
            <tbody>${list.map(a => `<tr>
              <td class="text-sm">${fmtDate(a.created_at)}</td>
              <td><span class="badge badge-accent">${esc(a.code || '—')}</span></td>
              <td>${esc(a.cat_name || `Category #${a.category_id}`)}</td>
              <td class="table-num font-bold ${a.delta >= 0 ? 'text-success' : 'text-danger'}">${a.delta >= 0 ? '+' : ''}${fmt(a.delta)}</td>
              <td class="text-sm">${esc(a.reason)}</td>
            </tr>`).join('')}</tbody>
          </table>
        </div>`;
    } catch (e) {
      $('#adj-list').innerHTML = errorBox(e.message);
    }
  }

  async function openAdjustmentModal() {
    let categories = [];
    try {
      categories = await api('/api/categories');
    } catch (e) {
      toast('Error loading categories: ' + e.message, 'error');
      return;
    }
    const activeCats = categories.filter(c => c.active);
    if (!activeCats.length) {
      toast('No active categories. Add one in Settings first.', 'error');
      return;
    }

    openModal(
      'New Stock Adjustment',
      `
      <div class="stat-list mb-4">
        <div class="stat-row"><span>Tip</span><span class="text-sm text-dim">Positive adds stock (e.g., found items); negative removes (e.g., damaged, lost).</span></div>
      </div>
      <div class="mt-2">
        <label>Category</label>
        <select class="select" id="adj-cat">
          ${activeCats.map(c => `<option value="${c.id}">${esc(c.code)} &mdash; ${esc(c.name)} (Rs ${fmt(c.sell_price)})</option>`).join('')}
        </select>
      </div>
      <div class="mt-3">
        <label>Delta (positive or negative)</label>
        <input class="input" id="adj-delta" type="number" value="0" step="1" autofocus>
      </div>
      <div class="mt-3">
        <label>Reason (min 3 chars)</label>
        <textarea class="textarea" id="adj-reason" rows="2" placeholder="e.g., Found 5 damaged units in storage"></textarea>
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="adj-save-btn">
         <span style="display:inline-flex;width:14px;height:14px">${SVG.save}</span>
         Save Adjustment
       </button>`
    );

    $('#adj-save-btn').onclick = async () => {
      const categoryId = parseInt($('#adj-cat').value);
      const delta = parseInt($('#adj-delta').value);
      const reason = $('#adj-reason').value.trim();
      if (!delta) { toast('Delta must be non-zero', 'error'); return; }
      if (reason.length < 3) { toast('Reason is required (min 3 chars)', 'error'); return; }
      try {
        await apiPost('/api/inventory/adjust', { category_id: categoryId, delta, reason });
        toast(`Stock adjusted by ${delta >= 0 ? '+' : ''}${delta}`, 'success');
        closeModal();
        loadAdjustments();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }
});

// ═══════════════════════════════════════════════════
// PURCHASE ORDERS — list + create modal + detail view
// ═══════════════════════════════════════════════════
route('/purchase-orders', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-warning">${SVG.shoppingBag}</div>
      <div>
        <h2 class="pos-page-header-title">Purchase Orders</h2>
        <p class="pos-page-header-sub">Create POs for suppliers, send via WhatsApp, mark received when stock arrives.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn" id="po-new-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          New PO
        </button>
      </div>
    </div>
    <div class="card">
      <div id="po-list">${skeletonCards(3)}</div>
    </div>`;

  $('#po-new-btn').onclick = () => openCreatePOModal();

  await loadPOs();

  async function loadPOs(status = '') {
    try {
      const r = await api(`/api/purchase-orders${status ? '?status=' + status : ''}`);
      const list = r.purchase_orders || [];
      if (!list.length) {
        $('#po-list').innerHTML = emptyState(
          'No purchase orders yet',
          'Create one to send to a supplier &mdash; it will appear here with status tracking.',
          'New PO', ''
        );
        const eb = document.querySelector('.empty-state button');
        if (eb) eb.onclick = () => openCreatePOModal();
        return;
      }
      $('#po-list').innerHTML = `
        <div class="table-wrap">
          <table class="table-clickable">
            <thead><tr>
              <th>PO #</th><th>Date</th><th>Supplier</th><th>Expected</th>
              <th class="table-num">Total</th><th>Status</th><th></th>
            </tr></thead>
            <tbody>${list.map(po => `<tr class="po-row" data-id="${po.id}">
              <td class="font-mono text-sm">${esc(po.po_no)}</td>
              <td class="text-sm">${fmtDate(po.created_at)}</td>
              <td>${esc(po.supplier_name || '—')}</td>
              <td class="text-sm">${esc(po.expected_date || '—')}</td>
              <td class="table-num font-semibold">${fmtRs(po.total)}</td>
              <td><span class="badge ${po.status === 'draft' ? 'badge-warning' : po.status === 'sent' ? 'badge-accent' : po.status === 'received' ? 'badge-success' : 'badge-danger'}">${esc(po.status)}</span></td>
              <td><button class="btn btn-ghost btn-sm btn-icon" data-delete-po="${po.id}" title="Delete">${SVG.trash}</button></td>
            </tr>`).join('')}</tbody>
          </table>
        </div>`;

      $$('.po-row').forEach(row => {
        row.onclick = (e) => {
          if (e.target.closest('[data-delete-po]')) return;
          navigate('/purchase-orders/' + row.dataset.id);
        };
      });
      $$('[data-delete-po]').forEach(btn => {
        btn.onclick = async (e) => {
          e.stopPropagation();
          if (!confirm('Delete this PO?')) return;
          try {
            await apiDelete(`/api/purchase-orders/${btn.dataset.deletePo}`);
            toast('PO deleted', 'success');
            loadPOs();
          } catch (e) { toast('Error: ' + e.message, 'error'); }
        };
      });
    } catch (e) {
      $('#po-list').innerHTML = errorBox(e.message);
    }
  }

  function openCreatePOModal() {
    openModal(
      'New Purchase Order',
      `
      <div class="mb-3">
        <label>Supplier Name</label>
        <input class="input" id="po-supplier" placeholder="Supplier name (or pick from existing)">
      </div>
      <div class="mb-3">
        <label>Expected Delivery</label>
        <input class="input" id="po-expected" type="date">
      </div>
      <div class="mb-2">
        <label>Notes</label>
        <textarea class="textarea" id="po-notes" rows="2" placeholder="Optional notes"></textarea>
      </div>
      <hr class="my-3">
      <h4>Items</h4>
      <div id="po-items-list"></div>
      <button class="btn btn-secondary btn-sm mt-2" id="po-add-row-btn">
        <span style="display:inline-flex;width:12px;height:12px">${SVG.plus}</span>
        Add Item
      </button>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="po-save-btn">
         <span style="display:inline-flex;width:14px;height:14px">${SVG.save}</span>
         Create PO
       </button>`
    );

    // Add 3 default rows
    [1, 2, 3].forEach(() => addPORow());
    $('#po-add-row-btn').onclick = addPORow;
    $('#po-save-btn').onclick = async () => {
      const items = [];
      $$('#po-items-list .po-item-row').forEach(row => {
        const name = row.querySelector('.po-item-name').value.trim();
        const qty = parseInt(row.querySelector('.po-item-qty').value) || 0;
        const price = parseFloat(row.querySelector('.po-item-price').value) || 0;
        if (name && qty > 0) items.push({ item_name: name, qty, est_price: price });
      });
      if (!items.length) { toast('Add at least one item', 'error'); return; }
      try {
        const r = await apiPost('/api/purchase-orders', {
          supplier_name: $('#po-supplier').value,
          items,
          notes: $('#po-notes').value,
          expected_date: $('#po-expected').value,
        });
        toast(`PO ${r.po_no} created`, 'success');
        closeModal();
        loadPOs();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }

  function addPORow() {
    const list = $('#po-items-list');
    const div = document.createElement('div');
    div.className = 'po-item-row';
    div.style.cssText = 'display:grid;grid-template-columns:1fr 70px 90px auto;gap:6px;margin-bottom:6px';
    div.innerHTML = `
      <input class="input input-sm po-item-name" placeholder="Item name">
      <input class="input input-sm po-item-qty" type="number" value="1" min="1" title="Qty">
      <input class="input input-sm po-item-price" type="number" value="0" min="0" title="Est price">
      <button class="btn btn-ghost btn-sm btn-icon" data-po-remove title="Remove">${SVG.x}</button>`;
    list.appendChild(div);
    div.querySelector('[data-po-remove]').onclick = () => div.remove();
  }
});

// PO detail page (still inside inventory app shell)
route('/purchase-orders/', async (el, path) => {
  const id = path.split('/').pop();
  // v8.18.11: guard bare/invalid id instead of firing a pointless 422/404
  if (!id || !/^\d+$/.test(id)) {
    el.innerHTML = emptyState('Not found', 'No purchase order id in the URL.', '', '');
    return;
  }
  let po;
  try { po = await api(`/api/purchase-orders/${id}`); }
  catch {
    el.innerHTML = emptyState('Not found', 'This purchase order does not exist.', '', '');
    return;
  }

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-warning">${SVG.shoppingBag}</div>
      <div>
        <h2 class="pos-page-header-title">${esc(po.po_no)}</h2>
        <p class="pos-page-header-sub">Purchase order details &middot; ${fmtDate(po.created_at)}</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-secondary btn-sm" id="po-back-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.arrowLeft}</span>
          Back
        </button>
        <button class="btn btn-secondary btn-sm" id="po-whatsapp-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.whatsapp}</span>
          WhatsApp
        </button>
        <button class="btn btn-secondary btn-sm" id="po-print-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.print}</span>
          Print
        </button>
        ${po.status !== 'received' ? `<button class="btn btn-sm" id="po-received-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span>
          Mark Received
        </button>` : ''}
        <button class="btn btn-danger btn-sm" id="po-delete-btn">${SVG.trash}</button>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <h3>Details</h3>
        <div class="stat-list mt-3">
          <div class="stat-row"><span>Supplier</span><span>${esc(po.supplier_name || '—')}</span></div>
          <div class="stat-row"><span>Status</span><span><span class="badge ${po.status === 'draft' ? 'badge-warning' : po.status === 'sent' ? 'badge-accent' : po.status === 'received' ? 'badge-success' : 'badge-danger'}">${esc(po.status)}</span></span></div>
          <div class="stat-row"><span>Created</span><span>${fmtDate(po.created_at)}</span></div>
          <div class="stat-row"><span>Expected</span><span>${esc(po.expected_date || '—')}</span></div>
          ${po.sent_via ? `<div class="stat-row"><span>Sent via</span><span>${esc(po.sent_via)}</span></div>` : ''}
          <div class="stat-row" style="border-top:2px solid var(--border)"><span class="font-bold">Total</span><span class="font-bold">${fmtRs(po.total)}</span></div>
          ${po.notes ? `<div class="stat-row"><span>Notes</span><span>${esc(po.notes)}</span></div>` : ''}
        </div>
      </div>
      <div class="card">
        <h3>Items (${po.items.length})</h3>
        <div class="table-wrap mt-3">
          <table>
            <thead><tr><th>Item</th><th class="table-num">Qty</th><th class="table-num">Est Price</th><th class="table-num">Total</th></tr></thead>
            <tbody>${po.items.map(i => `<tr>
              <td>${esc(i.item_name)}</td>
              <td class="table-num">${i.qty}</td>
              <td class="table-num">${fmtRs(i.est_price)}</td>
              <td class="table-num font-semibold">${fmtRs(i.line_total)}</td>
            </tr>`).join('')}</tbody>
          </table>
        </div>
      </div>
    </div>`;

  $('#po-back-btn').onclick = () => navigate('/purchase-orders');
  $('#po-whatsapp-btn').onclick = async () => {
    try {
      const r = await api(`/api/purchase-orders/${po.id}/whatsapp`);
      if (r.url) {
        window.open(r.url, '_blank');
        await apiPut(`/api/purchase-orders/${po.id}/status`, { status: 'sent', sent_via: 'whatsapp' });
        toast('Marked as sent via WhatsApp', 'success');
        reload();
      } else {
        toast('No supplier phone on file', 'info');
      }
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  };
  $('#po-print-btn').onclick = () => window.open(`/api/purchase-orders/${po.id}/whatsapp`, '_blank');
  const recvBtn = $('#po-received-btn');
  if (recvBtn) recvBtn.onclick = async () => {
    try {
      await apiPut(`/api/purchase-orders/${po.id}/status`, { status: 'received' });
      toast('Marked as received', 'success');
      reload();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  };
  $('#po-delete-btn').onclick = async () => {
    if (!confirm('Delete this PO?')) return;
    try {
      await apiDelete(`/api/purchase-orders/${po.id}`);
      toast('PO deleted', 'success');
      navigate('/purchase-orders');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  };
});

// ═══════════════════════════════════════════════════
// REORDER — low-stock reminders, dismiss / mark ordered
// ═══════════════════════════════════════════════════
route('/reorder', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-warning">${SVG.refreshCw}</div>
      <div>
        <h2 class="pos-page-header-title">Reorder Reminders</h2>
        <p class="pos-page-header-sub">Auto-generated alerts for low-stock categories. Dismiss or mark as ordered.</p>
      </div>
    </div>
    <div id="re-stats" class="mb-4"></div>
    <div class="card">
      <div class="card-title"><h3>Active Reminders</h3></div>
      <div id="re-list">${skeletonCards(3)}</div>
    </div>`;

  await loadReminders();

  async function loadReminders() {
    try {
      const r = await api('/api/reorder-reminders');
      const list = r.reminders || [];

      if (!list.length) {
        $('#re-stats').innerHTML = '';
        $('#re-list').innerHTML = `
          <div class="empty-state">
            <div class="empty-state-icon" style="background:var(--success-soft);color:var(--success-text)">${SVG.check}</div>
            <h3>All stocked up!</h3>
            <p>No active reorder reminders. The system checks stock levels and sales velocity to suggest reorders.</p>
          </div>`;
        return;
      }

      // v8.18.10 FIX: reads the REAL API fields now. The old code read
      // suggested_qty / avg_cost / category_name / current_stock / last_sold /
      // code / color — none of which the endpoint ever returned, so every
      // stat card showed 0 and the rows rendered empty shells.
      const totalValue = list.reduce((s, rem) => s + (rem.suggested_quantity || 0) * (rem.avg_price || 0), 0);
      $('#re-stats').innerHTML = `
        <div class="grid grid-3">
          ${statCard('Active Reminders', fmt(list.length), 'chip-warning', SVG.alert)}
          ${statCard('Total Suggested Qty', fmt(list.reduce((s, rem) => s + (rem.suggested_quantity || 0), 0)), 'chip-primary', SVG.box)}
          ${statCard('Est. Order Value', fmtRs(totalValue), 'chip-success', SVG.shoppingBag)}
        </div>`;

      $('#re-list').innerHTML = `
        <div class="re-grid">
          ${list.map(rem => `
            <div class="card re-card">
              <div class="re-card-header">
                <span class="pos-cat-code" style="background:${
                  rem.priority === 'high' ? 'var(--danger-soft)' : 'var(--warning-soft)'}">${
                  esc(rem.priority || 'med')}</span>
                <div style="flex:1">
                  <div class="font-semibold">${esc(rem.item_name || 'Item')}</div>
                  <div class="text-xs text-dim">Last bought ${fmt(rem.days_since || 0)}d ago (avg gap ${fmt(rem.avg_gap_days || 0)}d) &middot; Suggested: ${fmt(rem.suggested_quantity || 0)} @ ${fmtRs(rem.avg_price || 0)}</div>
                </div>
                <span class="badge ${rem.priority === 'high' ? 'badge-danger' : 'badge-warning'}">${esc(rem.priority || 'medium')}</span>
              </div>
              ${rem.seasonal_note ? `<p class="text-sm mt-2" style="margin:8px 0 0">${esc(rem.seasonal_note)}</p>` : ''}
              ${rem.supplier_name ? `<p class="text-xs text-dim mt-1">Usually from: ${esc(rem.supplier_name)} (${fmt(rem.total_purchases || 0)} purchases)</p>` : ''}
              <div class="re-card-actions">
                <button class="btn btn-sm" data-re-order="${rem.id}">Mark Ordered</button>
                <button class="btn btn-secondary btn-sm" data-re-dismiss="${rem.id}">Dismiss</button>
              </div>
            </div>
          `).join('')}
        </div>`;

      $$('[data-re-order]').forEach(btn => {
        btn.onclick = async () => {
          try {
            await apiPost(`/api/reorder-reminders/${btn.dataset.reOrder}/ordered`, {});
            toast('Marked as ordered', 'success');
            loadReminders();
          } catch (e) { toast('Error: ' + e.message, 'error'); }
        };
      });
      $$('[data-re-dismiss]').forEach(btn => {
        btn.onclick = async () => {
          try {
            await apiPost(`/api/reorder-reminders/${btn.dataset.reDismiss}/dismiss`, {});
            toast('Reminder dismissed', 'success');
            loadReminders();
          } catch (e) { toast('Error: ' + e.message, 'error'); }
        };
      });
    } catch (e) {
      $('#re-list').innerHTML = errorBox(e.message);
    }
  }
});

// ═══════════════════════════════════════════════════
// DEAD STOCK — slow-moving items, clearance suggestions
// ═══════════════════════════════════════════════════
// Dead stock route extracted to apps/pos/components/dead-stock.js


// ═══════════════════════════════════════════════════
// v8.4: CUSTOM ITEMS & ITEM DISCOUNTS (moved from Settings to Inventory)
// ═══════════════════════════════════════════════════
route('/inventory/custom-items', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-warning">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
      </div>
      <div>
        <h2 class="pos-page-header-title">Custom Items &amp; Discounts</h2>
        <p class="pos-page-header-sub">Add non-category items (bags, accessories) and set item-level discounts with full audit trail.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-primary btn-sm" id="btn-add-custom">+ Custom Item</button>
        <button class="btn btn-secondary btn-sm" id="btn-add-discount">+ Discount Rule</button>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <h3>Custom Items</h3>
        <p class="text-dim text-sm mb-3">Items that aren't price categories (e.g. bags, accessories, services).</p>
        <div id="custom-items-list" class="mt-2"></div>
      </div>
      <div class="card">
        <h3>Item Discounts</h3>
        <p class="text-dim text-sm mb-3">Set discounts on specific items or all items. Full audit trail in Activity Log.</p>
        <div id="discounts-list" class="mt-2"></div>
      </div>
    </div>`;

  await loadAll();

  async function loadAll() {
    try {
      const [customRes, discountRes] = await Promise.all([
        api('/api/custom-items'),
        api('/api/item-discounts'),
      ]);
      renderCustomItems(customRes.items || []);
      renderDiscounts(discountRes.discounts || []);
    } catch (e) {
      toast('Error: ' + e.message, 'error');
    }
  }

  function renderCustomItems(items) {
    const list = $('#custom-items-list');
    if (!items.length) {
      list.innerHTML = '<p class="text-dim text-sm">No custom items yet.</p>';
      return;
    }
    list.innerHTML = `<table><thead><tr><th>Name</th><th>Category</th><th>Price</th><th></th></tr></thead>
      <tbody>${items.map(i => `<tr>
        <td>${esc(i.name)}</td>
        <td class="text-sm text-dim">${esc(i.category)}</td>
        <td class="table-num">${fmtRs(i.sell_price)}</td>
        <td><button class="btn btn-ghost btn-sm btn-icon" data-del-custom="${i.id}" title="Delete"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg></button></td>
      </tr>`).join('')}</tbody></table>`;
    $$('[data-del-custom]').forEach(btn => btn.onclick = async () => {
      if (!confirm('Delete this custom item?')) return;
      try { await apiDelete(`/api/custom-items/${btn.dataset.delCustom}`); toast('Deleted', 'success'); loadAll(); }
      catch (e) { toast('Error: ' + e.message, 'error'); }
    });
  }

  function renderDiscounts(discounts) {
    const list = $('#discounts-list');
    if (!discounts.length) {
      list.innerHTML = '<p class="text-dim text-sm">No discount rules yet.</p>';
      return;
    }
    list.innerHTML = `<table><thead><tr><th>Applies To</th><th>Discount</th><th>Reason</th><th></th></tr></thead>
      <tbody>${discounts.map(d => `<tr>
        <td class="text-sm">${d.applies_to === 'all' ? 'All items' : d.applies_to === 'category' ? 'Category: ' + esc(d.category_name || '?') : 'Item: ' + esc(d.custom_item_name || '?')}</td>
        <td class="table-num">${d.discount_type === 'percent' ? d.discount_value + '%' : fmtRs(d.discount_value)}</td>
        <td class="text-sm text-dim">${esc(d.reason || '—')}</td>
        <td><button class="btn btn-ghost btn-sm btn-icon" data-del-disc="${d.id}" title="Remove"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg></button></td>
      </tr>`).join('')}</tbody></table>`;
    $$('[data-del-disc]').forEach(btn => btn.onclick = async () => {
      if (!confirm('Remove this discount rule?')) return;
      try { await apiDelete(`/api/item-discounts/${btn.dataset.delDisc}`); toast('Removed', 'success'); loadAll(); }
      catch (e) { toast('Error: ' + e.message, 'error'); }
    });
  }

  $('#btn-add-custom').onclick = () => {
    openModal('Add Custom Item', `
      <div class="form-group"><label class="form-label">Name</label><input class="input" id="ci-name" placeholder="e.g. Shopping Bag"></div>
      <div class="grid grid-2">
        <div class="form-group"><label class="form-label">Price (Rs)</label><input class="input" id="ci-price" type="number" value="0"></div>
        <div class="form-group"><label class="form-label">Cost (Rs)</label><input class="input" id="ci-cost" type="number" value="0"></div>
      </div>
      <div class="grid grid-2">
        <div class="form-group"><label class="form-label">Category</label><input class="input" id="ci-cat" placeholder="e.g. Bags" value="Miscellaneous"></div>
        <div class="form-group"><label class="form-label">Code (optional)</label><input class="input" id="ci-code" placeholder="e.g. BAG001"></div>
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button><button class="btn btn-primary" id="ci-save">Save</button>`);
    $('#ci-save').onclick = async () => {
      try {
        await apiPost('/api/custom-items', {
          name: $('#ci-name').value, sell_price: parseFloat($('#ci-price').value) || 0,
          cost_price: parseFloat($('#ci-cost').value) || 0, category: $('#ci-cat').value,
          code: $('#ci-code').value,
        });
        toast('Custom item added', 'success'); closeModal(); loadAll();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  };

  $('#btn-add-discount').onclick = async () => {
    // v8.4: Fetch categories + custom items for the selection dropdowns
    let cats = [], customItems = [];
    try {
      const [catRes, customRes] = await Promise.all([
        api('/api/pos/categories'),
        api('/api/custom-items'),
      ]);
      cats = (catRes || []).filter(c => !c.is_custom);
      customItems = customRes.items || [];
    } catch (e) { /* ignore — dropdowns will be empty */ }

    openModal('Add Discount Rule', `
      <div class="form-group"><label class="form-label">Applies To</label>
        <select class="input" id="d-applies">
          <option value="all">All items</option>
          <option value="category">Specific category</option>
          <option value="custom_item">Specific custom item</option>
        </select>
      </div>
      <div class="form-group" id="d-cat-row" style="display:none">
        <label class="form-label">Select Category</label>
        <select class="input" id="d-category">
          ${cats.map(c => `<option value="${c.id}">${esc(c.name)} — Rs ${c.sell_price} (code: ${c.code})</option>`).join('')}
        </select>
      </div>
      <div class="form-group" id="d-custom-row" style="display:none">
        <label class="form-label">Select Custom Item</label>
        <select class="input" id="d-custom">
          ${customItems.map(i => `<option value="${i.id}">${esc(i.name)} — Rs ${i.sell_price} (${esc(i.category)})</option>`).join('')}
        </select>
      </div>
      <div class="grid grid-2">
        <div class="form-group"><label class="form-label">Discount Type</label><select class="input" id="d-type"><option value="percent">Percentage</option><option value="amount">Fixed Amount</option></select></div>
        <div class="form-group"><label class="form-label">Value</label><input class="input" id="d-val" type="number" value="0"></div>
      </div>
      <div class="form-group"><label class="form-label">Reason (for audit trail)</label><input class="input" id="d-reason" placeholder="e.g. Damaged stock, clearance sale"></div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button><button class="btn btn-primary" id="d-save">Save</button>`);

    // v8.4: Show/hide category/custom item dropdown based on "Applies To" selection
    const appliesSelect = $('#d-applies');
    const catRow = $('#d-cat-row');
    const customRow = $('#d-custom-row');
    appliesSelect.onchange = () => {
      catRow.style.display = (appliesSelect.value === 'category') ? '' : 'none';
      customRow.style.display = (appliesSelect.value === 'custom_item') ? '' : 'none';
    };

    $('#d-save').onclick = async () => {
      const applies = appliesSelect.value;
      const payload = {
        applies_to: applies,
        discount_type: $('#d-type').value,
        discount_value: parseFloat($('#d-val').value) || 0,
        reason: $('#d-reason').value,
      };
      // v8.4: Include category_id or custom_item_id based on selection
      if (applies === 'category') {
        payload.category_id = parseInt($('#d-category').value) || null;
        if (!payload.category_id) { toast('Select a category', 'error'); return; }
      } else if (applies === 'custom_item') {
        payload.custom_item_id = parseInt($('#d-custom').value) || null;
        if (!payload.custom_item_id) { toast('Select a custom item', 'error'); return; }
      }
      try {
        await apiPost('/api/item-discounts', payload);
        toast('Discount rule added', 'success'); closeModal(); loadAll();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  };
});
