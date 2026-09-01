// POS Sales History — extracted from pos.js Phase 5
// v8.4: Added pagination + scrollable container + date filter
import { route } from '../../../router.js';
import { api, apiDelete } from '../../../api.js';
import { $, esc, fmt, fmtRs, fmtDate, icon, toast, skeletonCards, errorBox } from '../../../utils.js';
import { renderKioskBar, initKioskBar } from '../../../components/kiosk-bar.js';

// v8.4: Local helpers — these were defined in pos.js but not exported to this module
function fmtTime(ts) {
  if (!ts) return '';
  return ts.slice(11, 16);
}

function paymentBadge(method) {
  const m = {
    cash: 'badge-success',
    card: 'badge-accent',
    online: 'badge-accent',
    credit: 'badge-danger',
    split: 'badge-warning',
  };
  const labels = { cash: 'Cash', card: 'Card', online: 'Online', credit: 'Credit', split: 'Split' };
  return `<span class="badge ${m[method] || 'badge-success'}">${labels[method] || method}</span>`;
}

route('/pos/sales', async (el) => {
  let currentPage = 1;
  const pageSize = 25;

  el.innerHTML = `
    ${renderKioskBar('/pos/sales')}
    <div class="kiosk-content">
      <div class="topbar">
        <div class="topbar-title">
          <button class="btn btn-ghost btn-icon" onclick="location.hash='/pos'">${icon('arrowLeft', 16)}</button>
          <div><h1>Sales History</h1></div>
        </div>
        <div class="topbar-actions">
          <input class="input input-sm" id="sales-date-filter" type="date" value="${new Date().toISOString().slice(0, 10)}" style="width:150px" title="Filter by date">
          <button class="btn btn-ghost btn-sm" id="sales-clear-filter" title="Clear date filter" style="display:none">Clear</button>
          <button class="btn btn-secondary btn-sm" onclick="location.hash='/pos/quotes'">Quotes</button>
        </div>
      </div>
      <div class="card">
        <div id="sales-list">${skeletonCards(3)}</div>
        <div id="sales-pagination" style="padding:12px 16px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center"></div>
      </div>
    </div>`;
  initKioskBar();

  async function loadSales(page) {
    currentPage = page;
    const dateFilter = $('#sales-date-filter')?.value || '';
    const dateParam = dateFilter ? `&date=${dateFilter}` : '';
    $('#sales-list').innerHTML = skeletonCards(3);

    try {
      // v8.4: Use paginated API — returns {sales, total, page, page_size, pages_total}
      const data = await api(`/api/sales?page=${page}&page_size=${pageSize}${dateParam}`);
      // v8.19.1: backend clamps the page when the requested one no longer
      // exists (deleted the last page's sales / date filter shrank the
      // result) — follow it instead of sitting on an empty page.
      if (data && data.page && Number(data.page) !== Number(page)) {
        page = data.page;
      }
      currentPage = page;
      const sales = data.sales || data || [];
      const total = data.total || sales.length;
      const pagesTotal = data.pages_total || 1;

      if (sales.length === 0) {
        $('#sales-list').innerHTML = '<div class="empty-state"><h3>No sales found</h3><p class="text-dim text-sm">Try a different date or page.</p></div>';
        $('#sales-pagination').innerHTML = '';
        return;
      }

      $('#sales-list').innerHTML = `
        <div class="table-wrap" style="max-height:calc(100vh - 220px);overflow-y:auto">
          <table class="table-clickable">
            <thead style="position:sticky;top:0;z-index:10;background:var(--surface)">
              <tr><th>Invoice</th><th>Time</th><th>Date</th><th>Customer</th><th class="table-num">Total</th><th>Payment</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>${sales.map(s => `<tr onclick="location.hash='/pos/sale/${s.id}'">
              <td class="font-mono text-sm">${esc(s.invoice_no)}</td>
              <td class="text-sm">${fmtTime(s.created_at)}</td>
              <td class="text-sm">${fmtDate(s.created_at)}</td>
              <td>${esc(s.customer_name || 'Walk-in')}</td>
              <td class="table-num font-semibold">${fmtRs(s.total)}</td>
              <td>${paymentBadge(s.payment_method)}</td>
              <td><span class="badge ${s.payment_status==='paid'?'badge-success':s.payment_status==='credit'?'badge-danger':s.payment_status==='refunded'?'badge-warning':'badge-warning'}">${s.payment_status}</span></td>
              <td><button class="btn btn-ghost btn-sm btn-icon" onclick="event.stopPropagation();deleteSale(${s.id})">${icon('trash', 12)}</button></td>
            </tr>`).join('')}</tbody>
          </table>
        </div>`;

      // Pagination controls
      const startItem = (page - 1) * pageSize + 1;
      const endItem = Math.min(page * pageSize, total);
      $('#sales-pagination').innerHTML = `
        <div class="text-sm text-dim">
          Showing ${startItem}–${endItem} of ${total} sales
          ${dateFilter ? `· ${dateFilter}` : ''}
        </div>
        <div class="flex gap-2">
          <button class="btn btn-ghost btn-sm" id="prev-page" ${page <= 1 ? 'disabled' : ''}>← Prev</button>
          <span class="text-sm" style="line-height:28px">Page ${page} of ${pagesTotal}</span>
          <button class="btn btn-ghost btn-sm" id="next-page" ${page >= pagesTotal ? 'disabled' : ''}>Next →</button>
        </div>`;

      const prevBtn = $('#prev-page');
      const nextBtn = $('#next-page');
      if (prevBtn && !prevBtn.disabled) prevBtn.onclick = () => loadSales(page - 1);
      if (nextBtn && !nextBtn.disabled) nextBtn.onclick = () => loadSales(page + 1);
    } catch (e) {
      $('#sales-list').innerHTML = errorBox(e.message, "location.reload()");
    }
  }

  // Load first page
  await loadSales(1);

  // Date filter
  const dateInput = $('#sales-date-filter');
  const clearFilterBtn = $('#sales-clear-filter');
  if (dateInput) {
    dateInput.onchange = () => {
      if (clearFilterBtn) clearFilterBtn.style.display = dateInput.value ? '' : 'none';
      loadSales(1);
    };
  }
  if (clearFilterBtn) {
    clearFilterBtn.onclick = () => {
      dateInput.value = '';
      clearFilterBtn.style.display = 'none';
      loadSales(1);
    };
  }

  window.deleteSale = async (id) => {
    if (!confirm('Delete this sale?')) return;
    try { await apiDelete(`/api/sales/${id}`); toast('Deleted', 'success'); loadSales(currentPage); }
    catch (e) { toast('Error', 'error'); }
  };
});
