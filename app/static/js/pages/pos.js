// POS — full-featured point-of-sale for a wholesale discount shop.
// Features:
//  - Category buttons with live stock badges + out-of-stock warnings
//  - Cart with qty controls, line notes, quick discount
//  - Held orders (park & recall)
//  - Customer search with loyalty + outstanding credit info
//  - Loyalty point redemption (auto-converts to rupee discount)
//  - Split payments (cash + card + online)
//  - Quotations (save cart as quote, convert to sale later)
//  - Cash drawer quick actions (in / out)
//  - Last 5 sales panel for quick reprint
//  - Customer-facing display mode (toggle)
//  - Numeric keypad for touch / tablet
//  - Keyboard shortcuts (F1-F4 categories, F9 checkout, F10 hold, F12 quote)
//  - Kiosk mode: no sidebar, PIN-protected exit
import { route, navigate, reload } from '../router.js';
import { api, apiPost, apiPut, apiDelete } from '../api.js';
import { $, $$, esc, fmt, fmtRs, fmtDate, icon, iconHtml, toast, showLoading, hideLoading,
         openModal, closeModal, skeletonCards, errorBox, debounce } from '../utils.js';
import { renderKioskBar, initKioskBar } from '../components/kiosk-bar.js';
import { queueSale, isOnline, generateOfflineInvoiceNo, getQueueCount, initOfflineQueue, triggerFlush } from '../core/offline.js';

// Initialize offline queue listener (idempotent)
initOfflineQueue();

// Import extracted route components (Phase 5 decomposition)
import '../apps/pos/components/sales-history.js';
import '../apps/pos/components/sale-detail.js';
import '../apps/pos/components/quotes.js';
// v8.4: Import kiosk-extras functions (showScanModal, showCashActions, showZReport, toggleCustomerDisplay)
import { showScanModal, showCashActions, showZReport, toggleCustomerDisplay } from '../apps/pos/components/kiosk-extras.js';

// v8.8.0: Fetch tax settings on module load
(async () => {
  try {
    const r = await api('/api/settings');
    window._pos_tax_rate = parseFloat(r.tax_rate || 0) / 100;
    window._pos_tax_inclusive = (r.tax_inclusive || 'false') === 'true';
  } catch (e) { /* settings endpoint may not exist yet — default to 0% tax */ }
})();

// ---------- helpers ----------
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

// ==================================================================
// /pos — main POS screen
// ==================================================================
route('/pos', async (el) => {
  let posCats = [];
  let paymentMethods = [];
  let holdsCount = 0;
  let lastSales = [];
  let loyaltyRate = 1;
  let loyaltyPointsPerRs = 100;

  try {
    [posCats, paymentMethods, holdsRes, lastSalesRes, loyaltyRes] = await Promise.all([
      api('/api/pos/categories'),
      api('/api/payment-methods'),
      api('/api/pos/holds'),
      api('/api/sales?limit=5'),
      api('/api/loyalty/rate'),
    ]);
    holdsCount = holdsRes.holds?.length || 0;
    lastSales = lastSalesRes || [];
    loyaltyRate = loyaltyRes.rate || 1;
    loyaltyPointsPerRs = loyaltyRes.points_per_rs || 100;
  } catch (e) {}

  const pms = paymentMethods.length ? paymentMethods : [
    {name:'Cash',type:'cash',icon:''},{name:'Card',type:'card',icon:''},
    {name:'Online',type:'online',icon:''},{name:'Credit',type:'credit',icon:''}
  ];

  // POS state
  let cart = [];
  let discountVal = 0;
  let discountType = 'amount';
  let customerId = null;
  let customerLoyaltyPts = 0;
  let customerCredit = 0;
  let loyaltyPointsToRedeem = 0;
  let loyaltyDiscountVal = 0;
  let notesVal = '';
  let isCustomerDisplay = false;
  let stockWarnOverride = new Set(); // category IDs the user explicitly approved selling despite low/out stock
  let pendingQuoteId = null; // set when a quotation is loaded for conversion

  el.innerHTML = `
    ${renderKioskBar('/pos')}
    <div class="kiosk-content pos-shell">
      <!-- 3-COLUMN POS LAYOUT (Square / Toast / Shopify pattern)
           Left:   Items grid (large, touch-friendly)
           Center: Cart with line items + qty controls
           Right:  Customer + totals + payment + checkout
      -->
      <div class="pos-3col">

        <!-- LEFT: Items grid -->
        <section class="pos-col pos-col-items">
          <div class="pos-col-header">
            <h2>Select Items</h2>
            <div class="pos-search-wrap">
              <input class="input" id="pos-search" placeholder="Search items..." autocomplete="off">
            </div>
          </div>
          <!-- v8.12.0: QTY multiplier row (×1 / ×2 / ×3 / ×5 / ×10) -->
          <div class="pos-qty-row">
            <span class="pos-qty-label">QTY</span>
            <div class="pos-qty-pill-group" id="pos-qty-pills">
              <button class="pos-qty-pill active" data-qty="1">×1</button>
              <button class="pos-qty-pill" data-qty="2">×2</button>
              <button class="pos-qty-pill" data-qty="3">×3</button>
              <button class="pos-qty-pill" data-qty="5">×5</button>
              <button class="pos-qty-pill" data-qty="10">×10</button>
            </div>
          </div>
          <div class="pos-cat-grid" id="pos-cat-grid">
            ${renderCatButtons(posCats)}
          </div>
          <div class="pos-col-footer">
            <button class="btn btn-secondary btn-sm" id="btn-scan" title="Scan barcode (F8)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px"><path d="M3 5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><line x1="7" y1="8" x2="7" y2="16"/><line x1="11" y1="8" x2="11" y2="16"/><line x1="15" y1="8" x2="15" y2="16"/></svg>
              Scan
            </button>
            <button class="btn btn-secondary btn-sm" id="btn-holds" title="Held orders (F10)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px"><rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8"/><line x1="10" y1="14" x2="14" y2="14"/></svg>
              Holds <span class="badge badge-accent" id="holds-badge" style="display:none">${holdsCount}</span>
            </button>
          </div>
        </section>

        <!-- CENTER: Cart -->
        <section class="pos-col pos-col-cart">
          <div class="pos-col-header">
            <h2>Current Sale</h2>
            <div class="flex gap-2">
              <button class="btn btn-secondary btn-sm" id="btn-hold" title="Park this sale (F10)">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>
                Hold
              </button>
              <button class="btn btn-secondary btn-sm" id="btn-quote" title="Save as quotation (F12)">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
                Quote
              </button>
              <button class="btn btn-secondary btn-sm" id="btn-clear" title="Clear cart">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                Clear
              </button>
            </div>
          </div>

          <!-- Customer bar -->
          <div class="pos-customer-bar">
            <input class="input" id="cust-name" placeholder="Customer name (optional)" autocomplete="off">
            <input class="input" id="cust-phone" placeholder="Phone" autocomplete="off">
            <button class="btn btn-secondary btn-sm" id="btn-cust-search" title="Search existing customers"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></button>
          </div>
          <div id="cust-info" class="pos-cust-info" style="display:none"></div>
          <!-- v8.13.0: Walk-in badge — shows when no customer is attached, auto-hides when cashier types -->
          <div id="walkin-badge" class="pos-walkin-badge">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
            Walk-in sale — no customer attached
          </div>

          <!-- Cart line items -->
          <div id="cart-items" class="pos-cart-items">
            <div class="pos-empty-cart">
              <div class="pos-empty-cart-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></svg></div>
              <p>Cart is empty</p>
              <p class="text-xs text-dim">Tap items on the left to add</p>
            </div>
          </div>

          <!-- Sale notes -->
          <div class="pos-notes-row">
            <input class="input input-sm" id="sale-notes" placeholder="Sale notes (optional)" value="">
          </div>

          <!-- Held orders preview (compact, only shows if there are holds) -->
          <div class="pos-holds-preview" id="pos-holds-card" style="display:none">
            <div class="pos-holds-preview-title">Held Orders</div>
            <div id="pos-holds-list"></div>
          </div>
        </section>

        <!-- RIGHT: Payment + totals + checkout -->
        <section class="pos-col pos-col-pay">
          <!-- Discount -->
          <div class="pos-pay-section">
            <div class="pos-pay-section-title">Discount</div>
            <div class="pos-discount-row">
              <button class="btn btn-ghost btn-sm disc-q" data-pct="0">0%</button>
              <button class="btn btn-ghost btn-sm disc-q" data-pct="5">5%</button>
              <button class="btn btn-ghost btn-sm disc-q" data-pct="10">10%</button>
              <button class="btn btn-ghost btn-sm disc-q" data-pct="15">15%</button>
              <button class="btn btn-ghost btn-sm disc-q" data-pct="20">20%</button>
            </div>
            <div class="flex gap-2 mt-2">
              <input class="input input-sm" id="discount-input" type="number" value="0" min="0">
              <select class="select select-sm" id="discount-type" style="width:70px">
                <option value="percent">%</option>
                <option value="amount">Rs</option>
              </select>
            </div>
          </div>

          <!-- Loyalty redemption (only shows if customer has points) -->
          <div class="pos-pay-section" id="loyalty-row" style="display:none">
            <div class="pos-pay-section-title">Loyalty Points</div>
            <div class="text-xs text-dim mb-1" id="loyalty-available"></div>
            <div class="flex gap-2">
              <input class="input input-sm" id="loyalty-input" type="number" value="0" min="0" placeholder="Points">
              <button class="btn btn-sm" id="btn-loyalty-apply">Apply</button>
            </div>
            <div id="loyalty-applied" class="text-xs text-success mt-1"></div>
          </div>

          <!-- Totals (large, prominent) -->
          <div class="pos-pay-section pos-totals-block">
            <div class="pos-total-row" id="cart-gross-subtotal-row" style="display:none">
              <span class="text-dim">Gross Subtotal</span>
              <span id="cart-gross-subtotal" class="text-dim">Rs 0</span>
            </div>
            <div class="pos-total-row" id="cart-item-discounts-row" style="display:none">
              <span class="text-danger">Item Discounts</span>
              <span id="cart-item-discounts" class="text-danger">−Rs 0</span>
            </div>
            <div class="pos-total-row">
              <span>Subtotal</span>
              <span id="cart-subtotal">Rs 0</span>
            </div>
            <div class="pos-total-row" id="discount-row" style="display:none">
              <span>Order Discount</span>
              <span id="cart-discount" class="text-danger">−Rs 0</span>
            </div>
            <div class="pos-total-row" id="loyalty-display-row" style="display:none">
              <span>Loyalty (−<span id="loyalty-pts-shown">0</span> pts)</span>
              <span id="cart-loyalty" class="text-danger">−Rs 0</span>
            </div>
            <div class="pos-total-row" id="cart-tax-row" style="display:none">
              <span id="cart-tax-label">Tax</span>
              <span id="cart-tax-amount" class="text-warning">+Rs 0</span>
            </div>
            <div class="pos-grand-total-block">
              <div class="pos-grand-total-label">TOTAL</div>
              <div class="pos-grand-total-amount" id="cart-total">Rs 0</div>
            </div>
            <div class="pos-total-row" id="split-remaining-row" style="display:none">
              <span>Remaining</span>
              <span id="split-remaining" class="text-warning"></span>
            </div>
          </div>

          <!-- v8.4: Cash Received + Change Calculator (only shows when Cash is selected) -->
          <div class="pos-pay-section" id="cash-calc-section">
            <div class="pos-pay-section-title">Cash Received &amp; Change</div>
            <div class="flex gap-2">
              <input class="input input-sm" id="cash-received" type="number" value="0" min="0" placeholder="0" style="flex:1">
              <button class="btn btn-ghost btn-sm" id="btn-cash-exact" title="Set to exact total">Exact</button>
            </div>
            <div class="pos-quick-cash-row" id="quick-cash-row">
              <button class="btn btn-ghost btn-sm cash-quick" data-amt="100">+100</button>
              <button class="btn btn-ghost btn-sm cash-quick" data-amt="500">+500</button>
              <button class="btn btn-ghost btn-sm cash-quick" data-amt="1000">+1000</button>
              <button class="btn btn-ghost btn-sm cash-quick" data-amt="5000">+5000</button>
            </div>
            <div class="pos-change-row" id="change-row">
              <span class="text-sm">Change to return</span>
              <span class="pos-change-amount" id="change-amount">Rs 0</span>
            </div>
          </div>

          <!-- Payment method -->
          <div class="pos-pay-section">
            <div class="pos-pay-section-title">Payment Method</div>
            <div class="pos-pay-methods" id="pay-methods">
              ${pms.map((pm, i) => `
                <label class="pos-pay-btn ${i === 0 ? 'active' : ''}">
                  <input type="radio" name="pay-method" value="${pm.type}" ${i === 0 ? 'checked' : ''}>
                  <span class="pos-pay-icon">${pm.icon}</span>
                  <span class="pos-pay-name">${esc(pm.name)}</span>
                </label>`).join('')}
              <label class="pos-pay-btn" title="Split between methods">
                <input type="radio" name="pay-method" value="split">
                <span class="pos-pay-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="5" y1="12" x2="19" y2="12"/><line x1="12" y1="5" x2="12" y2="19"/></svg></span>
                <span class="pos-pay-name">Split</span>
              </label>
            </div>

            <!-- Split inputs (hidden by default) -->
            <div id="split-inputs" style="display:none" class="pos-split-grid mt-2">
              <div>
                <label class="text-xs text-dim">Cash</label>
                <input class="input input-sm" id="split-cash" type="number" value="0" min="0">
              </div>
              <div>
                <label class="text-xs text-dim">Card</label>
                <input class="input input-sm" id="split-card" type="number" value="0" min="0">
              </div>
              <div>
                <label class="text-xs text-dim">Online</label>
                <input class="input input-sm" id="split-online" type="number" value="0" min="0">
              </div>
              <div class="flex gap-1 items-end">
                <button class="btn btn-ghost btn-sm" id="split-fill-cash">All Cash</button>
                <button class="btn btn-ghost btn-sm" id="split-half">½ ½</button>
              </div>
            </div>
          </div>

          <!-- Big checkout button -->
          <button class="pos-checkout-btn" id="checkout-btn" disabled>
            <span class="pos-checkout-icon">${icon('check', 24)}</span>
            <span class="pos-checkout-text">Complete Sale</span>
            <span class="pos-checkout-total" id="checkout-total-display">Rs 0</span>
          </button>

          <!-- Secondary actions -->
          <div class="pos-secondary-actions">
            <button class="btn btn-ghost btn-sm" id="btn-history">History</button>
            <button class="btn btn-ghost btn-sm" id="btn-quotes">Quotes</button>
            <button class="btn btn-ghost btn-sm" id="btn-cash">Cash</button>
            <button class="btn btn-ghost btn-sm" id="btn-zreport">Z-Report</button>
            <button class="btn btn-ghost btn-sm" id="btn-display" title="Customer-facing display">Display</button>
          </div>
        </section>

      </div>
    </div>`;

  // Initialize kiosk bar (live clock + exit button)
  initKioskBar();

  // v8.13.0: Walk-in badge — auto-hide when cashier types in customer name/phone
  function updateWalkinBadge() {
    const name = $('#cust-name')?.value?.trim() || '';
    const phone = $('#cust-phone')?.value?.trim() || '';
    const badge = $('#walkin-badge');
    if (!badge) return;
    if (name || phone) badge.classList.add('hidden');
    else badge.classList.remove('hidden');
  }
  $('#cust-name')?.addEventListener('input', updateWalkinBadge);
  $('#cust-phone')?.addEventListener('input', updateWalkinBadge);
  updateWalkinBadge();  // initial state — badge visible (walk-in by default)

  // ---------- render functions ----------
  // v8.12.0: tier-style category tiles — colored border + code pill + big price + hotkey number
  function renderCatButtons(cats, filter = '') {
    const filtered = filter
      ? cats.filter(c => c.name.toLowerCase().includes(filter.toLowerCase()) || c.code.toLowerCase().includes(filter.toLowerCase()))
      : cats;
    if (!filtered.length) return '<p class="text-dim text-sm" style="padding:24px;text-align:center">No items match.</p>';
    // v8.12.0: tier color palette — emerald/sky/amber/violet/pink/teal/rose cycle
    const TIER_COLORS = ['#10B981', '#0EA5E9', '#F59E0B', '#8B5CF6', '#EC4899', '#14B8A6', '#F43F5E', '#06B6D4'];
    return filtered.map((c, i) => {
      const stockBadge = c.out_of_stock
        ? '<span class="pos-stock-badge pos-stock-out">OUT OF STOCK</span>'
        : c.low_stock
          ? `<span class="pos-stock-badge pos-stock-low">${c.stock} LEFT</span>`
          : '';
      const disabled = c.out_of_stock && !stockWarnOverride.has(c.id) ? 'pos-cat-disabled' : '';
      // Hotkey number badge (1-7) — matches the DOLLARMAX reference style
      const hotkeyNum = i < 7 ? `<kbd class="pos-fkey">${i + 1}</kbd>` : '';
      // Use category.color if set, otherwise cycle through tier palette
      const tierColor = c.color || TIER_COLORS[i % TIER_COLORS.length];
      return `
        <button class="pos-cat-btn ${disabled}" style="--cat-color:${tierColor}" data-cat-id="${c.id}" data-cat-price="${c.sell_price}" data-cat-code="${c.code}" data-cat-name="${esc(c.name)}" data-cat-color="${tierColor}" data-cat-stock="${c.stock}">
          <div class="pos-cat-tile-top">
            <span class="pos-cat-tile-code">${c.code}</span>
            ${hotkeyNum}
          </div>
          <div class="pos-cat-tile-body">
            <div class="pos-cat-tile-name">${esc(c.name)}</div>
            <div class="pos-cat-tile-price">${fmt(c.sell_price)}</div>
            ${stockBadge ? `<div class="pos-cat-tile-stock">${stockBadge}</div>` : ''}
          </div>
        </button>`;
    }).join('');
  }

  function refreshCatButtons() {
    const filter = $('#pos-search').value.trim();
    $('#pos-cat-grid').innerHTML = renderCatButtons(posCats, filter);
    bindCatButtons();
  }

  function bindCatButtons() {
    $$('.pos-cat-btn').forEach(btn => {
      btn.onclick = () => {
        const id = parseInt(btn.dataset.catId);
        const price = parseFloat(btn.dataset.catPrice);
        const code = btn.dataset.catCode;
        const name = btn.dataset.catName;
        const color = btn.dataset.catColor || '';
        const stock = parseInt(btn.dataset.catStock);
        const qtyMult = parseInt($('#pos-qty-pills .active')?.dataset.qty) || 1;
        const existing = cart.find(i => i.catId === id);
        const inCart = existing ? existing.qty : 0;
        if (stock <= 0 && !stockWarnOverride.has(id)) {
          openModal('Out of Stock', `
            <p>This item is currently out of stock (0 available).</p>
            <p class="text-dim text-sm">Do you still want to sell it? This will create a negative stock position.</p>`,
            `<button class="btn btn-secondary" data-modal-close>Cancel</button>
             <button class="btn btn-danger" id="oos-confirm">Sell Anyway</button>`);
          $('#oos-confirm').onclick = () => {
            stockWarnOverride.add(id);
            closeModal();
            addToCart(id, price, code, name, color, qtyMult);
            refreshCatButtons();
          };
          return;
        }
        if (stock > 0 && inCart + qtyMult > stock && !stockWarnOverride.has(id)) {
          openModal('Low Stock', `
            <p>Only <b>${stock}</b> in stock. You're trying to add ${inCart + qtyMult}.</p>
            <p class="text-dim text-sm">Continue anyway?</p>`,
            `<button class="btn btn-secondary" data-modal-close>Cancel</button>
             <button class="btn" id="low-confirm">Yes, Add</button>`);
          $('#low-confirm').onclick = () => {
            stockWarnOverride.add(id);
            closeModal();
            addToCart(id, price, code, name, color, qtyMult);
          };
          return;
        }
        addToCart(id, price, code, name, color, qtyMult);
      };
    });
  }

  // v8.12.0: Bind QTY multiplier pills
  function bindQtyPills() {
    $$('#pos-qty-pills .pos-qty-pill').forEach(pill => {
      pill.onclick = () => {
        $$('#pos-qty-pills .pos-qty-pill').forEach(p => p.classList.remove('active'));
        pill.classList.add('active');
      };
    });
  }
  bindQtyPills();

  function renderCart() {
    const container = $('#cart-items');
    if (!cart.length) {
      container.innerHTML = `
        <div class="pos-empty-cart">
          <div class="pos-empty-cart-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></svg></div>
          <p class="pos-empty-cart-title">Start a New Sale</p>
          <p class="text-xs text-dim">Tap an item on the left or press F1–F7 to add</p>
        </div>`;
    } else {
      container.innerHTML = cart.map((item, idx) => {
        // v8.8.0: per-item discount computation
        const effPrice = (item.override_price && item.override_price > 0) ? item.override_price : item.price;
        const lineGross = effPrice * item.qty;
        let lineDisc = 0;
        if (item.discount_pct && item.discount_pct > 0) {
          lineDisc = lineGross * (item.discount_pct / 100);
        } else if (item.discount_amount && item.discount_amount > 0) {
          lineDisc = item.discount_amount;
        }
        const lineTotal = Math.max(0, lineGross - lineDisc);
        const hasDiscount = lineDisc > 0 || (item.override_price && item.override_price > 0);
        const basePrice = item.price;
        const showStrikethrough = hasDiscount && effPrice !== basePrice;

        return `
        <div class="pos-cart-item ${hasDiscount ? 'pos-cart-item-discounted' : ''}" data-idx="${idx}">
          <div class="pos-cart-item-badge" style="background:${item.color || 'var(--accent)'}">${esc(item.code)}</div>
          <div class="pos-cart-item-info">
            <div class="pos-cart-item-name">${esc(item.name)}</div>
            <div class="pos-cart-item-price">
              ${showStrikethrough ? `<span style="text-decoration:line-through;color:var(--text-dim)">Rs ${fmt(basePrice)}</span> <span class="text-success">Rs ${fmt(effPrice)}</span>` : `Rs ${fmt(effPrice)} each`}
              ${item.discount_pct > 0 ? `<span class="pos-disc-chip">−${item.discount_pct}%</span>` : ''}
              ${item.discount_amount > 0 && !item.discount_pct ? `<span class="pos-disc-chip">−Rs ${fmt(item.discount_amount)}</span>` : ''}
            </div>
          </div>
          <div class="pos-cart-item-qty">
            <button class="pos-qty-btn" data-act="dec" data-idx="${idx}" title="Decrease (−1)">−</button>
            <span class="pos-qty-val" data-act="qty-edit" data-idx="${idx}" title="Click to type qty">${item.qty}</span>
            <button class="pos-qty-btn" data-act="inc" data-idx="${idx}" title="Increase (+1)">+</button>
          </div>
          <div class="pos-cart-item-total">${fmtRs(lineTotal)}</div>
          <button class="pos-cart-item-disc" data-act="disc" data-idx="${idx}" title="Discount / Price Override">${icon('edit', 14)}</button>
          <button class="pos-cart-item-rm" data-act="rm" data-idx="${idx}" title="Remove">${icon('x', 14)}</button>
        </div>`;
      }).join('');
      // bind controls
      $$('#cart-items [data-act]').forEach(btn => {
        btn.onclick = () => {
          const idx = parseInt(btn.dataset.idx);
          const act = btn.dataset.act;
          if (act === 'inc') changeQty(idx, 1);
          else if (act === 'dec') changeQty(idx, -1);
          else if (act === 'rm') removeItem(idx);
          else if (act === 'disc') openLineDiscount(idx);
          else if (act === 'qty-edit') openQtyNumpad(idx);
        };
      });
    }
    updateTotals();
  }

  // v8.8.0: Per-item discount popover
  function openLineDiscount(idx) {
    const item = cart[idx];
    if (!item) return;
    const currentPct = item.discount_pct || 0;
    const currentAmt = item.discount_amount || 0;
    const currentOverride = item.override_price || '';
    openModal(`Discount — ${esc(item.name)}`, `
      <div class="pos-disc-modal">
        <div class="pos-disc-modal-row">
          <label class="text-sm text-dim">Base Price</label>
          <span class="text-sm">Rs ${fmt(item.price)} each</span>
        </div>
        <div class="pos-disc-modal-section">
          <div class="text-sm font-semibold mb-1">Quick % Off</div>
          <div class="flex gap-1">
            ${[0, 5, 10, 15, 20, 25, 50].map(p => `
              <button class="btn btn-sm pos-disc-quick-pct" data-pct="${p}" ${currentPct === p ? 'style="background:var(--accent);color:var(--bg-inverted)"' : ''}>${p}%</button>
            `).join('')}
          </div>
        </div>
        <div class="pos-disc-modal-section">
          <div class="text-sm font-semibold mb-1">Custom % Off</div>
          <input class="input input-sm" id="disc-custom-pct" type="number" value="${currentPct}" min="0" max="100" placeholder="e.g. 12.5">
        </div>
        <div class="pos-disc-modal-section">
          <div class="text-sm font-semibold mb-1">Fixed Rs Off (total for this line)</div>
          <input class="input input-sm" id="disc-custom-amt" type="number" value="${currentAmt}" min="0" placeholder="e.g. 50">
        </div>
        <div class="pos-disc-modal-section">
          <div class="text-sm font-semibold mb-1">Price Override (new price per unit)</div>
          <input class="input input-sm" id="disc-override" type="number" value="${currentOverride}" min="0" placeholder="e.g. 400 (requires manager PIN)">
          <div class="text-xs text-dim mt-1">Overrides the sell price. Requires manager PIN.</div>
        </div>
        <div class="pos-disc-modal-preview" id="disc-preview"></div>
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn btn-danger btn-sm" id="disc-clear-btn">Clear Discount</button>
       <button class="btn" id="disc-apply-btn">Apply</button>`);

    // Preview calculation
    function updatePreview() {
      const pct = parseFloat($('#disc-custom-pct').value) || 0;
      const amt = parseFloat($('#disc-custom-amt').value) || 0;
      const override = parseFloat($('#disc-override').value) || 0;
      const effPrice = override > 0 ? override : item.price;
      const lineGross = effPrice * item.qty;
      let disc = 0;
      if (pct > 0) disc = lineGross * (pct / 100);
      else if (amt > 0) disc = amt;
      const lineTotal = Math.max(0, lineGross - disc);
      $('#disc-preview').innerHTML = `
        <div class="text-sm mt-2 p-2" style="background:var(--bg-elevated);border-radius:6px">
          <div>Effective price: <b>Rs ${fmt(effPrice)}</b> × ${item.qty} = Rs ${fmt(lineGross)}</div>
          ${disc > 0 ? `<div class="text-danger">Discount: −Rs ${fmt(disc)}</div>` : ''}
          <div class="text-success font-semibold">Line total: Rs ${fmt(lineTotal)}</div>
        </div>`;
    }
    $('#disc-custom-pct').oninput = updatePreview;
    $('#disc-custom-amt').oninput = updatePreview;
    $('#disc-override').oninput = updatePreview;
    updatePreview();

    // Quick % buttons
    $$('.pos-disc-quick-pct').forEach(btn => btn.onclick = () => {
      $('#disc-custom-pct').value = btn.dataset.pct;
      $('#disc-custom-amt').value = 0;
      $('#disc-override').value = '';
      updatePreview();
    });

    $('#disc-apply-btn').onclick = () => {
      const pct = parseFloat($('#disc-custom-pct').value) || 0;
      const amt = parseFloat($('#disc-custom-amt').value) || 0;
      const override = parseFloat($('#disc-override').value) || 0;
      applyLineDiscount(idx, pct, amt, override);
      closeModal();
    };
    $('#disc-clear-btn').onclick = () => {
      applyLineDiscount(idx, 0, 0, 0);
      closeModal();
    };
  }

  function applyLineDiscount(idx, pct, amt, override) {
    const item = cart[idx];
    if (!item) return;
    item.discount_pct = pct > 0 ? pct : 0;
    item.discount_amount = amt > 0 ? amt : 0;
    item.override_price = override > 0 ? override : null;
    item.base_price = override > 0 ? item.price : null;
    renderCart();
    if (isCustomerDisplay) updateCustomerDisplay();
  }

  // v8.8.0: Qty numpad modal
  function openQtyNumpad(idx) {
    const item = cart[idx];
    if (!item) return;
    openModal(`Quantity — ${esc(item.name)}`, `
      <div style="text-align:center">
        <input class="input" id="qty-numpad-input" type="number" value="${item.qty}" min="1"
               style="text-align:center;font-size:32px;font-weight:800;height:60px" autofocus>
        <div class="pos-numpad-grid mt-3">
          ${[7,8,9,4,5,6,1,2,3,'C',0,'⏎'].map(k => `
            <button class="btn btn-lg pos-numpad-key" data-key="${k}">${k}</button>
          `).join('')}
        </div>
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="qty-numpad-set">Set Quantity</button>`);
    const input = $('#qty-numpad-input');
    $$('.pos-numpad-key').forEach(btn => btn.onclick = () => {
      const k = btn.dataset.key;
      if (k === 'C') input.value = '';
      else if (k === '⏎') { setQty(); }
      else input.value = (input.value + k).replace(/^0+/, '');
    });
    function setQty() {
      const q = parseInt(input.value) || 0;
      if (q > 0) {
        cart[idx].qty = q;
        renderCart();
        if (isCustomerDisplay) updateCustomerDisplay();
      }
      closeModal();
    }
    $('#qty-numpad-set').onclick = setQty;
    input.focus();
    input.select();
    input.onkeydown = (e) => { if (e.key === 'Enter') setQty(); };
  }

  function updateTotals() {
    // v8.8.0: per-item discount computation
    // lineGross = effPrice × qty; lineDisc = pct or amt; lineTotal = lineGross − lineDisc
    // subtotal (displayed) = Σ(lineTotal) — AFTER per-item discounts
    // grossSubtotal (pre-discount) = Σ(price × qty) — for reference
    let grossSubtotal = 0;
    let itemDiscountsTotal = 0;
    let subtotal = 0;
    for (const i of cart) {
      const effPrice = (i.override_price && i.override_price > 0) ? i.override_price : i.price;
      const lineGross = effPrice * i.qty;
      let lineDisc = 0;
      if (i.discount_pct && i.discount_pct > 0) lineDisc = lineGross * (i.discount_pct / 100);
      else if (i.discount_amount && i.discount_amount > 0) lineDisc = i.discount_amount;
      grossSubtotal += i.price * i.qty;
      itemDiscountsTotal += lineDisc;
      subtotal += Math.max(0, lineGross - lineDisc);
    }
    // Invoice-level discount (applies AFTER per-item discounts)
    let disc = 0;
    if (discountType === 'percent') disc = subtotal * (discountVal / 100);
    else disc = discountVal;
    // Tax (v8.8.0: read from settings)
    const taxRate = window._pos_tax_rate || 0;
    const taxInclusive = window._pos_tax_inclusive || false;
    const afterInvoiceDisc = Math.max(0, subtotal - disc - loyaltyDiscountVal);
    const taxAmount = taxRate > 0 ? (taxInclusive ? afterInvoiceDisc - (afterInvoiceDisc / (1 + taxRate)) : afterInvoiceDisc * taxRate) : 0;
    const total = taxInclusive ? afterInvoiceDisc : (afterInvoiceDisc + taxAmount);

    // v8.8.0: visible discount breakdown
    // Gross Subtotal (pre-item-discount) — only shown if there are per-item discounts
    const grossRow = $('#cart-gross-subtotal-row');
    const itemDiscRow = $('#cart-item-discounts-row');
    if (grossRow && itemDiscRow) {
      if (itemDiscountsTotal > 0) {
        grossRow.style.display = 'flex';
        $('#cart-gross-subtotal').textContent = fmtRs(grossSubtotal);
        itemDiscRow.style.display = 'flex';
        $('#cart-item-discounts').textContent = '−' + fmtRs(itemDiscountsTotal);
      } else {
        grossRow.style.display = 'none';
        itemDiscRow.style.display = 'none';
      }
    }
    $('#cart-subtotal').textContent = fmtRs(subtotal);
    if (disc > 0) {
      $('#discount-row').style.display = 'flex';
      $('#cart-discount').textContent = '−' + fmtRs(disc);
    } else {
      $('#discount-row').style.display = 'none';
    }
    if (loyaltyDiscountVal > 0) {
      $('#loyalty-display-row').style.display = 'flex';
      $('#loyalty-pts-shown').textContent = loyaltyPointsToRedeem;
      $('#cart-loyalty').textContent = '−' + fmtRs(loyaltyDiscountVal);
    } else {
      $('#loyalty-display-row').style.display = 'none';
    }
    // v8.8.0: Tax row
    const taxRow = $('#cart-tax-row');
    if (taxRow) {
      if (taxAmount > 0) {
        taxRow.style.display = 'flex';
        $('#cart-tax-amount').textContent = '+' + fmtRs(taxAmount);
        $('#cart-tax-label').textContent = `Tax (${(taxRate * 100).toFixed(1)}%)`;
      } else {
        taxRow.style.display = 'none';
      }
    }
    const totalEl = $('#cart-total');
    const oldTotal = totalEl.textContent;
    totalEl.textContent = fmtRs(total);
    // Pulse animation when total changes
    if (oldTotal !== fmtRs(total)) {
      totalEl.classList.remove('changed');
      void totalEl.offsetWidth; // trigger reflow
      totalEl.classList.add('changed');
    }
    // Also update the total shown on the checkout button
    const checkoutDisplay = $('#checkout-total-display');
    if (checkoutDisplay) checkoutDisplay.textContent = fmtRs(total);
    $('#checkout-btn').disabled = cart.length === 0;

    // Update split remaining
    const pm = $$('input[name="pay-method"]:checked')[0]?.value || 'cash';
    if (pm === 'split') {
      const cash = parseFloat($('#split-cash').value) || 0;
      const card = parseFloat($('#split-card').value) || 0;
      const online = parseFloat($('#split-online').value) || 0;
      const paid = cash + card + online;
      const remaining = total - paid;
      $('#split-remaining-row').style.display = 'flex';
      if (Math.abs(remaining) < 0.01) {
        $('#split-remaining').textContent = 'Exact';
        $('#split-remaining').className = 'text-success';
      } else if (remaining > 0) {
        $('#split-remaining').textContent = 'Rs ' + fmt(remaining) + ' left';
        $('#split-remaining').className = 'text-warning';
      } else {
        $('#split-remaining').textContent = 'Rs ' + fmt(Math.abs(remaining)) + ' over';
        $('#split-remaining').className = 'text-danger';
      }
    } else {
      $('#split-remaining-row').style.display = 'none';
    }
  }

  function updateCustomerInfo() {
    const infoEl = $('#cust-info');
    if (!customerId) {
      infoEl.style.display = 'none';
      $('#loyalty-row').style.display = 'none';
      return;
    }
    infoEl.style.display = 'block';
    infoEl.innerHTML = `
      <div class="pos-cust-info-grid">
        <div><span class="text-dim">⭐ Points:</span> <b>${fmt(customerLoyaltyPts)}</b></div>
        <div><span class="text-dim">Value:</span> <b class="text-success">Rs ${fmt(customerLoyaltyPts * loyaltyRate)}</b></div>
        <div><span class="text-dim">Outstanding:</span> <b class="${customerCredit > 0 ? 'text-danger' : ''}">Rs ${fmt(customerCredit)}</b></div>
        ${customerCredit > 0 ? `<div><button class="btn btn-secondary btn-sm" id="btn-pay-credit">Pay Credit</button></div>` : ''}
      </div>`;
    // Show loyalty redemption row if customer has points
    if (customerLoyaltyPts > 0) {
      $('#loyalty-row').style.display = 'flex';
      $('#loyalty-available').textContent = `${customerLoyaltyPts} pts available (Rs ${fmt(customerLoyaltyPts * loyaltyRate)})`;
      $('#loyalty-input').max = customerLoyaltyPts;
    } else {
      $('#loyalty-row').style.display = 'none';
    }
    if (customerCredit > 0) {
      $('#btn-pay-credit').onclick = () => openPayCreditModal();
    }
  }

  function openPayCreditModal() {
    openModal('Pay Outstanding Credit', `
      <p>Customer: <b>${esc($('#cust-name').value)}</b></p>
      <p>Outstanding: <b class="text-danger">Rs ${fmt(customerCredit)}</b></p>
      <div class="mt-3">
        <label class="text-xs text-dim">Amount to Pay</label>
        <input class="input" id="pay-credit-amount" type="number" value="${customerCredit}" min="0" max="${customerCredit}">
      </div>
      <div class="mt-2">
        <label class="text-xs text-dim">Method</label>
        <select class="select" id="pay-credit-method">
          <option value="cash">Cash</option>
          <option value="card">Card</option>
          <option value="online">Online</option>
        </select>
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="pay-credit-confirm">Record Payment</button>`);
    $('#pay-credit-confirm').onclick = async () => {
      const amount = parseFloat($('#pay-credit-amount').value) || 0;
      const method = $('#pay-credit-method').value;
      if (amount <= 0) { toast('Enter amount', 'error'); return; }
      try {
        await apiPost('/api/customers/payments', {
          customer_id: customerId,
          customer_name: $('#cust-name').value,
          amount, payment_method: method,
        });
        toast('Payment recorded', 'success');
        customerCredit = Math.max(0, customerCredit - amount);
        updateCustomerInfo();
        closeModal();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }

  // ---------- cart operations ----------
  function addToCart(catId, price, code, name, color, qtyMult = 1) {
    const existing = cart.find(i => i.catId === catId);
    if (existing) existing.qty += qtyMult;
    else cart.push({ catId, price, code, name, qty: qtyMult, color });
    renderCart();
    if (isCustomerDisplay) updateCustomerDisplay();
  }
  function changeQty(idx, delta) {
    cart[idx].qty += delta;
    if (cart[idx].qty <= 0) cart.splice(idx, 1);
    renderCart();
    if (isCustomerDisplay) updateCustomerDisplay();
  }
  function removeItem(idx) { cart.splice(idx, 1); renderCart(); if (isCustomerDisplay) updateCustomerDisplay(); }
  function clearCart() {
    cart = []; discountVal = 0; loyaltyPointsToRedeem = 0; loyaltyDiscountVal = 0;
    $('#discount-input').value = 0;
    $('#loyalty-input').value = 0;
    $('#loyalty-applied').textContent = '';
    $('#sale-notes').value = '';
    stockWarnOverride.clear();
    renderCart();
    refreshCatButtons();
    if (isCustomerDisplay) updateCustomerDisplay();
  }

  // ====================================================================
  // v8.8.0 — Inlined missing functions (were in dead component files)
  // ====================================================================

  function updateCustomerDisplay() {
    // No-op — customer display is handled by CSS class toggle in toggleCustomerDisplay()
  }

  // ---------- customer lookup ----------
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
          <div class="cust-search-row" data-cid="${c.id}" style="padding:8px;cursor:pointer;border-radius:6px">
            <div><b>${esc(c.name)}</b> ${c.phone ? '<span class="text-dim text-sm">'+esc(c.phone)+'</span>' : ''}</div>
            <div class="text-xs text-dim">
              ${c.loyalty_points||0} pts
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
  async function holdCart() {
    if (!cart.length) { toast('Cart is empty', 'info'); return; }
    try {
      const total = cart.reduce((s, i) => s + i.price * i.qty, 0)
        - (discountType === 'percent' ? cart.reduce((s, i) => s + i.price * i.qty, 0) * discountVal/100 : discountVal);
      const r = await apiPost('/api/pos/holds', {
        customer_name: $('#cust-name').value,
        customer_phone: $('#cust-phone').value,
        notes: $('#sale-notes').value,
        items: cart.map(i => ({ catId: i.catId, code: i.code, price: i.price, qty: i.qty, name: i.name })),
        discount: discountVal, discount_type: discountType, total,
      });
      toast(`Parked as ${r.reference}`, 'success');
      clearCart();
      holdsCount++;
      $('#holds-badge').textContent = holdsCount;
      $('#holds-badge').style.display = 'inline-block';
      loadHoldsPreview();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  async function loadHoldsPreview() {
    try {
      const r = await api('/api/pos/holds');
      const list = r.holds || [];
      holdsCount = list.length;
      $('#holds-badge').textContent = holdsCount;
      $('#holds-badge').style.display = holdsCount > 0 ? 'inline-block' : 'none';
      const card = $('#pos-holds-card');
      if (!card) return;
      if (!list.length) { card.style.display = 'none'; return; }
      card.style.display = 'block';
      $('#pos-holds-list').innerHTML = list.slice(0, 5).map(h => `
        <div class="hold-row">
          <div><b>${esc(h.reference)}</b> <span class="text-dim text-sm">${fmtTime(h.created_at)}</span></div>
          <div class="text-sm">${esc(h.customer_name || 'Walk-in')} · ${h.items?.length || 0} items · Rs ${fmt(h.total)}</div>
          <div class="flex gap-1 mt-1">
            <button class="btn btn-sm" data-recall="${h.id}">Recall</button>
            <button class="btn btn-ghost btn-sm" data-del-hold="${h.id}">${icon('trash', 12)}</button>
          </div>
        </div>`).join('');
      $$('#pos-holds-list [data-recall]').forEach(b => b.onclick = () => recallHold(parseInt(b.dataset.recall)));
      $$('#pos-holds-list [data-del-hold]').forEach(b => b.onclick = async () => {
        if (!confirm('Delete this held order?')) return;
        try { await apiDelete(`/api/pos/holds/${b.dataset.delHold}`); toast('Deleted', 'success'); loadHoldsPreview(); }
        catch (e) { toast('Error', 'error'); }
      });
    } catch (e) {}
  }

  async function recallHold(hid) {
    try {
      const h = await api(`/api/pos/holds/${hid}`);
      if (cart.length && !confirm('Current cart will be replaced. Continue?')) return;
      cart = (h.items || []).map(i => ({ catId: i.catId, price: i.price, code: i.code, name: i.name, qty: i.qty }));
      discountVal = h.discount || 0;
      discountType = h.discount_type || 'amount';
      $('#cust-name').value = h.customer_name || '';
      $('#cust-phone').value = h.customer_phone || '';
      $('#sale-notes').value = h.notes || '';
      $('#discount-input').value = discountVal;
      $('#discount-type').value = discountType;
      renderCart();
      await apiDelete(`/api/pos/holds/${hid}`);
      loadHoldsPreview();
      toast(`Recalled ${h.reference}`, 'success');
      lookupCustomer();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  function showHoldsModal() {
    openModal('Held Orders', `<div id="holds-modal-list" class="stat-list">Loading...</div>`,
      `<button class="btn btn-secondary" data-modal-close>Close</button>`);
    api('/api/pos/holds').then(r => {
      const list = r.holds || [];
      $('#holds-modal-list').innerHTML = list.length ? list.map(h => `
        <div class="hold-row">
          <div><b>${esc(h.reference)}</b> · ${fmtDate(h.created_at)} ${fmtTime(h.created_at)}</div>
          <div class="text-sm">${esc(h.customer_name || 'Walk-in')} · ${h.items?.length || 0} items · Rs ${fmt(h.total)}</div>
          ${h.notes ? `<div class="text-xs text-dim">${esc(h.notes)}</div>` : ''}
          <div class="flex gap-1 mt-1">
            <button class="btn btn-sm" data-recall="${h.id}">Recall</button>
            <button class="btn btn-ghost btn-sm" data-del-hold="${h.id}">${icon('trash', 12)} Delete</button>
          </div>
        </div>`).join('') : '<p class="text-dim text-sm">No held orders.</p>';
      $$('#holds-modal-list [data-recall]').forEach(b => b.onclick = () => { closeModal(); recallHold(parseInt(b.dataset.recall)); });
      $$('#holds-modal-list [data-del-hold]').forEach(b => b.onclick = async () => {
        if (!confirm('Delete?')) return;
        try { await apiDelete(`/api/pos/holds/${b.dataset.delHold}`); toast('Deleted', 'success'); showHoldsModal(); loadHoldsPreview(); }
        catch (e) { toast('Error', 'error'); }
      });
    }).catch(() => toast('Error loading holds', 'error'));
  }

  // ---------- quotations ----------
  async function saveQuote() {
    if (!cart.length) { toast('Cart is empty', 'info'); return; }
    openModal('Save as Quotation', `
      <p>This will save the current cart as a quotation. The customer can review it and you can convert it to a sale later.</p>
      <div class="mt-3">
        <label class="text-xs text-dim">Customer</label>
        <input class="input" id="quote-cust" value="${esc($('#cust-name').value)}" placeholder="Customer name">
      </div>
      <div class="mt-2">
        <label class="text-xs text-dim">Phone</label>
        <input class="input" id="quote-phone" value="${esc($('#cust-phone').value)}" placeholder="Phone">
      </div>
      <div class="mt-2">
        <label class="text-xs text-dim">Valid for (days)</label>
        <input class="input" id="quote-days" type="number" value="7" min="1" max="90">
      </div>
      <div class="mt-2">
        <label class="text-xs text-dim">Notes</label>
        <textarea class="input" id="quote-notes" rows="2">${esc($('#sale-notes').value)}</textarea>
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="quote-save-btn">Save Quotation</button>`);
    $('#quote-save-btn').onclick = async () => {
      const total = cart.reduce((s, i) => s + i.price * i.qty, 0)
        - (discountType === 'percent' ? cart.reduce((s, i) => s + i.price * i.qty, 0) * discountVal/100 : discountVal);
      try {
        const r = await apiPost('/api/quotations', {
          customer_name: $('#quote-cust').value,
          customer_phone: $('#quote-phone').value,
          notes: $('#quote-notes').value,
          items: cart.map(i => ({ catId: i.catId, code: i.code, price: i.price, qty: i.qty, name: i.name })),
          discount: discountVal, discount_type: discountType, total,
          valid_days: parseInt($('#quote-days').value) || 7,
        });
        toast(`Quotation ${r.quote_no} saved`, 'success');
        closeModal();
        clearCart();
        openModal('Quotation Saved', `
          <p>Quote <b>${r.quote_no}</b> saved. Valid until <b>${r.valid_until}</b>.</p>`,
          `<button class="btn btn-secondary" data-modal-close>Done</button>
           <button class="btn" onclick="window.open('/api/quotations/${r.id}/receipt','_blank')">Print Quote</button>`);
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }

  // ---------- checkout ----------
  // ---------- checkout ----------
  let _checkoutBusy = false;   // v8.18.0: one in-flight sale at a time
  async function checkout() {
    if (!cart.length) return;
    // v8.18.0: double-click / double-F9 guard — a second tap while the sale
    // POSTs would double-charge the customer and double-deduct stock.
    if (_checkoutBusy) return;
    _checkoutBusy = true;
    setTimeout(() => { _checkoutBusy = false; }, 30000);   // hard safety release
    const payMethod = $$('input[name="pay-method"]:checked')[0]?.value || 'cash';
    let splitCash = 0, splitCard = 0, splitOnline = 0;
    if (payMethod === 'split') {
      splitCash = parseFloat($('#split-cash').value) || 0;
      splitCard = parseFloat($('#split-card').value) || 0;
      splitOnline = parseFloat($('#split-online').value) || 0;
      const subtotal = cart.reduce((s, i) => s + i.price * i.qty, 0);
      const disc = discountType === 'percent' ? subtotal * discountVal/100 : discountVal;
      const total = Math.max(0, subtotal - disc - loyaltyDiscountVal);
      if (splitCash + splitCard + splitOnline < total - 0.01) {
        toast('Split amount is less than total', 'error');
        return;
      }
    }
    showLoading('Processing...');
    try {
      const items = cart.map(i => ({
        category_id: i.catId, category_code: i.code,
        sell_price: i.price, qty: i.qty, item_name: `${i.code} (${i.name})`,
        discount_pct: i.discount_pct || 0,
        discount_amount: i.discount_amount || 0,
        override_price: i.override_price || null,
        base_price: i.base_price || null,
      }));
      const payload = {
        customer_name: $('#cust-name').value,
        customer_phone: $('#cust-phone').value,
        customer_id: customerId,
        discount: discountVal, discount_type: discountType,
        payment_method: payMethod,
        // v8.8.0: map sub-methods (easypaisa/jazzcash/raast/bank) to online + submethod
        payment_submethod: (() => {
          if (payMethod === 'easypaisa') return 'easypaisa';
          if (payMethod === 'jazzcash') return 'jazzcash';
          if (payMethod === 'raast') return 'raast_qr';
          if (payMethod === 'bank') return 'bank_transfer';
          return null;
        })(),
        split_cash: splitCash, split_card: splitCard, split_online: splitOnline,
        loyalty_points_used: loyaltyPointsToRedeem,
        notes: $('#sale-notes').value,
        quotation_id: pendingQuoteId,
        items,
      };
      // Normalize sub-methods: easypaisa/jazzcash/raast/bank → payment_method='online'
      if (['easypaisa', 'jazzcash', 'raast', 'bank'].includes(payload.payment_method)) {
        payload.payment_method = 'online';
      }

      // Offline detection
      if (!isOnline()) {
        hideLoading();
        const offlineInvoiceNo = generateOfflineInvoiceNo();
        await queueSale(payload);
        const queueCount = await getQueueCount();
        const cartCount = cart.length;
        const subtotal = cart.reduce((s, i) => s + i.price * i.qty, 0);
        const disc = discountType === 'percent' ? subtotal * discountVal/100 : discountVal;
        const cartTotal = subtotal - disc - loyaltyDiscountVal;
        openModal('Sale Queued (Offline)', `
          <div style="text-align:center;padding:16px 0">
            <p class="text-sm text-dim">${esc(offlineInvoiceNo)}</p>
            <p style="font-size:32px;font-weight:800;margin:8px 0;color:var(--warning-text)">${fmtRs(cartTotal)}</p>
            <p class="text-sm">${cartCount} items &middot; ${esc(payMethod)}</p>
            <div class="alert alert-warning mt-3">
              <div><strong>You are offline.</strong> Sale has been saved locally and will sync automatically when you reconnect.</div>
              <div class="text-xs text-dim mt-2">${queueCount} sale(s) in queue</div>
            </div>
          </div>`,
          `<button class="btn btn-secondary" data-modal-close>Done</button>
           <button class="btn" id="offline-retry-btn">Try Sync Now</button>`);
        const retryBtn = $('#offline-retry-btn');
        if (retryBtn) retryBtn.onclick = async () => {
          retryBtn.disabled = true;
          retryBtn.textContent = 'Syncing...';
          await triggerFlush();
          setTimeout(() => { closeModal(); toast('Sync attempted — check queue status', 'info'); reload(); }, 1500);
        };
        clearCart();
        $('#cust-name').value = ''; $('#cust-phone').value = '';
        customerId = null; customerLoyaltyPts = 0; customerCredit = 0; pendingQuoteId = null;
        updateCustomerInfo();
        return;
      }

      const r = await apiPost('/api/sales', payload);
      hideLoading();
      // v8.12.0: Redesigned Sale Complete modal — big checkmark + total + teal CTAs
      const cashReceived = parseFloat($('#cash-received')?.value) || 0;
      const changeDue = payMethod === 'cash' && cashReceived > 0 ? cashReceived - r.total : 0;
      const payMethodLabel = payMethod === 'split'
        ? `split (Cash Rs ${fmt(splitCash)} / Card Rs ${fmt(splitCard)} / Online Rs ${fmt(splitOnline)})`
        : payMethod === 'credit' ? 'on credit' : `in ${payMethod}`;
      const itemCount = cart.reduce((s, i) => s + i.qty, 0);
      openModal('Sale Complete', `
        <div class="pos-success-modal">
          <div class="pos-success-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
          </div>
          <p class="pos-success-title">Sale Complete</p>
          <p class="pos-success-subtitle">${esc(r.invoice_no)} · ${itemCount} items</p>
          <p class="pos-success-total">${fmt(r.total)}</p>
          <p class="pos-success-method">paid ${esc(payMethodLabel)}</p>
          ${changeDue > 0 ? `<div class="pos-success-change">Change due <span class="pos-success-change-amount">Rs ${fmt(changeDue)}</span></div>` : ''}
          ${r.payment_status === 'credit' ? '<div class="badge badge-danger" style="margin-bottom:8px">CREDIT SALE</div>' : ''}
          ${r.payment_status === 'partial' ? '<div class="badge badge-warning" style="margin-bottom:8px">PARTIAL PAYMENT</div>' : ''}
          ${r.loyalty_points_used > 0 ? `<p class="text-xs text-success" style="margin-top:8px">${r.loyalty_points_used} loyalty points used (−Rs ${fmt(r.loyalty_discount)})</p>` : ''}
        </div>`,
        `<div class="pos-success-actions">
           <button class="btn btn-secondary" data-modal-close>Receipt</button>
           <button class="btn btn-primary" id="pos-success-new">New Sale</button>
         </div>`);
      // Wire up the New Sale button (closes modal — cart is already cleared below)
      $('#pos-success-new').onclick = () => { closeModal(); $('#pos-search')?.focus(); };
      // Also: clicking "Receipt" should open the print window then close
      const receiptBtn = document.querySelector('.pos-success-actions .btn-secondary');
      if (receiptBtn) {
        receiptBtn.onclick = () => {
          window.open('/api/sales/${r.id}/receipt', '_blank');
          closeModal();
        };
      }
      clearCart();
      $('#cust-name').value = ''; $('#cust-phone').value = '';
      customerId = null; customerLoyaltyPts = 0; customerCredit = 0; pendingQuoteId = null;
      updateCustomerInfo();
    } catch (e) {
      hideLoading();
      // Handle discount_pin_required (403) by asking for manager PIN and retrying
      if (e.status === 403 && e.detail && e.detail.code === 'discount_pin_required' && !payload.manager_pin) {
        const pin = await window.__askManagerPin?.({
          title: 'Discount Authorization Required',
          reason: `Discount ${e.detail.discount_pct}% exceeds max ${e.detail.max_allowed}% allowed without manager approval.`,
          detail: 'Enter a manager PIN to authorize this discount and complete the sale.',
          confirmLabel: 'Authorize & Complete Sale',
        });
        if (pin) {
          payload.manager_pin = pin;
          showLoading('Authorizing...');
          try {
            const r2 = await apiPost('/api/sales', payload);
            hideLoading();
            const itemCount2 = cart.reduce((s, i) => s + i.qty, 0);
            openModal('Sale Complete', `
              <div class="pos-success-modal">
                <div class="pos-success-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="20 6 9 17 4 12"/>
                  </svg>
                </div>
                <p class="pos-success-title">Sale Complete</p>
                <p class="pos-success-subtitle">${esc(r2.invoice_no)} · ${itemCount2} items</p>
                <p class="pos-success-total">${fmt(r2.total)}</p>
                <p class="pos-success-method">paid in ${esc(payMethod)}</p>
              </div>`,
              `<div class="pos-success-actions">
                 <button class="btn btn-secondary" data-modal-close>Close</button>
                 <button class="btn btn-primary" id="pos-success-new-2" onclick="window.open('/api/sales/${r2.id}/receipt','_blank')">Receipt</button>
               </div>`);
            $('#pos-success-new-2').onclick = () => closeModal();
            clearCart();
            $('#cust-name').value = ''; $('#cust-phone').value = '';
            customerId = null; customerLoyaltyPts = 0; customerCredit = 0; pendingQuoteId = null;
            updateCustomerInfo();
          } catch (e2) {
            hideLoading();
            toast('Sale failed after PIN: ' + e2.message, 'error');
          }
        }
        return;
      }
      toast('Sale failed: ' + e.message, 'error');
    }
  }

  // WhatsApp receipt helper
  window.sendReceipt = async (id) => {
    try {
      const r = await api(`/api/sales/${id}/whatsapp`);
      if (r.url) window.open(r.url, '_blank');
      else toast('No customer phone', 'info');
    } catch (e) { toast('Error', 'error'); }
  };

  // C9 fix (v8.13.4): expose addToCart + toggleCustomerDisplay to window so
  // kiosk-extras.js (showScanModal calls addToCart, toggleCustomerDisplay
  // flips isCustomerDisplay) can invoke them. Previously they referenced
  // closure-internal symbols and threw ReferenceError at runtime.
  // The functions are still owned by this closure, so state stays private.
  window.addToCart = addToCart;
  window._posToggleCustomerDisplay = () => {
    isCustomerDisplay = !isCustomerDisplay;
    if (isCustomerDisplay) {
      document.body.classList.add('pos-customer-mode');
      $$('.pos-staff-only').forEach(el => el.style.display = 'none');
      $$('.pos-cat-btn .pos-cat-price').forEach(el => el.style.display = 'none');
      $$('.pos-cat-btn .pos-stock-badge').forEach(el => el.style.display = 'none');
      const btn = $('#btn-display');
      if (btn) btn.classList.add('active');
      toast('Customer display mode on — staff info hidden', 'info');
    } else {
      document.body.classList.remove('pos-customer-mode');
      $$('.pos-staff-only').forEach(el => el.style.display = '');
      $$('.pos-cat-btn .pos-cat-price').forEach(el => el.style.display = '');
      $$('.pos-cat-btn .pos-stock-badge').forEach(el => el.style.display = '');
      const btn = $('#btn-display');
      if (btn) btn.classList.remove('active');
      toast('Customer display mode off', 'info');
    }
  };

  // ---------- keyboard shortcuts (POS-only) ----------
  const posKeyHandler = (e) => {
    const tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
      if (e.key.startsWith('F') && e.key.length > 1) {} else return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const fMatch = /^F([1-7])$/.exec(e.key);
    if (fMatch) {
      e.preventDefault();
      const idx = parseInt(fMatch[1]) - 1;
      const c = posCats[idx];
      const qtyMult = parseInt($('#pos-qty-pills .active')?.dataset.qty) || 1;
      if (c) addToCart(c.id, c.sell_price, c.code, c.name, c.color, qtyMult);
      return;
    }
    if (e.key === 'F8') { e.preventDefault(); showScanModal(); return; }
    if (e.key === 'F9') { e.preventDefault(); checkout(); return; }
    if (e.key === 'F10') { e.preventDefault(); holdCart(); return; }
    if (e.key === 'F11') { e.preventDefault(); if (cart.length && confirm('Clear cart?')) clearCart(); return; }
    if (e.key === 'F12') { e.preventDefault(); saveQuote(); return; }
    // v8.12.0: number keys 1-5 select QTY multiplier
    const numMatch = /^[1-5]$/.exec(e.key);
    if (numMatch && !e.shiftKey) {
      const num = parseInt(numMatch[0]);
      const pillMap = { 1: 1, 2: 2, 3: 3, 4: 5, 5: 10 };
      const targetQty = pillMap[num];
      const pill = $(`#pos-qty-pills .pos-qty-pill[data-qty="${targetQty}"]`);
      if (pill) {
        e.preventDefault();
        $$('#pos-qty-pills .pos-qty-pill').forEach(p => p.classList.remove('active'));
        pill.classList.add('active');
      }
    }
  };
  document.addEventListener('keydown', posKeyHandler);

  // ---------- quote conversion listener ----------
  const quoteHandler = (e) => {
    const q = e.detail;
    if (!q) return;
    cart = (q.items || []).map(i => ({ catId: i.catId, price: i.price, code: i.code, name: i.name, qty: i.qty }));
    discountVal = q.discount || 0;
    discountType = q.discount_type || 'amount';
    $('#cust-name').value = q.customer_name || '';
    $('#cust-phone').value = q.customer_phone || '';
    $('#sale-notes').value = q.notes || '';
    $('#discount-input').value = discountVal;
    $('#discount-type').value = discountType;
    renderCart();
    lookupCustomer();
    pendingQuoteId = q.id;
    toast(`Loaded quote ${q.quote_no} — checkout to convert`, 'info');
  };
  window.addEventListener('pos:load-quote', quoteHandler);

  // ---------- initial render ----------
  renderCart();
  updateCustomerInfo();
  loadHoldsPreview();

  // Cleanup when navigating away
  el._cleanup = () => {
    document.removeEventListener('keydown', posKeyHandler);
    window.removeEventListener('pos:load-quote', quoteHandler);
  };

  // ---------- bindings ----------
  bindCatButtons();

  $('#pos-search').oninput = debounce(() => refreshCatButtons(), 200);

  $('#btn-clear').onclick = () => { if (cart.length && !confirm('Clear cart?')) return; clearCart(); };
  $('#btn-hold').onclick = () => holdCart();
  $('#btn-quote').onclick = () => saveQuote();
  $('#btn-history').onclick = () => navigate('/pos/sales');
  $('#btn-quotes').onclick = () => navigate('/pos/quotes');
  $('#btn-holds').onclick = () => showHoldsModal();
  $('#btn-zreport').onclick = () => showZReport();
  $('#btn-cash').onclick = () => showCashActions();
  $('#btn-display').onclick = () => toggleCustomerDisplay();
  $('#btn-cust-search').onclick = () => showCustomerSearch();
  $('#btn-scan').onclick = () => showScanModal();

  $('#cust-phone').onblur = () => lookupCustomer();
  $('#cust-name').onblur = () => lookupCustomer();

  $$('.disc-q').forEach(btn => btn.onclick = () => {
    discountType = 'percent'; discountVal = parseFloat(btn.dataset.pct);
    $('#discount-input').value = discountVal; $('#discount-type').value = 'percent';
    updateTotals();
  });
  $('#discount-input').oninput = () => {
    discountVal = parseFloat($('#discount-input').value) || 0;
    discountType = $('#discount-type').value;
    updateTotals();
  };
  $('#discount-type').onchange = () => {
    discountType = $('#discount-type').value;
    updateTotals();
  };

  $('#btn-loyalty-apply').onclick = () => {
    const pts = parseInt($('#loyalty-input').value) || 0;
    if (pts <= 0) {
      loyaltyPointsToRedeem = 0;
      loyaltyDiscountVal = 0;
      $('#loyalty-applied').textContent = '';
      updateTotals();
      return;
    }
    if (pts > customerLoyaltyPts) {
      toast('Not enough points', 'error');
      return;
    }
    loyaltyPointsToRedeem = pts;
    loyaltyDiscountVal = pts * loyaltyRate;
    $('#loyalty-applied').textContent = `−Rs ${fmt(loyaltyDiscountVal)}`;
    updateTotals();
  };

  // Payment method change → show split inputs + cash calc
  $$('input[name="pay-method"]').forEach(r => r.onchange = () => {
    $$('.pos-pay-btn').forEach(b => b.classList.remove('active'));
    r.closest('.pos-pay-btn').classList.add('active');
    if (r.value === 'split') {
      $('#split-inputs').style.display = 'grid';
    } else {
      $('#split-inputs').style.display = 'none';
    }
    // v8.4: Show cash calculator only when Cash is selected
    const cashSection = $('#cash-calc-section');
    if (cashSection) {
      cashSection.style.display = (r.value === 'cash') ? '' : 'none';
    }
    updateTotals();
    updateCashCalc();
  });

  // v8.4: Cash received + change calculator
  function updateCashCalc() {
    const cashInput = $('#cash-received');
    const changeEl = $('#change-amount');
    const changeRow = $('#change-row');
    if (!cashInput || !changeEl) return;
    const received = parseFloat(cashInput.value) || 0;
    const totalEl = $('#cart-total');
    const totalStr = totalEl ? totalEl.textContent.replace(/[^\d.]/g, '') : '0';
    const total = parseFloat(totalStr) || 0;
    const change = received - total;
    if (received > 0) {
      changeRow.style.display = '';
      if (change >= 0) {
        changeEl.textContent = 'Rs ' + change.toFixed(0);
        changeEl.style.color = 'var(--success-text, #3d8a52)';
      } else {
        changeEl.textContent = 'Rs ' + Math.abs(change).toFixed(0) + ' short';
        changeEl.style.color = 'var(--danger-text, #a03535)';
      }
    } else {
      changeRow.style.display = 'none';
    }
  }
  const cashReceivedInput = $('#cash-received');
  if (cashReceivedInput) cashReceivedInput.oninput = updateCashCalc;
  const cashExactBtn = $('#btn-cash-exact');
  if (cashExactBtn) cashExactBtn.onclick = () => {
    const totalEl = $('#cart-total');
    const totalStr = totalEl ? totalEl.textContent.replace(/[^\d.]/g, '') : '0';
    cashReceivedInput.value = parseFloat(totalStr) || 0;
    updateCashCalc();
  };
  $$('.cash-quick').forEach(btn => {
    btn.onclick = () => {
      const amt = parseFloat(btn.dataset.amt) || 0;
      const current = parseFloat(cashReceivedInput.value) || 0;
      cashReceivedInput.value = (current + amt).toFixed(0);
      updateCashCalc();
    };
  });

  $('#split-cash').oninput = updateTotals;
  $('#split-card').oninput = updateTotals;
  $('#split-online').oninput = updateTotals;
  $('#split-fill-cash').onclick = () => {
    // H15 fix (v8.13.4): compute the subtotal first, THEN derive the
    // discounted total. The previous code referenced `total` inside its
    // own initializer (`const total = ... total * discountVal/100 ...`),
    // which is a TDZ (temporal dead zone) self-reference and throws
    // ReferenceError at runtime — the "All Cash" split-tender button was
    // completely broken.
    const subtotal = cart.reduce((s, i) => s + i.price * i.qty, 0);
    const disc = discountType === 'percent' ? subtotal * discountVal / 100 : discountVal;
    const total = Math.max(0, subtotal - disc - loyaltyDiscountVal);
    $('#split-cash').value = total.toFixed(0);
    $('#split-card').value = 0;
    $('#split-online').value = 0;
    updateTotals();
  };
  $('#split-half').onclick = () => {
    const subtotal = cart.reduce((s, i) => s + i.price * i.qty, 0);
    let disc = discountType === 'percent' ? subtotal * discountVal/100 : discountVal;
    const total = Math.max(0, subtotal - disc - loyaltyDiscountVal);
    const half = Math.floor(total / 2);
    $('#split-cash').value = half;
    $('#split-card').value = (total - half).toFixed(0);
    $('#split-online').value = 0;
    updateTotals();
  };

  $('#checkout-btn').onclick = () => checkout();

  // ---------- customer lookup ----------

// Customer bar, holds/quotes, and kiosk extras extracted to components/
// See: customer-bar.js, holds-quotes.js, kiosk-extras.js

// Checkout extracted to checkout-modal.js
});

// ==================================================================
// /pos/sales — sales history
// ==================================================================
