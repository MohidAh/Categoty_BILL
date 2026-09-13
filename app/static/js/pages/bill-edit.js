// Bill detail / edit page — enhanced with profit calc, full manual entry
import { route, navigate } from '../router.js';
import { api, apiPost, apiDelete, apiUpload } from '../api.js';
import { $, $$, toast, showLoading, hideLoading,
         esc, flagText, fmt, fmtRs, fmtDate, fmtPct, fmtDecimalPct, icon, iconHtml, openModal, closeModal } from '../utils.js';

route('/bills/', async (el, path) => {
  const id = path.split('/').pop();
  // v8.18.11 fix: a bare /bills/ (empty id) previously fell through to
  // /api/bills/ which redirect-followed to the LIST endpoint (200), so the
  // page rendered a garbage "Bill #undefined" header. Guard it instead.
  if (!id || !/^\d+$/.test(id)) {
    el.innerHTML = `<div class="empty-state"><div class="empty-state-icon">${icon('alert')}</div><h3>Bill not found</h3><p>No bill id in the URL.</p><button class="btn" onclick="location.hash='/bills'">Back to Bills</button></div>`;
    return;
  }
  let b, categories;
  try {
    [b, categories] = await Promise.all([
      api(`/api/bills/${id}`),
      api('/api/categories'),
    ]);
  } catch (e) {
    el.innerHTML = `<div class="empty-state"><div class="empty-state-icon">${icon('alert')}</div><h3>Bill not found</h3><p>${esc(e.message)}</p><button class="btn" onclick="location.hash='/bills'">Back to Bills</button></div>`;
    return;
  }

  const activeCats = categories.filter(c => c.active);
  // Build a lookup so we can compute sell price per row
  const catMap = new Map(activeCats.map(c => [c.id, c]));

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-success">${iconHtml('bills')}</div>
      <div>
        <h2 class="pos-page-header-title">Bill #${b.id}</h2>
        <p class="pos-page-header-sub">${b.provider ? `Extracted via ${esc(b.provider)}` : 'Manual entry'} &middot; Created ${fmtDate(b.created_at)}${b.status === 'confirmed' ? ' &middot; <strong style="color:var(--success-text)">Confirmed</strong>' : ' &middot; <strong style="color:var(--warning-text)">Review</strong>'}</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-secondary" id="back-btn" title="Back to Bills list (Esc)">${icon('arrowLeft', 14)} Back</button>
        <button class="btn btn-secondary" id="add-pages-btn">${icon('image', 14)} Add Images</button>
        <button class="btn btn-secondary" id="duplicate-btn" title="Create a new bill with the same items">${icon('plus', 14)} Create Similar</button>
        <button class="btn" id="save-btn">${icon('save', 14)} Save &amp; Confirm</button>
        <button class="btn btn-danger" id="delete-btn">${icon('trash', 14)}</button>
      </div>
    </div>

    ${b.duplicate ? `<div class="alert alert-warning mb-4">${iconHtml('alert', 'alert-icon')}<div><strong>Possible duplicate</strong> — bill #${b.duplicate.id} (${esc(b.duplicate.supplier_name || '')}, ${fmtDate(b.duplicate.bill_date)}) has the same supplier and date.</div></div>` : ''}

    ${b.flags.length ? b.flags.map(f => `<div class="alert alert-warning mb-2">${iconHtml('alert', 'alert-icon')}<div>${esc(flagText(f))}</div></div>`).join('') : (b.status === 'confirmed' ? `<div class="alert alert-success mb-2">${iconHtml('check', 'alert-icon')}<div>All checks passed — bill is confirmed.</div></div>` : '')}

    <div class="bill-edit" id="bill-edit-grid">
      <div class="bill-images-panel">
        ${b.pages.length ? `
          <div class="bill-images-toolbar">
            <button class="btn btn-ghost btn-sm btn-icon" id="img-zoom-out" title="Zoom out (−)">−</button>
            <span class="bill-images-zoom" id="img-zoom-label">100%</span>
            <button class="btn btn-ghost btn-sm btn-icon" id="img-zoom-in" title="Zoom in (+)">+</button>
            <button class="btn btn-ghost btn-sm btn-icon" id="img-rotate-left" title="Rotate left">↺</button>
            <button class="btn btn-ghost btn-sm btn-icon" id="img-rotate-right" title="Rotate right">↻</button>
            <button class="btn btn-ghost btn-sm" id="img-reset" title="Reset zoom & rotation">Fit</button>
            <button class="btn btn-ghost btn-sm btn-icon" id="img-pos-btn" title="Change image panel position">⇄</button>
            <span class="bill-images-count" title="Pinch, Ctrl+scroll or double-click an image to zoom — drag / scroll to pan">${b.pages.length} page${b.pages.length > 1 ? 's' : ''} · pinch/scroll to zoom</span>
          </div>
          <div class="bill-images" id="bill-images-container">
            ${b.pages.map(p => `<img src="/pages/${esc(p.filename)}" alt="Bill page ${p.page_no}" class="bill-img" draggable="false">`).join('')}
          </div>` :
          `<div class="bill-images-empty">${icon('image', 28)}<p>No images uploaded yet.<br>Click "Add Images" to attach bill photos.</p></div>`}
      </div>
      <div class="bill-resize-handle" id="bill-resize-handle" role="separator" aria-orientation="vertical" tabindex="0" title="Drag to resize the image panel (wider images = narrower table). Double-click to reset layout."><span class="bill-resize-grip"></span></div>
      <div class="bill-form-section">
        <div class="card">
          <h3>Bill Details</h3>
          <div class="grid grid-2 mt-4">
            <div><label>Supplier Name</label><input class="input" id="f_supplier" value="${esc(b.supplier_name || '')}" list="supplier-list"></div>
            <datalist id="supplier-list">
              ${activeCats.length ? '' : ''}
            </datalist>
            <div><label>Phone</label><input class="input" id="f_phone" value="${esc(b.phone || '')}" placeholder="03001234567"></div>
            <div><label>Bill Date</label><input class="input" id="f_date" type="date" value="${esc(b.bill_date ? b.bill_date.slice(0, 10) : '')}"></div>
            <div><label>Bill No</label><input class="input" id="f_billno" value="${esc(b.bill_no || '')}"></div>
            <div><label>Written Total (Rs)</label><input class="input" id="f_total" type="number" step="0.01" value="${b.written_total ?? ''}"></div>
            <div><label>Payment Status</label>
              <select class="select" id="f_payment">
                <option value="paid" ${b.payment_status === 'paid' ? 'selected' : ''}>Paid</option>
                <option value="credit" ${b.payment_status === 'credit' ? 'selected' : ''}>Credit (Urdhaar)</option>
              </select>
            </div>
            <div id="due-row" style="${b.payment_status === 'credit' ? '' : 'display:none'}">
              <label>Credit Due Date</label>
              <input class="input" id="f_due" type="date" value="${esc(b.credit_due_date ? b.credit_due_date.slice(0, 10) : '')}">
            </div>
          </div>
          <div id="computed-info" class="help-text"></div>
        </div>

        <div class="card">
          <div class="card-title">
            <h3>Items</h3>
            <div class="flex gap-2 items-center">
              <span id="verify-progress" class="text-xs text-dim" style="margin-right:4px"></span>
              <button class="btn btn-ghost btn-sm" id="verify-all-btn" title="Mark all as verified">All</button>
              <button class="btn btn-ghost btn-sm" id="toggle-cols-btn" title="Toggle extra columns">${icon('settings', 12)} Columns</button>
              <button class="btn btn-secondary btn-sm" id="add-5-rows-btn">${icon('plus', 12)} Add 5</button>
              <button class="btn btn-secondary btn-sm" id="add-row-btn">${icon('plus', 12)} Add Row</button>
            </div>
          </div>

          <!-- Summary badges + search/filter row -->
          <div class="items-toolbar">
            <div class="items-summary" id="items-summary"></div>
            <div class="search-input items-search">
              ${icon('search', 13)}
              <input class="input" id="items-filter" placeholder="Filter items by name or code..." style="height:28px;font-size:12px">
            </div>
            <button class="btn btn-ghost btn-sm" id="review-mode-btn" title="Show only items that need review">${icon('alert', 12)} Review Mode</button>
            <button class="btn btn-ghost btn-sm" id="next-unverified-btn" title="Jump to next unverified item (V)">↓ Next</button>
          </div>

          <div class="table-wrap bill-items-table">
            <table id="items-table">
              <thead>
                <tr>
                  <th style="width:28px"><input type="checkbox" id="verify-all-cb" title="Mark all verified"></th>
                  <th style="width:34px">Sr</th>
                  <th class="col-required sortable" data-sort="raw">Item Name ↕</th>
                  <th class="col-extra col-code sortable" data-sort="code">Code ↕</th>
                  <th class="col-required table-num sortable" data-sort="qty">Qty ↕</th>
                  <th class="col-required">Unit</th>
                  <th class="col-required table-num sortable" data-sort="price">Unit Cost ↕</th>
                  <th class="col-extra col-pieces">Pieces</th>
                  <th class="col-extra col-category">Category</th>
                  <th class="col-required table-num">Sell Price</th>
                  <th class="col-required table-num">Line Total</th>
                  <th class="col-required sortable" data-sort="profit">Profit ↕</th>
                  <th></th>
                </tr>
              </thead>
              <tbody id="items"></tbody>
              <tfoot>
                <tr>
                  <td></td>
                  <td class="col-required">Totals</td>
                  <td class="col-required"></td>
                  <td class="col-extra col-code"></td>
                  <td class="col-required"></td>
                  <td class="col-required"></td>
                  <td class="col-required"></td>
                  <td class="col-extra col-pieces table-num" id="ft-pieces">0</td>
                  <td class="col-extra col-category"></td>
                  <td class="col-required"></td>
                  <td class="col-required table-num" id="ft-cost">Rs 0</td>
                  <td class="col-required table-num profit-cell">
                    <span class="profit-value" id="ft-profit">Rs 0</span>
                    <span class="profit-margin" id="ft-margin"></span>
                  </td>
                  <td></td>
                </tr>
              </tfoot>
            </table>
          </div>
          <div class="flex justify-between items-center mt-4">
            <span class="text-sm text-dim" id="item-count"></span>
            <span class="font-bold" id="grand-total"></span>
          </div>
          <div class="mt-4 p-3" style="background:var(--bg-elevated);border-radius:var(--radius-sm);font-size:12px;color:var(--text-secondary)">
            <strong>Tip:</strong> Set a category to auto-fill the sell price. Profit is calculated as <code>(sell - cost) × pieces</code>. Margin colors: <span class="text-success">≥30%</span>, <span class="text-warning">20-30%</span>, <span class="text-danger">&lt;20%</span>.
          </div>
        </div>
      </div>
    </div>`;

  const itemsBody = $('#items');

  // Define recalc functions locally so they're available immediately
  function recalcRow(el) {
    const tr = el.closest('tr');
    if (!tr) return;
    const price = parseFloat(tr.querySelector('.i-price').value) || 0;
    const qty = parseFloat(tr.querySelector('.i-qty').value) || 0;
    const unit = tr.querySelector('.i-unit').value;
    const sell = parseFloat(tr.querySelector('.i-sell').value) || 0;
    const p = unit === 'dozen' ? qty * 12 : qty;
    const cost = price * p;
    const revenue = sell * p;
    const profit = revenue - cost;
    const margin = revenue > 0 ? profit / revenue : 0;

    tr.querySelector('.i-pieces').textContent = fmt(p);
    tr.querySelector('.i-lt').textContent = fmt(parseFloat(cost.toFixed(6)));

    const profitCell = tr.querySelector('.profit-cell');
    const marginClass = margin >= 0.3 ? 'text-success' : margin >= 0.2 ? 'text-warning' : 'text-danger';
    profitCell.innerHTML = `<span class="profit-value ${marginClass}">${fmtRs(parseFloat(profit.toFixed(6)))}</span>
      <span class="profit-margin ${marginClass}">${revenue > 0 ? fmtDecimalPct(margin, 0) : '—'}</span>`;
    recalcGrand();
  }

  function recalcGrand() {
    let totalCost = 0, totalPieces = 0, totalProfit = 0, totalRevenue = 0;
    $$('.item-row').forEach(tr => {
      const price = parseFloat(tr.querySelector('.i-price').value) || 0;
      const qty = parseFloat(tr.querySelector('.i-qty').value) || 0;
      const unit = tr.querySelector('.i-unit').value;
      const sell = parseFloat(tr.querySelector('.i-sell').value) || 0;
      const p = unit === 'dozen' ? qty * 12 : qty;
      totalPieces += p;
      totalCost += price * p;
      totalRevenue += sell * p;
      totalProfit += (sell - price) * p;
    });
    const count = $$('.item-row').length;
    $('#item-count').textContent = `${count} item${count !== 1 ? 's' : ''}`;
    $('#ft-pieces').textContent = fmt(totalPieces);
    $('#ft-cost').textContent = fmtRs(totalCost);

    // v8.18.5: footer shows TOTAL margin alongside total profit — same
    // two-line layout and color thresholds as the per-item Profit column
    // (≥30% green, 20-30% amber, <20% red). Margin = totalProfit /
    // totalRevenue; shows '—' while no sell prices are entered.
    const totalMargin = totalRevenue > 0 ? totalProfit / totalRevenue : null;
    const marginClass = totalMargin == null ? '' :
      totalMargin >= 0.3 ? 'text-success' : totalMargin >= 0.2 ? 'text-warning' : 'text-danger';
    const profitEl = $('#ft-profit');
    profitEl.textContent = fmtRs(parseFloat(totalProfit.toFixed(6)));
    profitEl.className = `profit-value ${totalProfit >= 0 ? 'text-success' : 'text-danger'}`;
    const marginEl = $('#ft-margin');
    marginEl.textContent = totalMargin == null ? '—' : fmtDecimalPct(totalMargin, 0);
    marginEl.className = `profit-margin ${marginClass}`;

    const writtenEl = $('#f_total');
    const written = parseFloat(writtenEl.value) || 0;
    const totalEl = $('#grand-total');
    totalEl.textContent = 'Computed: ' + fmtRs(totalCost);
    if (written > 0 && Math.abs(totalCost - written) > Math.max(1, written * 0.01)) {
      totalEl.innerHTML += ` <span class="text-danger">mismatch (written ${fmtRs(written)})</span>`;
    }
  }

  function onCategoryChange(el) {
    const tr = el.closest('tr');
    const catId = parseInt(el.value);
    const cat = catMap.get(catId);
    if (cat) {
      tr.querySelector('.i-sell').value = cat.sell_price;
    }
    recalcRow(el);
  }

  function renderRow(it = {}, opts = {}) {
    const tr = document.createElement('tr');
    tr.className = 'item-row';
    tr.dataset.page = it.page_no || '';
    if (opts.splitChild) {
      tr.dataset.splitChild = '1';
      if (opts.parentSr) tr.dataset.parentSr = String(opts.parentSr);
    }
    const catId = it.category_id || '';
    const conf = it.confidence;
    // Confidence-based row class
    if (conf !== null && conf !== undefined && conf < 0.9) {
      tr.classList.add('low-confidence');
      if (conf < 0.7) tr.classList.add('very-low-confidence');
    }
    // Confidence indicator
    let confHtml = '';
    if (conf !== null && conf !== undefined) {
      const confColor = conf >= 0.9 ? 'text-success' : conf >= 0.7 ? 'text-warning' : 'text-danger';
      const confPct = Math.round(conf * 100);
      confHtml = `<span class="conf-dot ${confColor}" title="AI confidence: ${confPct}%">●</span>`;
    }
    tr.innerHTML = `
      <td style="text-align:center"><input type="checkbox" class="i-verified" title="Mark as verified"></td>
      <td class="i-sr text-dim"></td>
      <td class="col-required">
        <div class="flex items-center gap-1">
          ${confHtml}
          <input class="input i-raw" value="${esc(it.raw || '')}" style="width:140px" placeholder="Item name">
        </div>
      </td>
      <td class="col-extra col-code"><input class="input i-code" value="${esc(it.item_code || '')}" style="width:80px" placeholder="SKU"></td>
      <td class="col-required table-num"><input class="input i-qty" type="number" step="0.01" value="${it.qty ?? 0}" style="width:60px;text-align:right" placeholder="0"></td>
      <td class="col-required"><select class="select i-unit" style="width:75px">
        <option value="pcs" ${it.unit === 'pcs' ? 'selected' : ''}>pcs</option>
        <option value="dozen" ${it.unit === 'dozen' ? 'selected' : ''}>dozen</option>
      </select></td>
      <td class="col-required table-num"><input class="input i-price" type="number" step="0.01" value="${it.price ?? 0}" style="width:85px;text-align:right" placeholder="0"></td>
      <td class="col-extra col-pieces table-num i-pieces text-dim">0</td>
      <td class="col-extra col-category"><select class="select i-cat" style="width:120px">
        <option value="">—</option>
        ${activeCats.map(c => `<option value="${c.id}" ${catId === c.id ? 'selected' : ''}>${esc(c.name)} (${fmt(c.sell_price)})</option>`).join('')}
      </select></td>
      <td class="col-required table-num"><input class="input i-sell" type="number" step="0.01" value="${catMap.get(catId)?.sell_price ?? ''}" style="width:80px;text-align:right" placeholder="0"></td>
      <td class="col-required table-num i-lt font-semibold">0</td>
      <td class="col-required profit-cell"><span class="profit-value">—</span><span class="profit-margin"></span></td>
      <td><button class="btn btn-ghost btn-sm btn-icon" data-action="split" title="Split this item across multiple categories">${icon('edit', 12)}</button><button class="btn btn-ghost btn-sm btn-icon" data-action="remove" title="Remove this row">${icon('x', 12)}</button></td>`;

    // Bind events directly (no globals)
    tr.querySelector('.i-price').addEventListener('input', () => recalcRow(tr.querySelector('.i-price')));
    tr.querySelector('.i-qty').addEventListener('input', () => recalcRow(tr.querySelector('.i-qty')));
    tr.querySelector('.i-sell').addEventListener('input', () => recalcRow(tr.querySelector('.i-sell')));
    tr.querySelector('.i-unit').addEventListener('change', () => recalcRow(tr.querySelector('.i-unit')));
    tr.querySelector('.i-cat').addEventListener('change', () => onCategoryChange(tr.querySelector('.i-cat')));
    tr.querySelector('[data-action="remove"]').addEventListener('click', () => { tr.remove(); recalcGrand(); updateSerialNumbers(); });
    // v8.5.4: Split button — opens a dialog to split this row across multiple categories
    tr.querySelector('[data-action="split"]').addEventListener('click', () => {
      const rawEl = tr.querySelector('.i-raw');
      const priceEl = tr.querySelector('.i-price');
      const qtyEl = tr.querySelector('.i-qty');
      const raw = rawEl ? rawEl.value : '';
      const price = parseFloat(priceEl ? priceEl.value : 0) || 0;
      const qty = parseFloat(qtyEl ? qtyEl.value : 0) || 1;
      const totalCost = price * qty;

      openModal(
        `Split Item`,
        `<p>Split <strong>${esc(raw.slice(0, 50))}</strong> (cost Rs ${fmtRs(totalCost)}) across multiple categories.</p>
         <p class="text-dim text-sm">Enter categories as letters (A, B, C, D) or prices (250, 500, 750, 1000), separated by commas. The cost will be distributed proportionally by sell price.</p>
         <div class="mt-3">
           <label>Categories</label>
           <input class="input" id="split-cats" placeholder="e.g. A,B,C or 250,500,750" autofocus>
         </div>
         <div class="mt-3" id="split-preview"></div>`,
        `<button class="btn btn-secondary" data-modal-close>Cancel</button>
         <button class="btn" id="split-confirm-btn">Split</button>`
      );

      // Parse input on change + show preview
      const splitInput = $('#split-cats');
      const previewEl = $('#split-preview');
      function updatePreview() {
        const input = splitInput.value.trim();
        const cats = input.split(/[,;\s]+/).map(s => s.trim().toUpperCase()).filter(s => s);
        const prices = cats.map(c => {
          if (c === 'A') return 250;
          if (c === 'B') return 500;
          if (c === 'C') return 750;
          if (c === 'D') return 1000;
          const n = parseInt(c);
          return isNaN(n) ? 0 : n;
        }).filter(p => p > 0);
        if (prices.length < 2) {
          previewEl.innerHTML = '<p class="text-dim text-sm">Enter at least 2 categories to split.</p>';
          return;
        }
        const totalSell = prices.reduce((s, p) => s + p, 0);
        let html = '<table class="table text-sm"><thead><tr><th>Category</th><th>Sell Price</th><th>Cost Share</th><th>Unit Cost</th></tr></thead><tbody>';
        for (const sp of prices) {
          const ratio = sp / totalSell;
          const costShare = totalCost * ratio;
          const unitCost = qty > 0 ? costShare / qty : 0;
          const catName = activeCats.find(c => c.sell_price === sp)?.name || `Rs ${sp}`;
          html += `<tr><td>${esc(catName)}</td><td>Rs ${sp}</td><td>Rs ${costShare.toFixed(2)}</td><td>Rs ${unitCost.toFixed(2)}</td></tr>`;
        }
        html += '</tbody></table>';
        previewEl.innerHTML = html;
      }
      splitInput.oninput = updatePreview;
      splitInput.onkeydown = (e) => { if (e.key === 'Enter') $('#split-confirm-btn').click(); };

      // Confirm → replace the row with multiple rows
      $('#split-confirm-btn').onclick = () => {
        const input = splitInput.value.trim();
        const cats = input.split(/[,;\s]+/).map(s => s.trim().toUpperCase()).filter(s => s);
        const prices = cats.map(c => {
          if (c === 'A') return 250;
          if (c === 'B') return 500;
          if (c === 'C') return 750;
          if (c === 'D') return 1000;
          const n = parseInt(c);
          return isNaN(n) ? 0 : n;
        }).filter(p => p > 0);
        if (prices.length < 2) { toast('Enter at least 2 categories', 'error'); return; }

        const totalSell = prices.reduce((s, p) => s + p, 0);
        const totalCost = price * qty;

        // Capture WHERE the original row sits BEFORE removing it, so the
        // split rows land exactly there (not at the end of the table) — and
        // WHICH serial it held, so the splits become 3.1, 3.2 (parent SR).
        const before = tr.nextSibling;
        const parentSr = parseInt(tr.querySelector('.i-sr')?.textContent) || 0;
        // Remove the original row
        tr.remove();

        // Insert split rows in its place (marked as split children so they
        // get sub-serial numbers like 3.1, 3.2 …)
        for (const sp of prices) {
          const cat = activeCats.find(c => c.sell_price === sp);
          const ratio = sp / totalSell;
          const unitCost = qty > 0 ? (totalCost * ratio) / qty : 0;
          renderRow({
            raw: raw + ` (${sp})`,
            price: parseFloat(unitCost.toFixed(6)),
            qty: qty,
            unit: 'pcs',
            category_id: cat ? cat.id : null,
            page_no: tr.dataset.page || null,
          }, { before, splitChild: true, parentSr });
        }

        closeModal();
        recalcGrand();
        updateSerialNumbers();
        toast(`Split into ${prices.length} rows`, 'success');
      };
    });

    // Insert position: `opts.before` is the node the new row must land in
    // front of (captured by the split flow BEFORE the parent row is removed).
    // Falls back to append at the end (default / Add Row).
    if (opts.before && opts.before.parentNode === itemsBody) {
      itemsBody.insertBefore(tr, opts.before);
    } else {
      itemsBody.appendChild(tr);
    }
    recalcRow(tr.querySelector('.i-price'));
  }

  // Sort items by page_no before rendering (null page_no goes last)
  const sortedItems = b.items.length ? [...b.items] : [{}];
  sortedItems.sort((a, b) => {
    const pa = a.page_no || 9999;
    const pb = b.page_no || 9999;
    return pa - pb;
  });
  // v8.5.4: The AI now creates multiple rows directly when it detects
  // multiple categories on a single bill row. No frontend auto-split needed.
  // The Split button (per row) is still available for manual user-initiated splits.
  sortedItems.forEach(renderRow);
  recalcGrand();

  // Add serial numbers to each row. Split rows (marked data-split-child)
  // carry the ORIGINAL item's serial with a sub-index: if the item was
  // SR 3, its splits become 3.1, 3.2 … and the following rows keep their
  // numbers (the main counter continues from the parent's SR).
  function updateSerialNumbers() {
    let main = 0;    // last parent SR
    let sub = 0;     // sub-index within the current parent
    $$('.item-row').forEach(tr => {
      const srCell = tr.querySelector('.i-sr');
      if (!srCell) return;
      if (tr.dataset.splitChild === '1') {
        // Anchor to the parent's serial, captured at split time
        const p = parseInt(tr.dataset.parentSr);
        if (!isNaN(p) && p > main) main = p;
        sub += 1;
        srCell.textContent = `${main}.${sub}`;
        srCell.classList.add('i-sr-sub');
      } else {
        main += 1;
        sub = 0;
        srCell.textContent = main;
        srCell.classList.remove('i-sr-sub');
      }
    });
  }

  // Insert page section headers (if items have page_no) — sorted by page number
  function insertPageHeaders() {
    const rows = $$('.item-row');
    let lastPage = null;
    rows.forEach(tr => {
      const pg = tr.dataset.page;
      if (pg && pg !== lastPage) {
        lastPage = pg;
        // Insert a page header row before this item
        const headerRow = document.createElement('tr');
        headerRow.className = 'page-section-header';
        const pageNum = parseInt(pg);
        // v8.5.4: use the actual image element's offsetTop instead of a
        // hardcoded 600px-per-page estimate. This fixes the drift where
        // "View page 4" showed page 3 (because images have varying heights).
        const hasPage = b.pages[pageNum - 1];
        headerRow.innerHTML = `<td colspan="13">
          <div class="page-section-label">
            ${icon('image', 14)} Page ${esc(pg)}
            ${hasPage ? `<button class="btn btn-ghost btn-sm" id="view-page-${pageNum}" title="Scroll image to page ${pageNum}">View image →</button>` : ''}
          </div>
        </td>`;
        tr.parentNode.insertBefore(headerRow, tr);
        // Wire the button: scroll the .bill-images container so the target
        // image's top aligns with the container's visible top.
        if (hasPage) {
          const btn = headerRow.querySelector(`#view-page-${pageNum}`);
          if (btn) {
            btn.addEventListener('click', () => {
              const container = document.querySelector('.bill-images');
              if (!container) return;
              // Find the Nth <img> inside the container
              const imgs = container.querySelectorAll('.bill-img');
              const targetImg = imgs[pageNum - 1];
              if (targetImg) {
                // Scroll so the image's top is at the container's top
                // (offsetTop is relative to the offsetParent, which is the container)
                container.scrollTo({
                  top: targetImg.offsetTop - container.offsetTop,
                  behavior: 'smooth'
                });
              }
            });
          }
        }
      }
    });
  }
  insertPageHeaders();
  updateSerialNumbers();

  // Row add buttons — always renumber so the new row gets its Sr
  $('#add-row-btn').addEventListener('click', () => { renderRow(); updateSerialNumbers(); });
  $('#add-5-rows-btn').addEventListener('click', () => { for (let i = 0; i < 5; i++) renderRow(); updateSerialNumbers(); });

  // Toggle extra columns
  let extraColsVisible = true; // default: show all
  const applyColVisibility = () => {
    document.querySelectorAll('.col-extra').forEach(el => {
      el.style.display = extraColsVisible ? '' : 'none';
    });
    const btn = $('#toggle-cols-btn');
    if (btn) btn.innerHTML = `${icon('settings', 12)} ${extraColsVisible ? 'Hide' : 'All'}`;
  };
  applyColVisibility();
  $('#toggle-cols-btn').addEventListener('click', () => {
    extraColsVisible = !extraColsVisible;
    applyColVisibility();
  });

  // ---- v8.18.6: Image viewer — pinch / ctrl+scroll / button zoom + free pan ----
  // Zoom sets each image's width to `zoom`% of the panel, so the
  // .bill-images container (overflow:auto) grows real scrollbars on BOTH
  // axes — the browser then pans natively: wheel, trackpad, scrollbars and
  // one-finger touch scroll all just work. On top of that:
  //   • ctrl+wheel (what a trackpad pinch emits) → zoom anchored at cursor
  //   • two-finger pinch (touch screens)         → zoom anchored at midpoint
  //   • double-click / double-tap                → toggle 100% ↔ 250%
  //   • mouse drag while zoomed                  → pan (grab cursor)
  // (The old version scaled via CSS transform with overflow-x:hidden, so
  // anything past 100% was clipped on the sides and could not be reached.)
  const MIN_ZOOM = 50, MAX_ZOOM = 500;
  let imgZoom = 100, imgRotation = 0;
  const imgCont = $('#bill-images-container');
  const zoomLabel = $('#img-zoom-label');

  const setZoom = (pct) => {
    imgZoom = Math.round(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, pct)));
    $$('.bill-img').forEach(img => { img.style.width = imgZoom + '%'; });
    if (zoomLabel) zoomLabel.textContent = imgZoom + '%';
    if (imgCont) imgCont.classList.toggle('zoomed', imgZoom > 100);
  };
  const setRotation = (deg) => {
    imgRotation = ((deg % 360) + 360) % 360;
    $$('.bill-img').forEach(img => { img.style.transform = `rotate(${imgRotation}deg)`; });
  };
  // Zoom while keeping the content point under (cx, cy) — in client coords —
  // pinned, then let the container scroll to wherever the user is looking.
  const zoomAt = (pct, cx, cy) => {
    if (!imgCont) { setZoom(pct); return; }
    const r = imgCont.getBoundingClientRect();
    const ax = (cx != null ? cx : r.left + r.width / 2) - r.left;
    const ay = (cy != null ? cy : r.top + r.height / 2) - r.top;
    const old = imgZoom;
    setZoom(pct);
    const k = imgZoom / old;
    imgCont.scrollLeft = (imgCont.scrollLeft + ax) * k - ax;
    imgCont.scrollTop = (imgCont.scrollTop + ay) * k - ay;
  };

  if (imgCont) {
    $('#img-zoom-in').addEventListener('click', () => zoomAt(imgZoom + 25));
    $('#img-zoom-out').addEventListener('click', () => zoomAt(imgZoom - 25));
    $('#img-rotate-left').addEventListener('click', () => setRotation(imgRotation - 90));
    $('#img-rotate-right').addEventListener('click', () => setRotation(imgRotation + 90));
    $('#img-reset').addEventListener('click', () => {
      setZoom(100); setRotation(0);
      imgCont.scrollLeft = 0; imgCont.scrollTop = 0;
    });

    // Ctrl/Cmd+wheel = zoom (trackpad pinch emits exactly this; mouse users
    // hold Ctrl). Plain wheel is left alone → native vertical scroll.
    imgCont.addEventListener('wheel', (e) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      zoomAt(imgZoom * Math.exp(-e.deltaY * 0.002), e.clientX, e.clientY);
    }, { passive: false });

    // Two-finger pinch on touch screens. We only take ownership of the
    // gesture while exactly 2 fingers are down (preventDefault on the 2nd
    // touchstart stops the browser panning), so single-finger native
    // scrolling between/around pages keeps working.
    let pinchDist = 0, pinchZoom0 = 100;
    const tDist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
    const tMid = (t) => ({ x: (t[0].clientX + t[1].clientX) / 2, y: (t[0].clientY + t[1].clientY) / 2 });
    imgCont.addEventListener('touchstart', (e) => {
      if (e.touches.length === 2) {
        e.preventDefault();
        pinchDist = tDist(e.touches);
        pinchZoom0 = imgZoom;
      }
    }, { passive: false });
    imgCont.addEventListener('touchmove', (e) => {
      if (e.touches.length === 2 && pinchDist > 0) {
        e.preventDefault();
        const m = tMid(e.touches);
        zoomAt(pinchZoom0 * (tDist(e.touches) / pinchDist), m.x, m.y);
      }
    }, { passive: false });
    imgCont.addEventListener('touchend', (e) => {
      if (e.touches.length < 2) pinchDist = 0;
    }, { passive: true });

    // Double-click / double-tap an image → toggle fit / 250% at that point
    imgCont.addEventListener('dblclick', (e) => {
      if (!e.target.closest('.bill-img')) return;
      e.preventDefault();
      zoomAt(imgZoom > 100 ? 100 : 250, e.clientX, e.clientY);
    });

    // Mouse drag-pan while zoomed in (grab → grabbing cursor)
    let panDrag = null;
    imgCont.addEventListener('pointerdown', (e) => {
      if (e.pointerType !== 'mouse' || e.button !== 0 || imgZoom <= 100) return;
      panDrag = { x: e.clientX, y: e.clientY, sl: imgCont.scrollLeft, st: imgCont.scrollTop };
      imgCont.classList.add('grabbing');
    });
    window.addEventListener('pointermove', (e) => {
      if (!panDrag) return;
      imgCont.scrollLeft = panDrag.sl - (e.clientX - panDrag.x);
      imgCont.scrollTop = panDrag.st - (e.clientY - panDrag.y);
    });
    window.addEventListener('pointerup', () => {
      if (panDrag) { panDrag = null; imgCont.classList.remove('grabbing'); }
    });
  }

  // ---- v8.5.2: Wire up Save / Delete / Add-pages handlers ----
  // The handlers live in bill-edit-extras.js (window.__initBillEditExtras).
  // Without this call, the Save/Delete buttons do nothing when clicked.
  if (typeof window.__initBillEditExtras === 'function') {
    try {
      window.__initBillEditExtras(id, itemsBody);
    } catch (e) {
      console.error('bill-edit-extras init failed:', e);
      toast('Some buttons may not work — check console for errors', 'error');
    }
  } else {
    console.warn('window.__initBillEditExtras not defined — Save/Delete buttons will not work');
  }

  // ---- v8.5.2: Back button — navigates to /bills list (not browser back) ----
  // The browser back button may go to the launcher if the user came from there.
  // A dedicated "Back to Bills" button in the header always goes to /bills.
  const backBtn = $('#back-btn');
  if (backBtn) {
    backBtn.addEventListener('click', () => navigate('/bills'));
  }
  // Also: Escape key returns to /bills list (only fires once per visit)
  // v8.5.4: Don't fire Escape if a modal is open OR if the user is in an input
  // (Escape on an input closes the datalist autocomplete — don't also navigate away).
  document.addEventListener('keydown', function escBack(e) {
    if (e.key === 'Escape' && location.hash.startsWith('#/bills/')) {
      // Check if a modal is open — let the modal's own Escape handler close it
      if (document.getElementById('modal-root') && document.getElementById('modal-root').children.length > 0) return;
      // Check if the user is focused in an input/select/textarea — let the browser close the datalist
      const tag = e.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      document.removeEventListener('keydown', escBack);
      navigate('/bills');
    }
  });

  // ---- v8.5.4: Prevent number inputs from incrementing on mouse scroll ----
  // CSS hides the spinner buttons, but the wheel-scroll behavior still changes
  // values when the input is focused. This listener blocks it.
  document.addEventListener('wheel', (e) => {
    if (e.target.tagName === 'INPUT' && e.target.type === 'number') {
      e.target.blur();
    }
  }, { passive: true, capture: true });

  // ---- v8.5.4: Sticky image panel ----
  // The scroll container is .shell-content (not window), so CSS position:sticky
  // should work. We removed the JS fallback that was causing issues.
  // CSS sticky sticks relative to the nearest scroll ancestor (.shell-content).
  // No JS needed — the CSS in pages.css handles it.

  // ---- v8.18.5: Image panel layout — drag to resize + reposition ----
  // The user can now:
  //   • DRAG the divider between the images and the table: dragging right
  //     makes the bill images wider and the items table narrower (and vice
  //     versa). In Top/Bottom layouts it adjusts the image panel height.
  //   • Click the ⇄ toolbar button to move the images Left / Right / Top /
  //     Bottom of the page.
  //   • Double-click the divider (or press R while focused on it) to reset.
  // The choice persists in localStorage across sessions.
  const LAYOUT_KEY = 'bb.billImagesLayout';
  const LAYOUT_DEFAULTS = { pos: 'left', widthPct: 43, heightPx: 320 };
  let imgLayout = { ...LAYOUT_DEFAULTS };
  try {
    Object.assign(imgLayout, JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}'));
  } catch { /* corrupted value — fall back to defaults */ }

  const gridEl = $('#bill-edit-grid');
  const handleEl = $('#bill-resize-handle');

  function clampLayout() {
    imgLayout.widthPct = Math.min(72, Math.max(18, +imgLayout.widthPct || LAYOUT_DEFAULTS.widthPct));
    imgLayout.heightPx = Math.min(760, Math.max(140, +imgLayout.heightPx || LAYOUT_DEFAULTS.heightPx));
    if (!['left', 'right', 'top', 'bottom'].includes(imgLayout.pos)) imgLayout.pos = 'left';
  }

  function applyImgLayout() {
    if (!gridEl) return;
    clampLayout();
    ['img-left', 'img-right', 'img-top', 'img-bottom'].forEach(c => gridEl.classList.remove(c));
    gridEl.classList.add('img-' + imgLayout.pos);
    gridEl.style.setProperty('--img-w', imgLayout.widthPct + '%');
    gridEl.style.setProperty('--img-h', imgLayout.heightPx + 'px');
    const vertical = imgLayout.pos === 'top' || imgLayout.pos === 'bottom';
    if (handleEl) handleEl.setAttribute('aria-orientation', vertical ? 'horizontal' : 'vertical');
    const posBtn = $('#img-pos-btn');
    if (posBtn) {
      const labels = { left: 'Images on left', right: 'Images on right', top: 'Images on top', bottom: 'Images on bottom' };
      posBtn.title = `Image panel position: ${labels[imgLayout.pos]} — click to change`;
    }
  }

  function saveImgLayout() {
    try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(imgLayout)); } catch { }
  }

  applyImgLayout();

  // Position menu (⇄ button) — small popover with the 4 layout options
  const posBtn = $('#img-pos-btn');
  if (posBtn) {
    posBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      document.querySelectorAll('.img-pos-menu').forEach(m => m.remove());
      const menu = document.createElement('div');
      menu.className = 'img-pos-menu';
      const opts = [
        ['left', '◧ Images on Left'],
        ['right', '◨ Images on Right'],
        ['top', '⬓ Images on Top'],
        ['bottom', '⬔ Images on Bottom'],
      ];
      menu.innerHTML = opts.map(([p, label]) =>
        `<button data-pos="${p}" class="${imgLayout.pos === p ? 'active' : ''}">${label}</button>`
      ).join('');
      const rect = posBtn.getBoundingClientRect();
      menu.style.top = (rect.bottom + 6) + 'px';
      // Keep the menu on-screen when the panel is narrow
      menu.style.left = Math.min(window.innerWidth - 170, Math.max(8, rect.left - 110)) + 'px';
      // Append INSIDE the toolbar (position:fixed places it by viewport
      // coordinates) so a route change destroys it with the page — no orphan
      // menus floating over the next page.
      posBtn.closest('.bill-images-toolbar')?.appendChild(menu) || document.body.appendChild(menu);
      menu.querySelector('button').focus();
      const close = () => {
        menu.remove();
        document.removeEventListener('click', onDoc);
        menu.removeEventListener('keydown', onKey);
        posBtn.focus();
      };
      // Escape closes the menu WITHOUT triggering the page's Escape→back
      // navigation (stopPropagation stops the document-level handler).
      const onKey = (ev) => {
        if (ev.key === 'Escape') { ev.stopPropagation(); close(); }
      };
      menu.addEventListener('keydown', onKey);
      menu.addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-pos]');
        if (!b) return;
        imgLayout.pos = b.dataset.pos;
        saveImgLayout();
        applyImgLayout();
        close();
      });
      const onDoc = (ev) => { if (!menu.contains(ev.target) && ev.target !== posBtn) close(); };
      setTimeout(() => document.addEventListener('click', onDoc), 0);
    });
  }

  // Drag the divider to resize (pointer events cover mouse + touch)
  if (handleEl) {
    handleEl.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      handleEl.setPointerCapture(e.pointerId);
      handleEl.classList.add('dragging');
      document.body.classList.add('bill-resizing');
      const rect = gridEl.getBoundingClientRect();
      const startX = e.clientX, startY = e.clientY;
      const startW = imgLayout.widthPct, startH = imgLayout.heightPx;
      const vertical = imgLayout.pos === 'top' || imgLayout.pos === 'bottom';

      const onMove = (ev) => {
        if (vertical) {
          let dh = ev.clientY - startY;
          if (imgLayout.pos === 'bottom') dh = -dh; // panel is below — drag up to grow
          imgLayout.heightPx = startH + dh;
        } else {
          let dw = ((ev.clientX - startX) / rect.width) * 100;
          if (imgLayout.pos === 'right') dw = -dw; // panel is on the right — drag left to grow
          imgLayout.widthPct = startW + dw;
        }
        applyImgLayout(); // live preview (clamps inside)
      };
      const onUp = () => {
        handleEl.removeEventListener('pointermove', onMove);
        handleEl.removeEventListener('pointerup', onUp);
        handleEl.removeEventListener('pointercancel', onUp);
        handleEl.classList.remove('dragging');
        document.body.classList.remove('bill-resizing');
        clampLayout();
        saveImgLayout();
      };
      handleEl.addEventListener('pointermove', onMove);
      handleEl.addEventListener('pointerup', onUp);
      handleEl.addEventListener('pointercancel', onUp);
    });

    // Double-click resets the layout to defaults
    handleEl.addEventListener('dblclick', () => {
      imgLayout = { ...LAYOUT_DEFAULTS };
      saveImgLayout();
      applyImgLayout();
      toast('Image panel layout reset', 'info');
    });

    // Keyboard: arrows nudge size, Home resets
    handleEl.addEventListener('keydown', (e) => {
      const vertical = imgLayout.pos === 'top' || imgLayout.pos === 'bottom';
      let handled = true;
      if (e.key === 'ArrowLeft' && !vertical) imgLayout.widthPct -= (e.shiftKey ? 10 : 2);
      else if (e.key === 'ArrowRight' && !vertical) imgLayout.widthPct += (e.shiftKey ? 10 : 2);
      else if (e.key === 'ArrowUp' && vertical) imgLayout.heightPx -= (e.shiftKey ? 100 : 20);
      else if (e.key === 'ArrowDown' && vertical) imgLayout.heightPx += (e.shiftKey ? 100 : 20);
      else if (e.key === 'Home' || e.key === 'r' || e.key === 'R') imgLayout = { ...LAYOUT_DEFAULTS };
      else handled = false;
      if (handled) {
        e.preventDefault();
        clampLayout();
        saveImgLayout();
        applyImgLayout();
      }
    });
  }

  // ---- Verification checkboxes ----

// Extras extracted to bill-edit-extras.js
});

