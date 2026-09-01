// Bills list page — with bulk selection + inline editing
// Renders inside the Billing shell — no internal topbar (shell provides it).
import { route, navigate, reload } from '../router.js';
import { api, apiDelete, apiPost, apiPut } from '../api.js';
import { $, $$, esc, fmt, fmtRs, fmtDate, icon, iconHtml, debounce, toast,
         skeletonRows, errorBox, emptyState } from '../utils.js';
import { pagination } from '../components/pagination.js';
import { initListState } from '../list-state.js';
import { errorState } from '../core/states.js';

// Shared SVG icon set for billing pages
const SVG = {
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  inbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  bills: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
};

route('/bills', async (el, path, q) => {
  // v8.18.5: list state (page/filters/sort) now survives navigation —
  // URL params win; on a bare URL the last-used state is restored from
  // localStorage (list → bill detail → Back keeps your view).
  const st = initListState('bills', q, {
    q: '', status: '', payment: '', sort_by: '', sort_order: 'desc', page: 1,
  });
  st.syncUrlIfRestored();
  // v8.19.1: `page` is let (not const) — the backend may clamp it to the
  // last valid page (deleted last page / filter shrink) and we reassign here
  let page = st.val('page');
  const status = st.val('status');
  const search = st.val('q');
  const payment = st.val('payment');
  // v8.15.0: Dynamic sort state
  const sortBy = st.val('sort_by');
  const sortOrder = st.val('sort_order');

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-success">${SVG.bills}</div>
      <div>
        <h2 class="pos-page-header-title">All Bills</h2>
        <p class="pos-page-header-sub">Manage and review all your supplier bills. Click total or payment to edit inline.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn" id="b-new-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          New Bill
        </button>
        <button class="btn btn-secondary" id="b-export-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
          Excel
        </button>
      </div>
    </div>

    <div class="card mb-4">
      <div class="filter-bar">
        <div class="search-input filter-search">
          ${SVG.search}
          <input class="input" id="b-search" placeholder="Search supplier, phone, or bill no" value="${esc(search)}">
        </div>
        <select class="select filter-select" id="b-status">
          <option value="">All status</option>
          <option value="review" ${status === 'review' ? 'selected' : ''}>Review</option>
          <option value="confirmed" ${status === 'confirmed' ? 'selected' : ''}>Confirmed</option>
        </select>
        <select class="select filter-select" id="b-payment">
          <option value="">All payments</option>
          <option value="paid" ${payment === 'paid' ? 'selected' : ''}>Paid</option>
          <option value="credit" ${payment === 'credit' ? 'selected' : ''}>Credit</option>
        </select>
      </div>
    </div>

    <div class="card">
      <div id="bills-table">${skeletonRows(5, 8)}</div>
    </div>`;

  $('#b-new-btn').onclick = () => navigate('/bills/new');
  // v8.5.4: Excel export — uses the per-category sheet format (250/500/750/1000/Summary).
  // Exports only selected bills if any are checked, otherwise exports all bills.
  $('#b-export-btn').onclick = () => {
    const checked = document.querySelectorAll('.bill-checkbox:checked');
    const billIds = Array.from(checked).map(cb => cb.dataset.billId);
    // Use a wide date range so all bills are included
    const start = '2020-01-01', end = '2030-12-31';
    if (billIds.length > 0) {
      toast(`Exporting ${billIds.length} selected bills to Excel...`, 'info');
      location.href = `/api/reports/billwise/export?start=${start}&end=${end}&status=all&bill_ids=${billIds.join(',')}`;
    } else {
      toast('Exporting all bills to Excel...', 'info');
      location.href = `/api/reports/billwise/export?start=${start}&end=${end}&status=all`;
    }
  };

  const debouncedSearch = debounce(() => doFilter(), 350);
  $('#b-search').oninput = debouncedSearch;
  $('#b-status').onchange = doFilter;
  $('#b-payment').onchange = doFilter;

  // v8.15.0: Local refresh — re-fetches data and re-renders ONLY the table area
  // instead of calling navigate() which rebuilds the entire shell (sidebar, header, etc.)
  // v8.18.5: hash syncing now goes through the list-state helper — replaceState
  // for filters/sort (silent, no history entry) and pushState for pagination
  // (history entry so Back/Forward step through pages). The old approach set
  // location.hash directly, which pushed a history entry per keystroke and
  // needed a hashchange-suppression hack.
  let currentSortBy = sortBy;
  let currentSortOrder = sortOrder;

  function doFilter() {
    st.replace({
      q: $('#b-search').value,
      status: $('#b-status').value,
      payment: $('#b-payment').value,
      page: 1,  // filters reset to page 1
    });
    loadBills(1, st.val('q'), st.val('status'), st.val('payment'), currentSortBy, currentSortOrder);
  }

  async function loadBills(pageNum, q, statusV, pay, sb, so) {
    let data;
    try {
      data = await api(`/api/bills?page=${pageNum}&status=${encodeURIComponent(statusV)}&q=${encodeURIComponent(q)}&payment=${encodeURIComponent(pay)}&sort_by=${encodeURIComponent(sb)}&sort_order=${encodeURIComponent(so)}`);
    } catch (e) {
      $('#bills-table').innerHTML = errorBox(e.message, "location.reload()");
      return;
    }
    // v8.19.1: backend clamps the page when the requested one no longer
    // exists (deleted the last page's rows / filter shrank the result) —
    // follow it so the pager, URL and saved state show the page served.
    if (data.page && Number(data.page) !== Number(pageNum)) {
      pageNum = data.page;
      st.replace({ page: data.page });
    }
    renderTable(data, pageNum, q, statusV, pay, sb, so);
  }

  let data;
  try {
    data = await api(`/api/bills?page=${page}&status=${encodeURIComponent(status)}&q=${encodeURIComponent(search)}&payment=${encodeURIComponent(payment)}&sort_by=${encodeURIComponent(sortBy)}&sort_order=${encodeURIComponent(sortOrder)}`);
  } catch (e) {
    $('#bills-table').innerHTML = errorBox(e.message, "location.reload()");
    return;
  }
  // v8.19.1: follow a backend-clamped page (deleted last page / filter
  // shrink) — e.g. user deletes everything on page 5, is served page 4
  if (data.page && Number(data.page) !== Number(page)) {
    page = data.page;
    st.replace({ page: data.page });
  }

  // Selection state — must be outside renderTable so it persists across re-renders
  const selected = new Set();

  // Initial render
  renderTable(data, page, search, status, payment, sortBy, sortOrder);

  function renderTable(data, pageNum, searchVal, statusVal, payVal, sb, so) {
    const sortBy = sb || '';
    const sortOrder = so || 'desc';

    function renderBulkBar() {
      const bar = $('#bulk-bar');
      if (!bar) return;
      bar.style.display = selected.size === 0 ? 'none' : 'flex';
      if (selected.size > 0) $('#bulk-count').textContent = `${selected.size} selected`;
    }

    function toggleSelect(id, checked) {
      if (checked) selected.add(id);
      else selected.delete(id);
      renderBulkBar();
      const allChecked = data.bills.length > 0 && data.bills.every(b => selected.has(b.id));
      const headerCb = $('#select-all');
      if (headerCb) headerCb.checked = allChecked;
    }

    function toggleSelectAll(checked) {
      if (checked) data.bills.forEach(b => selected.add(b.id));
      else data.bills.forEach(b => selected.delete(b.id));
      $$('.row-cb').forEach(cb => { cb.checked = checked; });
      renderBulkBar();
    }

  $('#bills-table').innerHTML = data.bills.length ? `
    <div id="bulk-bar" class="bulk-bar" style="display:none">
      <span id="bulk-count" class="font-semibold">0 selected</span>
      <div class="flex gap-2">
        <button class="btn btn-secondary btn-sm" id="bulk-paid-btn">
          <span style="display:inline-flex;width:12px;height:12px">${SVG.check}</span>
          Mark Paid
        </button>
        <button class="btn btn-secondary btn-sm" id="bulk-export-btn">
          <span style="display:inline-flex;width:12px;height:12px">${SVG.download}</span>
          Export
        </button>
        <button class="btn btn-danger btn-sm" id="bulk-delete-btn">
          <span style="display:inline-flex;width:12px;height:12px">${SVG.trash}</span>
          Delete
        </button>
        <button class="btn btn-ghost btn-sm" id="bulk-clear-btn">Clear</button>
      </div>
    </div>
    <div class="table-wrap">
      <table class="table-clickable">
        <thead>
          <tr>
            <th style="width:32px"><input type="checkbox" id="select-all"></th>
            <th class="sortable ${sortBy === 'bill_no' ? 'sorted' : ''}" data-sort="bill_no">Bill No <span class="sort-indicator">${sortBy === 'bill_no' ? (sortOrder === 'asc' ? '▲' : '▼') : '⇅'}</span></th>
            <th class="sortable ${sortBy === 'supplier' ? 'sorted' : ''}" data-sort="supplier">Supplier <span class="sort-indicator">${sortBy === 'supplier' ? (sortOrder === 'asc' ? '▲' : '▼') : '⇅'}</span></th>
            <th class="sortable ${sortBy === 'date' ? 'sorted' : ''}" data-sort="date">Date <span class="sort-indicator">${sortBy === 'date' ? (sortOrder === 'asc' ? '▲' : '▼') : '⇅'}</span></th>
            <th class="table-num sortable ${sortBy === 'total' ? 'sorted' : ''}" data-sort="total">Total <span class="sort-indicator">${sortBy === 'total' ? (sortOrder === 'asc' ? '▲' : '▼') : '⇅'}</span></th>
            <th class="sortable ${sortBy === 'status' ? 'sorted' : ''}" data-sort="status">Status <span class="sort-indicator">${sortBy === 'status' ? (sortOrder === 'asc' ? '▲' : '▼') : '⇅'}</span></th>
            <th class="sortable ${sortBy === 'payment' ? 'sorted' : ''}" data-sort="payment">Payment <span class="sort-indicator">${sortBy === 'payment' ? (sortOrder === 'asc' ? '▲' : '▼') : '⇅'}</span></th>
            <th>Flags</th>
            <th>AI Review</th>
            <th class="sortable ${sortBy === 'created' ? 'sorted' : ''}" data-sort="created">Created <span class="sort-indicator">${sortBy === 'created' ? (sortOrder === 'asc' ? '▲' : '▼') : '⇅'}</span></th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${data.bills.map(b => `<tr class="bill-row" data-id="${b.id}">
            <td onclick="event.stopPropagation()"><input type="checkbox" class="row-cb" data-id="${b.id}"></td>
            <td class="text-dim">#${b.id}</td>
            <td>${esc(b.supplier_name) || '<span class="text-dim">—</span>'}</td>
            <td class="text-sm">${fmtDate(b.bill_date)}</td>
            <td class="table-num font-semibold" onclick="event.stopPropagation()">
              <span class="inline-edit" data-id="${b.id}" data-field="written_total" data-value="${b.written_total ?? ''}">${fmtRs(b.written_total || b.computed_total)}</span>
            </td>
            <td><span class="badge ${b.status === 'confirmed' ? 'badge-success' : 'badge-warning'} badge-dot">${b.status}</span></td>
            <td onclick="event.stopPropagation()">
              <select class="select inline-pay" data-id="${b.id}" style="width:auto;height:24px;padding:2px 6px;font-size:11px">
                <option value="paid" ${b.payment_status === 'paid' ? 'selected' : ''}>paid</option>
                <option value="credit" ${b.payment_status === 'credit' ? 'selected' : ''}>credit</option>
              </select>
            </td>
            <td>${b.flag_count ? `<span class="badge badge-warning">${b.flag_count}</span>` : '<span class="text-dim text-xs">—</span>'}</td>
            <td class="text-sm text-dim">${fmtDate(b.created_at)}</td>
            <td>${b.review_count > 0 ? `<span class="badge badge-danger" title="${b.review_count} items need review">${b.review_count} review</span>` : '<span class="text-dim text-xs">—</span>'}</td>
            <td onclick="event.stopPropagation()">
              <div class="row-actions">
                <button class="btn btn-ghost" title="View" data-view="${b.id}">${SVG.bills}</button>
                <button class="btn btn-ghost" title="Delete" data-delete="${b.id}" data-name="${esc(b.supplier_name || 'this bill')}">${SVG.trash}</button>
              </div>
            </td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>
    ${pagination(data, pageNum, '/bills', { status: statusVal, q: searchVal, payment: payVal, sort_by: sortBy, sort_order: sortOrder })}
    <div class="text-xs text-dim text-center mt-3">${data.total} bills total &middot; showing page ${pageNum} of ${data.pages_total || 1}</div>
  ` : emptyState('No bills found',
      searchVal || statusVal || payVal ? 'Try adjusting your filters' : 'Upload your first bill to get started',
      'Upload Bill', '');

    // v8.15.0: Intercept pagination clicks to use local refresh (no full page rebuild)
    $$('.pagination button:not([disabled])').forEach(btn => {
      const originalOnclick = btn.getAttribute('onclick');
      if (!originalOnclick) return;
      btn.removeAttribute('onclick');
      btn.onclick = (e) => {
        e.preventDefault();
        const match = originalOnclick.match(/page=(\d+)/);
        const newPage = match ? parseInt(match[1]) : 1;
        // v8.18.5: push a history entry so Back/Forward step through pages
        st.push({ page: newPage });
        loadBills(newPage, $('#b-search')?.value || '', $('#b-status')?.value || '',
                  $('#b-payment')?.value || '', currentSortBy, currentSortOrder);
      };
    });

    // v8.18.5 FIX: all table wiring lives INSIDE renderTable. It used to run
    // once after the first render — but renderTable replaces #bills-table's
    // innerHTML on every filter/sort/page change, destroying the handlers,
    // so after the first filter change rows became unclickable and sort
    // headers went dead. Re-wiring here keeps them alive across re-renders.
    $$('.bill-row').forEach(row => {
      row.onclick = () => navigate('/bills/' + row.dataset.id);
    });
    // v8.15.0: Sortable column headers — click to toggle sort (local refresh, no full navigation)
    $$('.sortable[data-sort]').forEach(th => {
      th.onclick = () => {
        const col = th.dataset.sort;
        let newSortBy = col;
        let newSortOrder = 'desc';
        if (currentSortBy === col) {
          newSortOrder = currentSortOrder === 'asc' ? 'desc' : 'asc';
        }
        currentSortBy = newSortBy;
        currentSortOrder = newSortOrder;
        st.replace({ sort_by: newSortBy, sort_order: newSortOrder, page: 1 });
        loadBills(1, $('#b-search')?.value || '', $('#b-status')?.value || '',
                  $('#b-payment')?.value || '', newSortBy, newSortOrder);
      };
    });
    $$('.row-cb').forEach(cb => {
      cb.onchange = () => toggleSelect(parseInt(cb.dataset.id), cb.checked);
    });
    const selAll = $('#select-all');
    if (selAll) selAll.onchange = () => toggleSelectAll(selAll.checked);

    const bulkPaidBtn = $('#bulk-paid-btn');
    if (bulkPaidBtn) bulkPaidBtn.onclick = markPaidSelected;
    const bulkExportBtn = $('#bulk-export-btn');
    if (bulkExportBtn) bulkExportBtn.onclick = exportSelected;
    const bulkDeleteBtn = $('#bulk-delete-btn');
    if (bulkDeleteBtn) bulkDeleteBtn.onclick = deleteSelected;
    const bulkClearBtn = $('#bulk-clear-btn');
    if (bulkClearBtn) bulkClearBtn.onclick = () => {
      selected.clear();
      $$('.row-cb').forEach(cb => { cb.checked = false; });
      if (selAll) selAll.checked = false;
      renderBulkBar();
    };

    // Empty-state upload button
    const emptyBtn = document.querySelector('.empty-state button');
    if (emptyBtn && !data.bills.length) emptyBtn.onclick = () => navigate('/bills/new');

    // Inline view/delete buttons
    $$('[data-view]').forEach(b => b.onclick = (e) => { e.stopPropagation(); navigate('/bills/' + b.dataset.view); });
    $$('[data-delete]').forEach(b => b.onclick = (e) => {
      e.stopPropagation();
      confirmDeleteBill(parseInt(b.dataset.delete), b.dataset.name);
    });

    // Inline payment status changes
    $$('.inline-pay').forEach(sel => {
      sel.onchange = async (e) => {
        const id = e.target.dataset.id;
        const val = e.target.value;
        try {
          await api(`/api/bills/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ payment_status: val }) });
          toast(`Bill #${id} marked as ${val}`, 'success');
        } catch (err) {
          toast("Update failed: " + err.message, 'error');
        }
        reload();
      };
    });

    // Inline total editing
    $$('.inline-edit').forEach(span => {
      span.title = 'Click to edit';
      span.onclick = async (e) => {
        const id = e.target.dataset.id;
        const field = e.target.dataset.field;
        const oldVal = e.target.dataset.value;
        const input = document.createElement('input');
        input.type = 'number';
        input.step = '0.01';
        input.value = oldVal;
        input.style.width = '90px';
        input.style.textAlign = 'right';
        input.className = 'input';
        e.target.replaceWith(input);
        input.focus();
        input.select();
        const save = async () => {
          const newVal = input.value;
          if (newVal === oldVal) { reload(); return; }
          try {
            await api(`/api/bills/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ [field]: parseFloat(newVal) }) });
            toast(`Updated bill #${id}`, 'success');
          } catch (err) {
            toast("Update failed: " + err.message, 'error');
          }
          reload();
        };
        input.onblur = save;
        input.onkeydown = (ev) => {
          if (ev.key === 'Enter') input.blur();
          if (ev.key === 'Escape') { input.value = oldVal; reload(); }
        };
      };
    });
  } // end of renderTable

  async function markPaidSelected() {
    if (selected.size === 0) return;
    const ids = [...selected];
    let ok = 0, fail = 0;
    for (const id of ids) {
      try {
        await apiPut(`/api/bills/${id}`, { payment_status: 'paid' });
        ok++;
      } catch { fail++; }
    }
    selected.clear();
    toast(`Marked ${ok} bill(s) as paid${fail ? `, ${fail} failed` : ''}`, fail ? 'warning' : 'success');
    reload();
  }

  async function exportSelected() {
    if (selected.size === 0) return;
    const ids = [...selected];
    toast(`Exporting ${ids.length} bills to Excel...`, 'info');
    // v8.5.4: use per-category sheet format + only export selected bills
    location.href = `/api/reports/billwise/export?start=2020-01-01&end=2030-12-31&status=all&bill_ids=${ids.join(',')}`;
  }

  async function deleteSelected() {
    if (selected.size === 0) return;
    const ids = [...selected];
    if (!confirm(`Delete ${ids.length} selected bill(s)? You can undo this.`)) return;
    let ok = 0, fail = 0;
    for (const id of ids) {
      try {
        await apiDelete(`/api/bills/${id}`);
        ok++;
      } catch { fail++; }
    }
    selected.clear();
    toast(`Deleted ${ok} bill(s)${fail ? `, ${fail} failed` : ''}`, fail ? 'warning' : 'success', {
      duration: 6000,
      action: {
        label: 'Undo',
        onClick: async () => {
          for (const id of ids) {
            try { await apiPost(`/api/bills/${id}/restore`, {}); } catch {}
          }
          toast(`Restored ${ids.length} bill(s)`, 'success');
          reload();
        },
      },
    });
    reload();
  }

  async function confirmDeleteBill(id, name) {
    if (!confirm(`Delete bill #${id} (${name})? You can undo this.`)) return;
    try {
      await apiDelete(`/api/bills/${id}`);
      toast(`Deleted bill #${id}`, 'success', {
        duration: 6000,
        action: {
          label: 'Undo',
          onClick: async () => {
            try {
              await apiPost(`/api/bills/${id}/restore`, {});
              toast(`Restored bill #${id}`, 'success');
              reload();
            } catch (e) {
              toast('Restore failed: ' + e.message, 'error');
            }
          },
        },
      });
      reload();
    } catch (e) {
      toast('Delete failed: ' + e.message, 'error');
    }
  }
});
