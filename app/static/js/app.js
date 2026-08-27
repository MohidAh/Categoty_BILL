// BillBook SPA entry point
import { render } from './router.js';
import { initTheme, initAppearance, $ } from './utils.js';
import { initShortcutsOverlay } from './components/shortcuts.js';
import { renderLauncher } from './core/launcher.js';
import { renderCommandPalette } from './core/shell.js';

// Import all route registrations (side effects)
import './pages/dashboard.js';
// v3.1: Core utilities
import './core/states.js';
import './core/i18n.js';
import './pages/bills-list.js';
import './pages/bill-new.js';
import './pages/bill-edit.js';
import './apps/pos/components/bill-edit-extras.js';
import './pages/billing-pages.js';
import './pages/suppliers-list.js';
import './pages/supplier-detail.js';
import './pages/reports-pages.js';
import './pages/reports-financial.js';
import './pages/expenses-page.js';
import './pages/actual-earnings-page.js';
import './pages/cash-controls-pages.js';
import './pages/margins-page.js';
import './pages/monthly-profit-page.js';
import './pages/ytd-profit-page.js';
import './pages/daily-stock-page.js';
import './pages/cash-buckets-page.js';
import './pages/store-profit-dashboard.js';
import './pages/help-page.js';
import { initFloatingHelp } from './pages/help-page.js';
import './pages/approval-queue-page.js';
import './pages/agent-chat-page.js';
import './pages/ai-usage-page.js';
import './pages/ai-automations-page.js';
import './pages/branch-page.js';
import './pages/hq-branches-page.js';
import './pages/owner-hub-page.js';
import './pages/transfers-page.js';
import './pages/central-purchases-page.js';
import './pages/price-push-page.js';
import './pages/audit-report-page.js';
import './pages/pos-import-sync-page.js';
import './pages/insights-pages.js';
import './pages/settings-pages.js';
import './pages/settings-staff.js';
import './pages/items-search.js';
import './pages/pos.js';
import './pages/pos-pages.js';
import './pages/inventory-pages.js';
import './pages/customers-pages.js';
import './apps/pos/components/customer-detail.js';
import './apps/pos/components/dead-stock.js';
import './pages/more.js';

// Register launcher route
import { route } from './router.js';
route('/launcher', async (el) => {
  const role = localStorage.getItem('bb-role') || 'manager';
  const name = localStorage.getItem('bb-user-name') || 'Manager';
  renderLauncher(el, role, name);
});

// Init theme (instant, from cache) then sync full appearance from the server
// (v8.15.0: accent/density/serif/radius/font-scale now actually apply — and
// follow the account across devices)
initTheme();
initAppearance();
initShortcutsOverlay();

// Command palette (Ctrl+K)
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    // Don't open in kiosk mode
    if (document.body.classList.contains('kiosk-mode')) return;
    e.preventDefault();
    renderCommandPalette();
  }
});

window.addEventListener('hashchange', render);

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', render);
} else {
  render();
}

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  const tag = e.target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (/^F\d{1,2}$/.test(e.key)) return;
  if (document.body.classList.contains('kiosk-mode')) return;

  const map = {
    'd': '/', 'p': '/pos', 'b': '/bills', 'f': '/items', 's': '/suppliers',
    'r': '/reports', 'i': '/insights', 'm': '/more', ',': '/settings', 'n': '/bills/new',
    'h': '/launcher',
  };
  const path = map[e.key.toLowerCase()];
  if (path) {
    e.preventDefault();
    window.location.hash = '#' + path;
  }
});

// Initialize floating help button on every page
initFloatingHelp();

// Refresh pending-actions badge on shell mount + every 60s
import { refreshPendingCount } from './pages/approval-queue-page.js';
function _refreshBadgeSoon() {
  // Wait for the shell nav to be in the DOM
  setTimeout(() => { refreshPendingCount(); }, 50);
}
window.addEventListener('hashchange', _refreshBadgeSoon);
_refreshBadgeSoon();
setInterval(refreshPendingCount, 60000);

// ─── v8.1 Phase 6: Daily-Use Friction Fixes ────────────────────────────────

// 1. Honor start_page setting — redirect to chosen page on first load
import { api } from './api.js';
(async () => {
  try {
    const state = await api('/api/setup/state');
    if (state.setup_completed && state.start_page && state.start_page !== 'launcher') {
      // Only redirect if we're at the root (first load, no hash)
      if (!window.location.hash || window.location.hash === '#/' || window.location.hash === '') {
        const target = state.start_page === 'pos' ? '/pos' :
                       state.start_page === 'dashboard' ? '/reports/store-profit' : '/launcher';
        if (target !== '/launcher') {
          window.location.hash = '#' + target;
        }
      }
    }
  } catch {}
})();

// 2. Global drag-drop bill upload — drop a PDF/image anywhere → /bills/new
let _dragOverlay = null;
let _dragCounter = 0;
document.addEventListener('dragenter', (e) => {
  if (document.body.classList.contains('kiosk-mode')) return;
  _dragCounter++;
  if (!_dragOverlay) {
    _dragOverlay = document.createElement('div');
    _dragOverlay.id = 'bb-drag-overlay';
    _dragOverlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(204,120,92,0.15);z-index:99998;display:flex;align-items:center;justify-content:center;pointer-events:none;backdrop-filter:blur(2px)';
    _dragOverlay.innerHTML = '<div style="background:var(--surface-card,#efe9de);padding:32px 48px;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,0.2);font-size:18px;font-weight:600;color:var(--coral,#cc785c)">Drop to upload bill</div>';
    document.body.appendChild(_dragOverlay);
  }
  _dragOverlay.style.display = 'flex';
});
document.addEventListener('dragleave', (e) => {
  _dragCounter--;
  if (_dragCounter <= 0 && _dragOverlay) {
    _dragOverlay.style.display = 'none';
    _dragCounter = 0;
  }
});
document.addEventListener('dragover', (e) => { e.preventDefault(); });
document.addEventListener('drop', async (e) => {
  e.preventDefault();
  e.stopPropagation();
  _dragCounter = 0;
  if (_dragOverlay) _dragOverlay.style.display = 'none';
  if (document.body.classList.contains('kiosk-mode')) return;
  const files = e.dataTransfer.files;
  if (files.length === 0) return;
  // Navigate to /bills/new and store the file for the page to pick up
  window.location.hash = '#/bills/new';
  // Store the dropped file globally so bill-new.js can pick it up
  window._bb_dropped_file = files[0];
});

// 3. Today's Profit ticker — persistent chip in the shell topbar
let _profitTicker = null;
let _profitValue = 0;
async function _refreshProfitTicker() {
  try {
    const r = await api('/api/profit/dashboard');
    const today = r.daily_summary || {};
    _profitValue = today.gross_profit || 0;
    _updateTickerText();
  } catch {}
}
function _updateTickerText() {
  if (_profitTicker) {
    _profitTicker.textContent = 'Today: Rs ' + Math.round(_profitValue).toLocaleString();
  }
}
function _mountProfitTicker() {
  // If ticker already exists in the DOM, just refresh its value
  if (_profitTicker && document.body.contains(_profitTicker)) {
    _updateTickerText();
    return;
  }
  // Find the shell topbar-right (exists in ALL shell variants — pos, billing,
  // inventory, reports, insights, settings all use the same shell-topbar)
  const topbar = document.querySelector('.shell-topbar-right');
  if (topbar) {
    _profitTicker = document.createElement('div');
    _profitTicker.id = 'bb-profit-ticker';
    _profitTicker.style.cssText = 'padding:5px 12px;background:var(--success-soft,#f0fdf4);color:var(--success-text,#15803d);border:1px solid var(--success,#16a34a);border-radius:8px;font-size:13px;font-weight:700;cursor:pointer;white-space:nowrap;margin-left:8px;line-height:1.4';
    _profitTicker.title = 'Click to view Store Profit Dashboard';
    _profitTicker.onclick = () => { window.location.hash = '#/reports/store-profit'; };
    topbar.appendChild(_profitTicker);
    _updateTickerText();
  }
}
// Mount ticker after each navigation (the shell is re-rendered on hashchange)
window.addEventListener('hashchange', () => {
  // Wait for the new shell to render, then mount the ticker
  setTimeout(() => {
    _mountProfitTicker();
    _refreshProfitTicker();
  }, 800);
});
// Also try mounting on initial load
setTimeout(() => { _mountProfitTicker(); _refreshProfitTicker(); }, 1500);

// 4. Quick Expense FAB — floating "+" button → 2-field modal
import { esc, toast, openModal, closeModal } from './utils.js';
import { api as _api, apiPost as _apiPost } from './api.js';
let _expenseFab = null;
function _initExpenseFab() {
  if (_expenseFab) return;
  _expenseFab = document.createElement('button');
  _expenseFab.id = 'bb-expense-fab';
  _expenseFab.style.cssText = 'position:fixed;bottom:80px;right:20px;z-index:9997;width:48px;height:48px;border-radius:50%;background:var(--danger,#dc2626);color:white;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 12px rgba(220,38,38,0.4);transition:transform .2s,opacity .2s';
  _expenseFab.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:22px;height:22px"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
  _expenseFab.title = 'Quick Expense';
  _expenseFab.onclick = _openQuickExpenseModal;
  document.body.appendChild(_expenseFab);
}
function _openQuickExpenseModal() {
  _api('/api/expense-categories').then(r => {
    const cats = r.categories || r || [];
    openModal('Quick Expense', `
      <div class="form-group">
        <label class="text-sm"><strong>Amount (Rs)</strong></label>
        <input class="input" id="qe-amount" type="number" min="1" step="0.01" placeholder="0" autofocus style="margin-top:4px">
      </div>
      <div class="form-group" style="margin-bottom:0">
        <label class="text-sm"><strong>Category</strong></label>
        <select class="input" id="qe-category" style="margin-top:4px">
          ${cats.map(c => `<option value="${c.id || c.name}">${esc(c.name)}</option>`).join('')}
        </select>
      </div>`,
      `<button class="btn" data-close>Cancel</button>
       <button class="btn btn-primary" id="qe-save">Save Expense</button>`);
    $('#qe-amount').focus();
    $('#qe-amount').onkeydown = (e) => { if (e.key === 'Enter') $('#qe-save').click(); };
    $('#qe-save').onclick = async () => {
      const amount = parseFloat($('#qe-amount').value);
      const catId = $('#qe-category').value;
      if (!amount || amount <= 0) { toast('Enter a valid amount', 'error'); return; }
      try {
        await _apiPost('/api/expenses', { amount, category_id: parseInt(catId), description: 'Quick expense', payment_method: 'cash' });
        toast('Expense saved', 'success');
        closeModal();
      } catch (e) { toast('Save failed: ' + e.message, 'error'); }
    };
  }).catch(() => {
    toast('Could not load expense categories', 'error');
  });
}
// Initialize FAB after a short delay (so it doesn't interfere with page load)
setTimeout(_initExpenseFab, 2000);
