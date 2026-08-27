// More tools page — purchase orders, barcodes, CSV import, financial reports
import { route, navigate, reload } from '../router.js';
import { api, apiPost, apiPut, apiDelete } from '../api.js';
import { $, $$, esc, fmt, fmtRs, fmtDate, icon, toast, showLoading, hideLoading,
         openModal, closeModal, skeletonCards, errorBox } from '../utils.js';

// v8.4: SVG icons used by the /more page (the /pos-import route moved to
// pos-import-sync-page.js — this file no longer registers it).
const SVG = {
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
};

route('/more', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></div>
      <div>
        <h2 class="pos-page-header-title">More Tools</h2>
        <p class="pos-page-header-sub">Imports, settings shortcuts, and POS integration. Most reports moved to the Reports app.</p>
      </div>
    </div>

    <div class="grid grid-3 mt-4">
      <a href="#/pos-import" class="card more-card">
        <div class="more-card-icon chip-success"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg></div>
        <h3>Import from Existing POS</h3>
        <p class="text-dim text-sm">Upload daily backup from your shop's existing POS — sales flow into BillBook automatically</p>
      </a>
      <a href="#/purchase-orders" class="card more-card">
        <div class="more-card-icon chip-secondary"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/></svg></div>
        <h3>Purchase Orders</h3>
        <p class="text-dim text-sm">Generate POs for suppliers, send via WhatsApp (opens in Inventory app)</p>
      </a>
      <a href="#/barcodes" class="card more-card">
        <div class="more-card-icon chip-warning"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="4" y1="4" x2="4" y2="20"/><line x1="8" y1="4" x2="8" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/><line x1="16" y1="4" x2="16" y2="20"/><line x1="20" y1="4" x2="20" y2="20"/></svg></div>
        <h3>Barcodes & QR</h3>
        <p class="text-dim text-sm">Print barcodes for category buttons (opens in POS app)</p>
      </a>
      <a href="#/import" class="card more-card">
        <div class="more-card-icon chip-info"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></div>
        <h3>CSV Import (Customers/Suppliers)</h3>
        <p class="text-dim text-sm">Bulk import customers, suppliers, categories via CSV</p>
      </a>
      <a href="#/reports" class="card more-card">
        <div class="more-card-icon chip-danger"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg></div>
        <h3>Reports App</h3>
        <p class="text-dim text-sm">P&L, cash flow, balance sheet, top items, peak hours, targets, monthly close</p>
      </a>
      <a href="#/settings" class="card more-card">
        <div class="more-card-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></div>
        <h3>Tax & SMS</h3>
        <p class="text-dim text-sm">Configure GST, Twilio SMS notifications</p>
      </a>
      <a href="#/insights" class="card more-card">
        <div class="more-card-icon chip-pink"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg></div>
        <h3>AI Insights</h3>
        <p class="text-dim text-sm">Trends, reorder reminders, ABC analysis</p>
      </a>
    </div>`;
});

// ==================================================================
// Purchase Orders
// ==================================================================
// NOTE: /purchase-orders and /purchase-orders/{id} routes moved to inventory-pages.js
// (Inventory app shell — Phase 7)

// ==================================================================
// Barcodes
// ==================================================================
// NOTE: /barcodes route moved to pos-pages.js (POS app — scan-to-cart feature)

// ==================================================================
// CSV Import
// ==================================================================
route('/import', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
      </div>
      <div>
        <h2 class="pos-page-header-title">Import Data</h2>
        <p class="pos-page-header-sub">Bulk-import customers, suppliers, and price categories via CSV paste or file upload.</p>
      </div>
    </div>
    <div class="grid grid-2">
      <div class="card">
        <h3>Import Customers</h3>
        <p class="text-dim text-sm mb-3">CSV with columns: <code>name, phone, address</code></p>
        <textarea class="input" id="csv-customers" rows="8" placeholder="name,phone,address
Ali Khan,03001234567,Lahore
Fatima Bibi,03007654321,Karachi"></textarea>
        <button class="btn mt-2" data-import="customers">Import Customers</button>
        <div id="result-customers" class="mt-2"></div>
      </div>
      <div class="card">
        <h3>Import Suppliers</h3>
        <p class="text-dim text-sm mb-3">CSV with columns: <code>name, phone, address, notes</code></p>
        <textarea class="input" id="csv-suppliers" rows="8" placeholder="name,phone,address,notes
ABC Trading,0423555666,Lahore,Wholesale toys
XYZ Imports,0213555777,Karachi,Cosmetics"></textarea>
        <button class="btn mt-2" data-import="suppliers">Import Suppliers</button>
        <div id="result-suppliers" class="mt-2"></div>
      </div>
      <div class="card">
        <h3>Import Price Categories</h3>
        <p class="text-dim text-sm mb-3">CSV with columns: <code>name, sell_price, color</code></p>
        <textarea class="input" id="csv-categories" rows="8" placeholder="name,sell_price,color
Budget,250,#3b82f6
Standard,500,#10b981"></textarea>
        <button class="btn mt-2" data-import="categories">Import Categories</button>
        <div id="result-categories" class="mt-2"></div>
      </div>
      <div class="card">
        <h3>Or upload a file</h3>
        <p class="text-dim text-sm mb-3">Upload a .csv file to import.</p>
        <input class="input" type="file" id="csv-file" accept=".csv">
        <select class="select mt-2" id="file-type">
          <option value="customers">Customers</option>
          <option value="suppliers">Suppliers</option>
          <option value="categories">Price Categories</option>
        </select>
        <button class="btn mt-2" id="csv-upload-btn">Upload &amp; Import</button>
        <div id="result-file" class="mt-2"></div>
      </div>
    </div>`;

  async function doImport(type) {
    const text = $(`#csv-${type}`).value.trim();
    if (!text) { toast('Paste CSV data first', 'error'); return; }
    try {
      const r = await apiPost('/api/import/csv', { csv_text: text, type });
      $(`#result-${type}`).innerHTML = `
        <div class="alert alert-success text-sm mt-2">
          Imported ${r.imported} ${type}${r.skipped ? `, skipped ${r.skipped} (already exist)` : ''}
          ${r.errors?.length ? `<br>${r.errors.length} errors: ${r.errors.slice(0, 3).join('; ')}` : ''}
        </div>`;
      toast(`Imported ${r.imported} ${type}`, 'success');
    } catch (e) {
      $(`#result-${type}`).innerHTML = `<div class="alert alert-danger text-sm mt-2">${esc(e.message)}</div>`;
    }
  }

  $$('[data-import]').forEach(b => b.onclick = () => doImport(b.dataset.import));
  $('#csv-upload-btn').onclick = async () => {
    const fileInput = $('#csv-file');
    if (!fileInput.files.length) { toast('Choose a file', 'error'); return; }
    const text = await fileInput.files[0].text();
    const type = $('#file-type').value;
    try {
      const r = await apiPost('/api/import/csv', { csv_text: text, type });
      $('#result-file').innerHTML = `
        <div class="alert alert-success text-sm mt-2">
          Imported ${r.imported} ${type}${r.skipped ? `, skipped ${r.skipped}` : ''}
          ${r.errors?.length ? `<br>${r.errors.length} errors` : ''}
        </div>`;
      toast(`Imported ${r.imported} ${type}`, 'success');
    } catch (e) {
      $('#result-file').innerHTML = `<div class="alert alert-danger text-sm mt-2">${esc(e.message)}</div>`;
    }
  };
});

// NOTE: /reports/financial moved to reports-pages.js (Phase 9 — Reports app)

// ==================================================================
// External POS Backup Import
// ==================================================================
// v8.4: The /pos-import route is now registered in pos-import-sync-page.js
// which has the proper Ezi POS ZIP upload + dbfread integration.
// The old route handler below was removed to prevent the "SVG is not defined"
// error caused by this file not having an SVG constant in scope.

// NOTE: /reports/sales-analytics moved to reports-pages.js (Phase 9 — Reports app)
