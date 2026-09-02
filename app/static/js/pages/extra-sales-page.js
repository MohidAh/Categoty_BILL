// v8.18.13 — Extra Sales page (Billing app)
// Sales made OUTSIDE the POS of non-stock items (cardboard cartons, scrap/
// raddi, empty drums, packing material sold on...). Pure other income:
// no stock movement, no COGS — flows into Actual Earnings, P&L (other
// income), Cash Flow and the daily summary.
import { route } from '../router.js';
import { api, apiPost, apiDelete } from '../api.js';
import { $, $$, esc, fmtRs, fmtDate, toast, openModal, closeModal,
         skeletonCards, errorBox } from '../utils.js';
import { initListState } from '../list-state.js';

const SVG = {
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
  box: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>',
  recycle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
};

function statCard(label, value, chipClass, svgIcon, sub = '') {
  return `<div class="stat-card">
    <div class="stat-card-icon ${chipClass}">${svgIcon}</div>
    <div class="stat-card-label">${esc(label)}</div>
    <div class="stat-card-value">${value}</div>
    ${sub ? `<div class="stat-card-delta">${sub}</div>` : ''}
  </div>`;
}

function methodChip(m) {
  const cls = m === 'cash' ? 'chip-success' : m === 'bank' ? 'chip-info' : 'chip-secondary';
  return `<span class="chip ${cls} chip-sm">${esc(m)}</span>`;
}

route('/bills/extra-sales', async (el, path, q) => {
  // month + search persist across navigation
  const st = initListState('extraSales', q, { month: '', q: '' });
  st.syncUrlIfRestored();
  const thisMonth = st.val('month') || new Date().toISOString().slice(0, 7);
  st.replace({ month: thisMonth });
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-success">${SVG.recycle}</div>
      <div>
        <h2 class="pos-page-header-title">Extra Sales</h2>
        <p class="pos-page-header-sub">Money from things sold outside the POS — cartons, raddi/scrap, drums. Not stock products: no COGS, counted as other income.</p>
      </div>
      <div class="pos-page-header-actions">
        <div class="pos-date-input">
          <span class="pos-date-input-icon">${SVG.calendar}</span>
          <input class="input input-sm" id="xs-month" type="month" value="${thisMonth}">
        </div>
        <button class="btn btn-secondary btn-sm" id="xs-export-pdf" title="Download this month's Extra Sales report as PDF">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
          PDF
        </button>
        <button class="btn btn-secondary btn-sm" id="xs-export-excel" title="Download this month's Extra Sales report as Excel">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.download}</span>
          Excel
        </button>
        <button class="btn btn-primary btn-sm" id="xs-add-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          Add Extra Sale
        </button>
      </div>
    </div>

    <div id="xs-stats">${skeletonCards(3)}</div>

    <div class="card mt-4">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <h3>Entries — <span id="xs-month-label">${thisMonth}</span></h3>
        <div style="display:flex;gap:8px;align-items:center">
          <div style="position:relative">
            <input class="input input-sm" id="xs-search" placeholder="Search items..." value="${esc(st.val('q') || '')}" style="width:200px;padding-left:30px">
            <span style="position:absolute;left:9px;top:9px;width:14px;height:14px;color:var(--text-dim)">${SVG.search}</span>
          </div>
        </div>
      </div>
      <div id="xs-table" class="mt-3">${skeletonCards(2)}</div>
    </div>

    <div class="card mt-4" id="xs-top-card" style="display:none">
      <h3>Top Items This Month</h3>
      <div id="xs-top" class="mt-3"></div>
    </div>`;

  $('#xs-month').onchange = () => { st.replace({ month: $('#xs-month').value }); loadAll(); };
  $('#xs-add-btn').onclick = () => openAddModal();
  // v8.18.14: month-scoped PDF/Excel exports via the universal report route
  // (report name 'extra-sales' → shop.get_extra_sales_report(month))
  $('#xs-export-pdf').onclick = () => {
    const m = $('#xs-month').value || thisMonth;
    window.open(`/api/reports/extra-sales/export?format=pdf&month=${encodeURIComponent(m)}`, '_blank');
  };
  $('#xs-export-excel').onclick = () => {
    const m = $('#xs-month').value || thisMonth;
    window.open(`/api/reports/extra-sales/export?format=excel&month=${encodeURIComponent(m)}`, '_blank');
  };
  let _searchTimer = null;
  $('#xs-search').oninput = () => {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => { st.replace({ q: $('#xs-search').value }); loadTable(); }, 250);
  };

  await loadAll();

  async function loadAll() {
    $('#xs-month-label').textContent = $('#xs-month').value;
    await Promise.all([loadSummary(), loadTable()]);
  }

  async function loadSummary() {
    const month = $('#xs-month').value;
    try {
      const s = await api(`/api/extra-sales/summary?month=${month}`);
      const delta = s.delta_pct;
      $('#xs-stats').innerHTML = `
        <div class="grid grid-3">
          ${statCard('Extra Sales Income', fmtRs(s.month_total), 'chip-success', SVG.recycle,
            s.last_month_total > 0 ? `${delta >= 0 ? '▲' : '▼'} ${Math.abs(delta)}% vs ${s.last_month}` : 'this month')}
          ${statCard('Entries', s.entries, 'chip-warning', SVG.box, `${s.total_qty.toLocaleString()} qty sold`)}
          ${statCard('Last Month', fmtRs(s.last_month_total), 'chip-info', SVG.calendar, s.last_month)}
        </div>`;
      // Top items card
      if (s.by_item && s.by_item.length > 0) {
        $('#xs-top-card').style.display = '';
        $('#xs-top').innerHTML = `
          <div style="overflow-x:auto">
          <table class="table">
            <thead><tr><th>Item</th><th style="text-align:right">Times Sold</th><th style="text-align:right">Qty</th><th style="text-align:right">Total</th></tr></thead>
            <tbody>${s.by_item.map(t => `
              <tr>
                <td class="font-semibold">${esc(t.item_name)}</td>
                <td style="text-align:right">${t.times}</td>
                <td style="text-align:right">${t.qty.toLocaleString()}</td>
                <td style="text-align:right;font-weight:600" class="text-success">${fmtRs(t.total)}</td>
              </tr>`).join('')}</tbody>
          </table>
          </div>`;
      } else {
        $('#xs-top-card').style.display = 'none';
      }
    } catch (e) {
      $('#xs-stats').innerHTML = errorBox(e.message);
    }
  }

  async function loadTable() {
    const month = $('#xs-month').value;
    const q = $('#xs-search').value.trim();
    let url = `/api/extra-sales?month=${month}&limit=200`;
    if (q) url += `&q=${encodeURIComponent(q)}`;
    try {
      const r = await api(url);
      const rows = r.extra_sales || [];
      if (rows.length === 0) {
        $('#xs-table').innerHTML = `
          <div class="text-center text-dim" style="padding:32px">
            <p style="font-weight:600;margin-bottom:4px">No extra sales recorded for ${esc(month)}</p>
            <p class="text-sm">Sold cartons, raddi/scrap, drums or anything that isn't a stock product? Click "Add Extra Sale" — the money counts as other income in your earnings.</p>
          </div>`;
        return;
      }
      $('#xs-table').innerHTML = `
        <div style="overflow-x:auto">
        <table class="table">
          <thead>
            <tr>
              <th>Date</th><th>Item</th><th class="text-sm text-dim">Note</th>
              <th style="text-align:right">Qty</th><th style="text-align:right">Unit Price</th>
              <th style="text-align:right">Total</th><th>Method</th><th></th>
            </tr>
          </thead>
          <tbody>
            ${rows.map(s => `
              <tr>
                <td class="text-sm">${esc(fmtDate(s.sale_date))}</td>
                <td class="font-semibold">${esc(s.item_name)}</td>
                <td class="text-sm text-dim">${esc(s.description || '')}</td>
                <td style="text-align:right">${s.quantity}</td>
                <td style="text-align:right">${fmtRs(s.unit_price)}</td>
                <td style="text-align:right;font-weight:600" class="text-success">${fmtRs(s.total)}</td>
                <td>${methodChip(s.payment_method)}</td>
                <td><button class="btn-icon btn-icon-danger" data-xs-del="${s.id}" title="Delete">
                  <span style="display:inline-flex;width:14px;height:14px">${SVG.trash}</span>
                </button></td>
              </tr>`).join('')}
          </tbody>
        </table>
        </div>`;
      document.querySelectorAll('[data-xs-del]').forEach(btn => {
        btn.onclick = async () => {
          const id = btn.getAttribute('data-xs-del');
          if (!confirm('Delete this extra sale? Its cash drawer entry is removed too.')) return;
          try {
            await apiDelete(`/api/extra-sales/${id}`);
            toast('Extra sale deleted', 'success');
            await loadAll();
          } catch (e) {
            toast('Delete failed: ' + e.message, 'error');
          }
        };
      });
    } catch (e) {
      $('#xs-table').innerHTML = errorBox(e.message);
    }
  }

  function openAddModal() {
    const today = new Date().toISOString().slice(0, 10);
    openModal(
      'Add Extra Sale',
      `
      <p class="text-dim text-sm" style="margin-bottom:12px">
        For things sold outside the POS that are <strong>not</strong> stock products — cartons, raddi (scrap), drums, packing material. No stock is reduced; the amount counts as other income.
      </p>
      <div class="form-group">
        <label class="form-label">What did you sell?</label>
        <input class="input" id="xs-item" placeholder="e.g. Cardboard cartons, Raddi (scrap), Empty drums" autofocus>
      </div>
      <div class="grid grid-2">
        <div class="form-group">
          <label class="form-label">Quantity</label>
          <input class="input" id="xs-qty" type="number" min="0.01" step="any" value="1">
        </div>
        <div class="form-group">
          <label class="form-label">Unit Price (Rs)</label>
          <input class="input" id="xs-price" type="number" min="0" step="any" placeholder="0">
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Total: <strong id="xs-total-preview" style="color:var(--success-text)">Rs 0</strong></label>
      </div>
      <div class="grid grid-2">
        <div class="form-group">
          <label class="form-label">Payment Method</label>
          <select class="input" id="xs-method">
            <option value="cash">Cash</option>
            <option value="bank">Bank</option>
            <option value="card">Card</option>
            <option value="online">Online</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Date</label>
          <input class="input" id="xs-date" type="date" value="${today}">
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Note (optional)</label>
        <input class="input" id="xs-desc" placeholder="e.g. sold to Khalid scrap dealer">
      </div>
      <p class="text-dim text-sm" style="margin-top:8px">
        Cash entries show up in the Cash Drawer; income appears in Actual Earnings, P&L and Cash Flow for this month.
      </p>
      `,
      `<button class="btn" data-close>Cancel</button>
       <button class="btn btn-primary" id="xs-save-btn"><span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span> Save Extra Sale</button>`,
    );
    const preview = () => {
      const t = (parseFloat($('#xs-qty').value) || 0) * (parseFloat($('#xs-price').value) || 0);
      $('#xs-total-preview').textContent = 'Rs ' + t.toLocaleString(undefined, { maximumFractionDigits: 2 });
    };
    $('#xs-qty').oninput = preview;
    $('#xs-price').oninput = preview;
    $('#xs-save-btn').onclick = async () => {
      const item_name = $('#xs-item').value.trim();
      const quantity = parseFloat($('#xs-qty').value);
      const unit_price = parseFloat($('#xs-price').value);
      if (!item_name) { toast('Enter what you sold', 'error'); return; }
      if (!quantity || quantity <= 0) { toast('Enter a valid quantity', 'error'); return; }
      if (isNaN(unit_price) || unit_price < 0) { toast('Enter a valid unit price', 'error'); return; }
      if (!(quantity * unit_price > 0)) { toast('Total must be more than 0', 'error'); return; }
      try {
        await apiPost('/api/extra-sales', {
          item_name, quantity, unit_price,
          description: $('#xs-desc').value.trim(),
          payment_method: $('#xs-method').value,
          date: $('#xs-date').value,
        });
        toast('Extra sale added', 'success');
        closeModal();
        await loadAll();
      } catch (e) {
        toast('Save failed: ' + e.message, 'error');
      }
    };
  }
});
