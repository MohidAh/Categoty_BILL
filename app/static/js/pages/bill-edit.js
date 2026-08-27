// Bill detail / edit page — enhanced with profit calc, full manual entry
import { route, navigate } from '../router.js';
import { api, apiPost, apiDelete, apiUpload } from '../api.js';
import { $, $$, toast, showLoading, hideLoading,
         esc, fmt, fmtRs, fmtDate, fmtPct, fmtDecimalPct, icon, iconHtml, openModal, closeModal } from '../utils.js';

route('/bills/', async (el, path) => {
  const id = path.split('/').pop();
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

    ${b.flags.length ? b.flags.map(f => `<div class="alert alert-warning mb-2">${iconHtml('alert', 'alert-icon')}<div>${esc(f)}</div></div>`).join('') : (b.status === 'confirmed' ? `<div class="alert alert-success mb-2">${iconHtml('check', 'alert-icon')}<div>All checks passed — bill is confirmed.</div></div>` : '')}

    <div class="bill-edit">
      <div class="bill-images-panel">
        ${b.pages.length ? `
          <div class="bill-images-toolbar">
            <button class="btn btn-ghost btn-sm btn-icon" id="img-zoom-out" title="Zoom out">−</button>
            <span class="bill-images-zoom" id="img-zoom-label">100%</span>
            <button class="btn btn-ghost btn-sm btn-icon" id="img-zoom-in" title="Zoom in">+</button>
            <button class="btn btn-ghost btn-sm btn-icon" id="img-rotate-left" title="Rotate left">↺</button>
            <button class="btn btn-ghost btn-sm btn-icon" id="img-rotate-right" title="Rotate right">↻</button>
            <button class="btn btn-ghost btn-sm" id="img-reset" title="Reset">Fit</button>
            <span class="bill-images-count">${b.pages.length} page${b.pages.length > 1 ? 's' : ''}</span>
          </div>
          <div class="bill-images" id="bill-images-container">
            ${b.pages.map(p => `<img src="/pages/${esc(p.filename)}" alt="Bill page ${p.page_no}" class="bill-img">`).join('')}
          </div>` :
          `<div class="bill-images-empty">${icon('image', 28)}<p>No images uploaded yet.<br>Click "Add Images" to attach bill photos.</p></div>`}
      </div>
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
                  <th style="width:28px">Sr</th>
                  <th class="col-required sortable" data-sort="raw">Item Name ↕</th>
                  <th class="col-extra col-code sortable" data-sort="code">Code ↕</th>
                  <th class="col-required table-num sortable" data-sort="price">Unit Cost ↕</th>
                  <th class="col-required table-num sortable" data-sort="qty">Qty ↕</th>
                  <th class="col-required">Unit</th>
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
                  <td class="col-extra col-code"></td>
                  <td class="col-required"></td>
                  <td class="col-required"></td>
                  <td class="col-required"></td>
                  <td class="col-extra col-pieces table-num" id="ft-pieces">0</td>
                  <td class="col-extra col-category"></td>
                  <td class="col-required"></td>
                  <td class="col-required table-num" id="ft-cost">Rs 0</td>
                  <td class="col-required table-num" id="ft-profit">Rs 0</td>
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
    let totalCost = 0, totalPieces = 0, totalProfit = 0;
    $$('.item-row').forEach(tr => {
      const price = parseFloat(tr.querySelector('.i-price').value) || 0;
      const qty = parseFloat(tr.querySelector('.i-qty').value) || 0;
      const unit = tr.querySelector('.i-unit').value;
      const sell = parseFloat(tr.querySelector('.i-sell').value) || 0;
      const p = unit === 'dozen' ? qty * 12 : qty;
      totalPieces += p;
      totalCost += price * p;
      totalProfit += (sell - price) * p;
    });
    const count = $$('.item-row').length;
    $('#item-count').textContent = `${count} item${count !== 1 ? 's' : ''}`;
    $('#ft-pieces').textContent = fmt(totalPieces);
    $('#ft-cost').textContent = fmtRs(totalCost);
    $('#ft-profit').textContent = fmtRs(totalProfit);
    $('#ft-profit').className = `table-num ${totalProfit >= 0 ? 'text-success' : 'text-danger'}`;

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

  function renderRow(it = {}) {
    const tr = document.createElement('tr');
    tr.className = 'item-row';
    tr.dataset.page = it.page_no || '';
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
      <td class="col-required">
        <div class="flex items-center gap-1">
          ${confHtml}
          <input class="input i-raw" value="${esc(it.raw || '')}" style="width:140px" placeholder="Item name">
        </div>
      </td>
      <td class="col-extra col-code"><input class="input i-code" value="${esc(it.item_code || '')}" style="width:80px" placeholder="SKU"></td>
      <td class="col-required table-num"><input class="input i-price" type="number" step="0.01" value="${it.price ?? 0}" style="width:85px;text-align:right" placeholder="0"></td>
      <td class="col-required table-num"><input class="input i-qty" type="number" step="0.01" value="${it.qty ?? 0}" style="width:60px;text-align:right" placeholder="0"></td>
      <td class="col-required"><select class="select i-unit" style="width:75px">
        <option value="pcs" ${it.unit === 'pcs' ? 'selected' : ''}>pcs</option>
        <option value="dozen" ${it.unit === 'dozen' ? 'selected' : ''}>dozen</option>
      </select></td>
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

        // Remove the original row
        tr.remove();

        // Insert split rows in its place
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
          });
        }

        closeModal();
        recalcGrand();
        updateSerialNumbers();
        toast(`Split into ${prices.length} rows`, 'success');
      };
    });

    itemsBody.appendChild(tr);
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

  // Add serial numbers to each row
  function updateSerialNumbers() {
    $$('.item-row').forEach((tr, idx) => {
      let srCell = tr.querySelector('.i-sr');
      if (!srCell) {
        srCell = document.createElement('td');
        srCell.className = 'i-sr text-dim';
        tr.insertBefore(srCell, tr.firstChild);
      }
      srCell.textContent = idx + 1;
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
        headerRow.innerHTML = `<td colspan="12">
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

  // Row add buttons
  $('#add-row-btn').addEventListener('click', () => renderRow());
  $('#add-5-rows-btn').addEventListener('click', () => { for (let i = 0; i < 5; i++) renderRow(); });

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

  // ---- Image zoom/rotate controls ----
  let imgZoom = 100, imgRotation = 0;
  const applyImgTransform = () => {
    const imgs = $$('.bill-img');
    imgs.forEach(img => {
      img.style.transform = `scale(${imgZoom / 100}) rotate(${imgRotation}deg)`;
      img.style.transformOrigin = 'top center';
    });
    const label = $('#img-zoom-label');
    if (label) label.textContent = `${imgZoom}%`;
  };
  const imgContainer = $('#bill-images-container');
  if (imgContainer) {
    $('#img-zoom-in').addEventListener('click', () => { imgZoom = Math.min(300, imgZoom + 25); applyImgTransform(); });
    $('#img-zoom-out').addEventListener('click', () => { imgZoom = Math.max(50, imgZoom - 25); applyImgTransform(); });
    $('#img-rotate-left').addEventListener('click', () => { imgRotation -= 90; applyImgTransform(); });
    $('#img-rotate-right').addEventListener('click', () => { imgRotation += 90; applyImgTransform(); });
    $('#img-reset').addEventListener('click', () => { imgZoom = 100; imgRotation = 0; applyImgTransform(); });
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

  // ---- Verification checkboxes ----

// Extras extracted to bill-edit-extras.js
});

